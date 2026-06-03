from typing import cast

from chonkie import SentenceChunker

from docubrain.configs.app_configs import AVERAGE_SUMMARY_EMBEDDINGS
from docubrain.configs.app_configs import BLURB_SIZE
from docubrain.configs.app_configs import CHUNK_OVERLAP_PERCENT
from docubrain.configs.app_configs import LARGE_CHUNK_RATIO
from docubrain.configs.app_configs import MINI_CHUNK_SIZE
from docubrain.configs.app_configs import SKIP_METADATA_IN_CHUNK
from docubrain.configs.app_configs import USE_CHUNK_SUMMARY
from docubrain.configs.app_configs import USE_DOCUMENT_SUMMARY
from docubrain.configs.constants import DocumentSource
from docubrain.configs.constants import RETURN_SEPARATOR
from docubrain.configs.constants import SECTION_SEPARATOR
from docubrain.connectors.cross_connector_utils.miscellaneous_utils import (
    get_metadata_keys_to_ignore,
)
from docubrain.connectors.models import IndexingDocument
from docubrain.connectors.models import Section
from docubrain.indexing.indexing_heartbeat import IndexingHeartbeatInterface
from docubrain.indexing.models import DocAwareChunk
from docubrain.llm.utils import MAX_CONTEXT_TOKENS
from docubrain.natural_language_processing.utils import BaseTokenizer
from docubrain.utils.logger import setup_logger
from docubrain.utils.text_processing import clean_text
from docubrain.utils.text_processing import shared_precompare_cleanup
from shared_configs.configs import DOC_EMBEDDING_CONTEXT_SIZE
from shared_configs.configs import STRICT_CHUNK_TOKEN_LIMIT

# Not supporting overlaps, we need a clean combination of chunks and it is unclear if overlaps
# actually help quality at all
CHUNK_OVERLAP = 0
# Fairly arbitrary numbers but the general concept is we don't want the title/metadata to
# overwhelm the actual contents of the chunk
MAX_METADATA_PERCENTAGE = 0.25
CHUNK_MIN_CONTENT = 256

logger = setup_logger()


def _get_metadata_suffix_for_document_index(
    metadata: dict[str, str | list[str]], include_separator: bool = False
) -> tuple[str, str]:
    """
    Returns the metadata as a natural language string representation with all of the keys and values
    for the vector embedding and a string of all of the values for the keyword search.
    """
    if not metadata:
        return "", ""

    metadata_str = "Metadata:\n"
    metadata_values = []
    for key, value in metadata.items():
        if key in get_metadata_keys_to_ignore():
            continue

        value_str = ", ".join(value) if isinstance(value, list) else value

        if isinstance(value, list):
            metadata_values.extend(value)
        else:
            metadata_values.append(value)

        metadata_str += f"\t{key} - {value_str}\n"

    metadata_semantic = metadata_str.strip()
    metadata_keyword = " ".join(metadata_values)

    if include_separator:
        return RETURN_SEPARATOR + metadata_semantic, RETURN_SEPARATOR + metadata_keyword
    return metadata_semantic, metadata_keyword


def _combine_chunks(chunks: list[DocAwareChunk], large_chunk_id: int) -> DocAwareChunk:
    """
    Combines multiple DocAwareChunks into one large chunk (for "multipass" mode),
    appending the content and adjusting source_links accordingly.
    """
    merged_chunk = DocAwareChunk(
        source_document=chunks[0].source_document,
        chunk_id=chunks[0].chunk_id,
        blurb=chunks[0].blurb,
        content=chunks[0].content,
        source_links=chunks[0].source_links or {},
        image_file_id=None,
        section_continuation=(chunks[0].chunk_id > 0),
        title_prefix=chunks[0].title_prefix,
        metadata_suffix_semantic=chunks[0].metadata_suffix_semantic,
        metadata_suffix_keyword=chunks[0].metadata_suffix_keyword,
        large_chunk_reference_ids=[chunk.chunk_id for chunk in chunks],
        mini_chunk_texts=None,
        large_chunk_id=large_chunk_id,
        chunk_context="",
        doc_summary="",
        contextual_rag_reserved_tokens=0,
    )

    offset = 0
    for i in range(1, len(chunks)):
        merged_chunk.content += SECTION_SEPARATOR + chunks[i].content

        offset += len(SECTION_SEPARATOR) + len(chunks[i - 1].content)
        for link_offset, link_text in (chunks[i].source_links or {}).items():
            if merged_chunk.source_links is None:
                merged_chunk.source_links = {}
            merged_chunk.source_links[link_offset + offset] = link_text

    return merged_chunk


def generate_large_chunks(chunks: list[DocAwareChunk]) -> list[DocAwareChunk]:
    """
    Generates larger "grouped" chunks by combining sets of smaller chunks.
    """
    large_chunks = []
    for idx, i in enumerate(range(0, len(chunks), LARGE_CHUNK_RATIO)):
        chunk_group = chunks[i : i + LARGE_CHUNK_RATIO]
        if len(chunk_group) > 1:
            large_chunk = _combine_chunks(chunk_group, idx)
            large_chunks.append(large_chunk)
    return large_chunks


class Chunker:
    """
    Chunks documents into smaller chunks for indexing.
    """

    def __init__(
        self,
        tokenizer: BaseTokenizer,
        enable_multipass: bool = False,
        enable_large_chunks: bool = False,
        enable_contextual_rag: bool = False,
        blurb_size: int = BLURB_SIZE,
        include_metadata: bool = not SKIP_METADATA_IN_CHUNK,
        chunk_token_limit: int = DOC_EMBEDDING_CONTEXT_SIZE,
        chunk_overlap: int = CHUNK_OVERLAP,
        mini_chunk_size: int = MINI_CHUNK_SIZE,
        callback: IndexingHeartbeatInterface | None = None,
    ) -> None:
        self.include_metadata = include_metadata
        self.chunk_token_limit = chunk_token_limit
        self.enable_multipass = enable_multipass
        self.enable_large_chunks = enable_large_chunks
        self.enable_contextual_rag = enable_contextual_rag
        if enable_contextual_rag:
            assert (
                USE_CHUNK_SUMMARY or USE_DOCUMENT_SUMMARY
            ), "Contextual RAG requires at least one of chunk summary and document summary enabled"
        self.default_contextual_rag_reserved_tokens = MAX_CONTEXT_TOKENS * (
            int(USE_CHUNK_SUMMARY) + int(USE_DOCUMENT_SUMMARY)
        )
        self.tokenizer = tokenizer
        self.callback = callback

        self.max_context = 0
        self.prompt_tokens = 0

        # Create a token counter function that returns the count instead of the tokens
        def token_counter(text: str) -> int:
            return len(tokenizer.encode(text))

        # Audit fix #7: derive a small token overlap from CHUNK_OVERLAP_PERCENT so a
        # fact spanning a chunk boundary survives intact in at least one chunk.
        # An explicit non-zero chunk_overlap arg always wins; otherwise fall back to
        # the configured percentage of the chunk token limit (0 disables overlap).
        effective_chunk_overlap = chunk_overlap or int(
            chunk_token_limit * CHUNK_OVERLAP_PERCENT / 100
        )
        self.chunk_overlap = effective_chunk_overlap

        self.blurb_splitter = SentenceChunker(
            tokenizer_or_token_counter=token_counter,
            chunk_size=blurb_size,
            chunk_overlap=0,
            return_type="texts",
        )

        self.chunk_splitter = SentenceChunker(
            tokenizer_or_token_counter=token_counter,
            chunk_size=chunk_token_limit,
            chunk_overlap=effective_chunk_overlap,
            return_type="texts",
        )

        self.mini_chunk_splitter = (
            SentenceChunker(
                tokenizer_or_token_counter=token_counter,
                chunk_size=mini_chunk_size,
                chunk_overlap=0,
                return_type="texts",
            )
            if enable_multipass
            else None
        )

    @staticmethod
    def _is_markdown_table(text: str) -> bool:
        """Detect if text is a markdown table (has pipe-delimited rows)."""
        lines = text.strip().split("\n")
        if len(lines) < 2:
            return False
        pipe_lines = sum(1 for line in lines if line.strip().startswith("|") and line.strip().endswith("|"))
        return pipe_lines >= 2 and pipe_lines / len(lines) > 0.5

    def _split_markdown_table(self, text: str, content_token_limit: int) -> list[str]:
        """Split a markdown table on row boundaries, repeating the header per chunk."""
        lines = text.strip().split("\n")
        header_lines: list[str] = []
        data_rows: list[str] = []

        for line in lines:
            stripped = line.strip()
            if not data_rows and (
                stripped.startswith("|---") or stripped.startswith("| ---")
                or (header_lines and all(c in "-| " for c in stripped))
            ):
                header_lines.append(line)
            elif not data_rows and not header_lines:
                header_lines.append(line)
            elif not data_rows and header_lines and stripped.startswith("|"):
                # Could be the separator row right after header
                if all(c in "-| :" for c in stripped.replace("|", "").strip()):
                    header_lines.append(line)
                else:
                    data_rows.append(line)
            else:
                data_rows.append(line)

        if not data_rows:
            return [text]

        header_text = "\n".join(header_lines)
        header_tokens = len(self.tokenizer.encode(header_text)) if header_lines else 0
        available = content_token_limit - header_tokens - 5

        chunks: list[str] = []
        current_rows: list[str] = []
        current_tokens = 0

        for row in data_rows:
            row_tokens = len(self.tokenizer.encode(row))
            if current_tokens + row_tokens > available and current_rows:
                chunk = header_text + "\n" + "\n".join(current_rows) if header_text else "\n".join(current_rows)
                chunks.append(chunk)
                current_rows = []
                current_tokens = 0
            current_rows.append(row)
            current_tokens += row_tokens

        if current_rows:
            chunk = header_text + "\n" + "\n".join(current_rows) if header_text else "\n".join(current_rows)
            chunks.append(chunk)

        return chunks or [text]

    def _split_oversized_chunk(self, text: str, content_token_limit: int) -> list[str]:
        """
        Splits the text into smaller chunks based on token count to ensure
        no chunk exceeds the content_token_limit.
        """
        tokens = self.tokenizer.tokenize(text)
        chunks = []
        start = 0
        total_tokens = len(tokens)
        while start < total_tokens:
            end = min(start + content_token_limit, total_tokens)
            token_chunk = tokens[start:end]
            chunk_text = " ".join(token_chunk)
            chunks.append(chunk_text)
            start = end
        return chunks

    def _extract_blurb(self, text: str) -> str:
        """
        Extract a short blurb from the text (first chunk of size `blurb_size`).
        """
        # chunker is in `text` mode
        texts = cast(list[str], self.blurb_splitter.chunk(text))
        if not texts:
            return ""
        return texts[0]

    def _get_mini_chunk_texts(self, chunk_text: str) -> list[str] | None:
        """
        For "multipass" mode: additional sub-chunks (mini-chunks) for use in certain embeddings.
        """
        if self.mini_chunk_splitter and chunk_text.strip():
            # chunker is in `text` mode
            return cast(list[str], self.mini_chunk_splitter.chunk(chunk_text))
        return None

    # ADDED: extra param image_url to store in the chunk
    def _create_chunk(
        self,
        document: IndexingDocument,
        chunks_list: list[DocAwareChunk],
        text: str,
        links: dict[int, str],
        is_continuation: bool = False,
        title_prefix: str = "",
        metadata_suffix_semantic: str = "",
        metadata_suffix_keyword: str = "",
        image_file_id: str | None = None,
    ) -> None:
        """
        Helper to create a new DocAwareChunk, append it to chunks_list.
        """
        new_chunk = DocAwareChunk(
            source_document=document,
            chunk_id=len(chunks_list),
            blurb=self._extract_blurb(text),
            content=text,
            source_links=links or {0: ""},
            image_file_id=image_file_id,
            section_continuation=is_continuation,
            title_prefix=title_prefix,
            metadata_suffix_semantic=metadata_suffix_semantic,
            metadata_suffix_keyword=metadata_suffix_keyword,
            mini_chunk_texts=self._get_mini_chunk_texts(text),
            large_chunk_id=None,
            doc_summary="",
            chunk_context="",
            contextual_rag_reserved_tokens=0,  # set per-document in _handle_single_document
        )
        chunks_list.append(new_chunk)

    def _chunk_document_with_sections(
        self,
        document: IndexingDocument,
        sections: list[Section],
        title_prefix: str,
        metadata_suffix_semantic: str,
        metadata_suffix_keyword: str,
        content_token_limit: int,
    ) -> list[DocAwareChunk]:
        """
        Loops through sections of the document, converting them into one or more chunks.
        Works with processed sections that are base Section objects.
        """
        chunks: list[DocAwareChunk] = []
        link_offsets: dict[int, str] = {}
        chunk_text = ""

        for section_idx, section in enumerate(sections):
            # Get section text and other attributes
            section_text = clean_text(str(section.text or ""))
            section_link_text = section.link or ""
            image_url = section.image_file_id

            # If there is no useful content, skip
            if not section_text and (not document.title or section_idx > 0):
                logger.warning(
                    f"Skipping empty or irrelevant section in doc {document.semantic_identifier}, link={section_link_text}"
                )
                continue

            # CASE 1: If this section has an image, force a separate chunk
            if image_url:
                # First, if we have any partially built text chunk, finalize it
                if chunk_text.strip():
                    self._create_chunk(
                        document,
                        chunks,
                        chunk_text,
                        link_offsets,
                        is_continuation=False,
                        title_prefix=title_prefix,
                        metadata_suffix_semantic=metadata_suffix_semantic,
                        metadata_suffix_keyword=metadata_suffix_keyword,
                    )
                    chunk_text = ""
                    link_offsets = {}

                # Create a chunk specifically for this image section
                # (Using the text summary that was generated during processing)
                self._create_chunk(
                    document,
                    chunks,
                    section_text,
                    links={0: section_link_text} if section_link_text else {},
                    image_file_id=image_url,
                    title_prefix=title_prefix,
                    metadata_suffix_semantic=metadata_suffix_semantic,
                    metadata_suffix_keyword=metadata_suffix_keyword,
                )
                # Continue to next section
                continue

            # CASE 2: Normal text section
            section_token_count = len(self.tokenizer.encode(section_text))

            # CASE 2a (audit fix #7): Markdown table — never merge a table into a
            # mixed prose chunk and never cut it mid-row. Finalize any pending
            # chunk, then emit the table split on row boundaries with the header
            # repeated per chunk (preserves row/column/header semantics), even
            # when the table is small enough to "fit".
            if self._is_markdown_table(section_text):
                if chunk_text.strip():
                    self._create_chunk(
                        document,
                        chunks,
                        chunk_text,
                        link_offsets,
                        False,
                        title_prefix,
                        metadata_suffix_semantic,
                        metadata_suffix_keyword,
                    )
                    chunk_text = ""
                    link_offsets = {}

                table_splits = self._split_markdown_table(
                    section_text, content_token_limit
                )
                for i, split_text in enumerate(table_splits):
                    self._create_chunk(
                        document,
                        chunks,
                        split_text,
                        {0: section_link_text},
                        is_continuation=(i != 0),
                        title_prefix=title_prefix,
                        metadata_suffix_semantic=metadata_suffix_semantic,
                        metadata_suffix_keyword=metadata_suffix_keyword,
                    )
                continue

            # If the section is large on its own, split it separately
            if section_token_count > content_token_limit:
                if chunk_text.strip():
                    self._create_chunk(
                        document,
                        chunks,
                        chunk_text,
                        link_offsets,
                        False,
                        title_prefix,
                        metadata_suffix_semantic,
                        metadata_suffix_keyword,
                    )
                    chunk_text = ""
                    link_offsets = {}

                # For markdown tables, split on row boundaries to preserve header context
                if self._is_markdown_table(section_text):
                    split_texts = self._split_markdown_table(section_text, content_token_limit)
                else:
                    # chunker is in `text` mode
                    split_texts = cast(list[str], self.chunk_splitter.chunk(section_text))
                for i, split_text in enumerate(split_texts):
                    # If even the split_text is bigger than strict limit, further split
                    if (
                        STRICT_CHUNK_TOKEN_LIMIT
                        and len(self.tokenizer.encode(split_text)) > content_token_limit
                    ):
                        smaller_chunks = self._split_oversized_chunk(
                            split_text, content_token_limit
                        )
                        for j, small_chunk in enumerate(smaller_chunks):
                            self._create_chunk(
                                document,
                                chunks,
                                small_chunk,
                                {0: section_link_text},
                                is_continuation=(j != 0),
                                title_prefix=title_prefix,
                                metadata_suffix_semantic=metadata_suffix_semantic,
                                metadata_suffix_keyword=metadata_suffix_keyword,
                            )
                    else:
                        self._create_chunk(
                            document,
                            chunks,
                            split_text,
                            {0: section_link_text},
                            is_continuation=(i != 0),
                            title_prefix=title_prefix,
                            metadata_suffix_semantic=metadata_suffix_semantic,
                            metadata_suffix_keyword=metadata_suffix_keyword,
                        )
                continue

            # If we can still fit this section into the current chunk, do so
            current_token_count = len(self.tokenizer.encode(chunk_text))
            current_offset = len(shared_precompare_cleanup(chunk_text))
            next_section_tokens = (
                len(self.tokenizer.encode(SECTION_SEPARATOR)) + section_token_count
            )

            if next_section_tokens + current_token_count <= content_token_limit:
                if chunk_text:
                    chunk_text += SECTION_SEPARATOR
                chunk_text += section_text
                link_offsets[current_offset] = section_link_text
            else:
                # finalize the existing chunk
                self._create_chunk(
                    document,
                    chunks,
                    chunk_text,
                    link_offsets,
                    False,
                    title_prefix,
                    metadata_suffix_semantic,
                    metadata_suffix_keyword,
                )
                # start a new chunk
                link_offsets = {0: section_link_text}
                chunk_text = section_text

        # finalize any leftover text chunk
        if chunk_text.strip() or not chunks:
            self._create_chunk(
                document,
                chunks,
                chunk_text,
                link_offsets or {0: ""},  # safe default
                False,
                title_prefix,
                metadata_suffix_semantic,
                metadata_suffix_keyword,
            )
        return chunks

    def _handle_single_document(
        self, document: IndexingDocument
    ) -> list[DocAwareChunk]:
        # Specifically for reproducing an issue with gmail
        if document.source == DocumentSource.GMAIL:
            logger.debug(f"Chunking {document.semantic_identifier}")

        # Title prep
        title = self._extract_blurb(document.get_title_for_document_index() or "")
        title_prefix = title + RETURN_SEPARATOR if title else ""
        title_tokens = len(self.tokenizer.encode(title_prefix))

        # Metadata prep
        metadata_suffix_semantic = ""
        metadata_suffix_keyword = ""
        metadata_tokens = 0
        if self.include_metadata:
            (
                metadata_suffix_semantic,
                metadata_suffix_keyword,
            ) = _get_metadata_suffix_for_document_index(
                document.metadata, include_separator=True
            )
            metadata_tokens = len(self.tokenizer.encode(metadata_suffix_semantic))

        # If metadata is too large, skip it in the semantic content
        if metadata_tokens >= self.chunk_token_limit * MAX_METADATA_PERCENTAGE:
            metadata_suffix_semantic = ""
            metadata_tokens = 0

        single_chunk_fits = True
        doc_token_count = 0
        if self.enable_contextual_rag:
            doc_content = document.get_text_content()
            tokenized_doc = self.tokenizer.tokenize(doc_content)
            doc_token_count = len(tokenized_doc)

            # check if doc + title + metadata fits in a single chunk. If so, no need for contextual RAG
            single_chunk_fits = (
                doc_token_count + title_tokens + metadata_tokens
                <= self.chunk_token_limit
            )

        # expand the size of the context used for contextual rag based on whether chunk context and doc summary are used
        context_size = 0
        if (
            self.enable_contextual_rag
            and not single_chunk_fits
            and not AVERAGE_SUMMARY_EMBEDDINGS
        ):
            context_size += self.default_contextual_rag_reserved_tokens

        # Adjust content token limit to accommodate title + metadata
        content_token_limit = (
            self.chunk_token_limit - title_tokens - metadata_tokens - context_size
        )

        # first check: if there is not enough actual chunk content when including contextual rag,
        # then don't do contextual rag
        if content_token_limit <= CHUNK_MIN_CONTENT:
            context_size = 0  # Don't do contextual RAG
            # revert to previous content token limit
            content_token_limit = (
                self.chunk_token_limit - title_tokens - metadata_tokens
            )

        # If there is not enough context remaining then just index the chunk with no prefix/suffix
        if content_token_limit <= CHUNK_MIN_CONTENT:
            # Not enough space left, so revert to full chunk without the prefix
            content_token_limit = self.chunk_token_limit
            title_prefix = ""
            metadata_suffix_semantic = ""

        # Use processed_sections if available (IndexingDocument), otherwise use original sections
        sections_to_chunk = document.processed_sections

        normal_chunks = self._chunk_document_with_sections(
            document,
            sections_to_chunk,
            title_prefix,
            metadata_suffix_semantic,
            metadata_suffix_keyword,
            content_token_limit,
        )

        # Optional "multipass" large chunk creation
        if self.enable_multipass and self.enable_large_chunks:
            large_chunks = generate_large_chunks(normal_chunks)
            normal_chunks.extend(large_chunks)

        for chunk in normal_chunks:
            chunk.contextual_rag_reserved_tokens = context_size

        return normal_chunks

    def chunk(self, documents: list[IndexingDocument]) -> list[DocAwareChunk]:
        """
        Takes in a list of documents and chunks them into smaller chunks for indexing
        while persisting the document metadata.

        Works with both standard Document objects and IndexingDocument objects with processed_sections.
        """
        final_chunks: list[DocAwareChunk] = []
        for document in documents:
            if self.callback and self.callback.should_stop():
                raise RuntimeError("Chunker.chunk: Stop signal detected")

            chunks = self._handle_single_document(document)
            final_chunks.extend(chunks)

            if self.callback:
                self.callback.progress("Chunker.chunk", len(chunks))

        return final_chunks
