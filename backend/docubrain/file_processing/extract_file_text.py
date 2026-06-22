import csv
import gc
import io
import json
import os
import re
import zipfile
from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Sequence
from email.parser import Parser as EmailParser
from io import BytesIO
from pathlib import Path
from typing import Any
from typing import IO
from typing import NamedTuple
from typing import Optional
from typing import TYPE_CHECKING
from zipfile import BadZipFile

import chardet
import openpyxl
from openpyxl.worksheet.worksheet import Worksheet
from PIL import Image

from docubrain.configs.constants import DOCUBRAIN_METADATA_FILENAME
from docubrain.configs.llm_configs import get_image_extraction_and_analysis_enabled
from docubrain.file_processing.file_types import DocubrainFileExtensions
from docubrain.file_processing.file_types import DocubrainMimeTypes
from docubrain.file_processing.file_types import PRESENTATION_MIME_TYPE
from docubrain.file_processing.file_types import WORD_PROCESSING_MIME_TYPE
from docubrain.file_processing.html_utils import parse_html_page_basic
from docubrain.file_processing.unstructured import get_unstructured_api_key
from docubrain.file_processing.unstructured import unstructured_to_text
from docubrain.utils.logger import setup_logger

if TYPE_CHECKING:
    from markitdown import MarkItDown
logger = setup_logger()

TEXT_SECTION_SEPARATOR = "\n\n"

_MARKITDOWN_CONVERTER: Optional["MarkItDown"] = None

KNOWN_OPENPYXL_BUGS = [
    "Value must be either numerical or a string containing a wildcard",
    "File contains no valid workbook part",
    "Unable to read workbook: could not read stylesheet from None",
    "Colors must be aRGB hex values",
]


def get_markitdown_converter() -> "MarkItDown":
    global _MARKITDOWN_CONVERTER

    if _MARKITDOWN_CONVERTER is None:
        from markitdown import MarkItDown

        # Patch this function to effectively no-op because we were seeing this
        # module take an inordinate amount of time to convert charts to markdown,
        # making some powerpoint files with many or complicated charts nearly
        # unindexable.
        from markitdown.converters._pptx_converter import PptxConverter

        def _safe_convert_chart_to_markdown(self: Any, chart: Any) -> str:
            title = "data not extracted"
            try:
                if getattr(chart, "has_title", False) and getattr(chart, "chart_title", None):
                    text_frame = getattr(chart.chart_title, "text_frame", None)
                    if text_frame and getattr(text_frame, "text", None):
                        title_text = text_frame.text.strip()
                        if title_text:
                            title = f" - {title_text}: data not extracted"
            except Exception:
                pass
            return f"\n\n[chart detected{title}]\n\n"

        setattr(
            PptxConverter,
            "_convert_chart_to_markdown",
            _safe_convert_chart_to_markdown,
        )
        _MARKITDOWN_CONVERTER = MarkItDown(enable_plugins=False)
    return _MARKITDOWN_CONVERTER


def get_file_ext(file_path_or_name: str | Path) -> str:
    _, extension = os.path.splitext(file_path_or_name)
    return extension.lower()


def is_text_file(file: IO[bytes]) -> bool:
    """
    checks if the first 1024 bytes only contain printable or whitespace characters
    if it does, then we say it's a plaintext file
    """
    raw_data = file.read(1024)
    file.seek(0)
    text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)) - {0x7F})
    return all(c in text_chars for c in raw_data)


def detect_encoding(file: IO[bytes]) -> str:
    raw_data = file.read(50000)
    file.seek(0)
    encoding = chardet.detect(raw_data)["encoding"] or "utf-8"
    return encoding


def is_macos_resource_fork_file(file_name: str) -> bool:
    return os.path.basename(file_name).startswith("._") and file_name.startswith(
        "__MACOSX"
    )


def to_bytesio(stream: IO[bytes]) -> BytesIO:
    if isinstance(stream, BytesIO):
        return stream
    data = stream.read()  # consumes the stream!
    return BytesIO(data)


def load_files_from_zip(
    zip_file_io: IO,
    ignore_macos_resource_fork_files: bool = True,
    ignore_dirs: bool = True,
) -> Iterator[tuple[zipfile.ZipInfo, IO[Any]]]:
    """
    Iterates through files in a zip archive, yielding (ZipInfo, file handle) pairs.
    """
    with zipfile.ZipFile(zip_file_io, "r") as zip_file:
        for file_info in zip_file.infolist():
            if ignore_dirs and file_info.is_dir():
                continue

            if (
                ignore_macos_resource_fork_files
                and is_macos_resource_fork_file(file_info.filename)
            ) or file_info.filename == DOCUBRAIN_METADATA_FILENAME:
                continue

            with zip_file.open(file_info.filename, "r") as subfile:
                # Try to match by exact filename first
                yield file_info, subfile


def _extract_docubrain_metadata(line: str) -> dict | None:
    """
    Example: first line has:
        <!-- DOCUBRAIN_METADATA={"title": "..."} -->
      or
        #DOCUBRAIN_METADATA={"title":"..."}
    """
    html_comment_pattern = r"<!--\s*DOCUBRAIN_METADATA=\{(.*?)\}\s*-->"
    hashtag_pattern = r"#DOCUBRAIN_METADATA=\{(.*?)\}"

    html_comment_match = re.search(html_comment_pattern, line)
    hashtag_match = re.search(hashtag_pattern, line)

    if html_comment_match:
        json_str = html_comment_match.group(1)
    elif hashtag_match:
        json_str = hashtag_match.group(1)
    else:
        return None

    try:
        return json.loads("{" + json_str + "}")
    except json.JSONDecodeError:
        return None


def read_text_file(
    file: IO,
    encoding: str = "utf-8",
    errors: str = "replace",
    ignore_docubrain_metadata: bool = True,
) -> tuple[str, dict]:
    """
    For plain text files. Optionally extracts DocuBrain metadata from the first line.
    """
    metadata = {}
    file_content_raw = ""
    for ind, line in enumerate(file):
        # decode
        try:
            line = line.decode(encoding) if isinstance(line, bytes) else line
        except UnicodeDecodeError:
            line = (
                line.decode(encoding, errors=errors)
                if isinstance(line, bytes)
                else line
            )

        # optionally parse metadata in the first line
        if ind == 0 and not ignore_docubrain_metadata:
            potential_meta = _extract_docubrain_metadata(line)
            if potential_meta is not None:
                metadata = potential_meta
                continue

        file_content_raw += line

    return file_content_raw, metadata


def pdf_to_text(file: IO[Any], pdf_pass: str | None = None) -> str:
    """
    Extract text from a PDF. For embedded images, a more complex approach is needed.
    This is a minimal approach returning text only.
    """
    text, _, _ = read_pdf_file(file, pdf_pass)
    return text


def ocr_pdf_to_text(pdf_bytes: bytes, file_name: str = "") -> str:
    """OCR a scanned / image-only PDF.

    Prefers Unstructured (hi-res) when an API key is configured, else falls back
    to local Tesseract. Returns "" if OCR is unavailable/fails; never raises.
    """
    if not pdf_bytes:
        return ""

    from docubrain.configs.app_configs import PDF_OCR_DPI
    from docubrain.configs.app_configs import PDF_OCR_MAX_PAGES

    # Unstructured (hosted hi-res OCR) — only if an API key is configured.
    try:
        unstructured_enabled = bool(get_unstructured_api_key())
    except Exception as e:
        logger.warning(f"Could not resolve Unstructured API key: {e}")
        unstructured_enabled = False
    if unstructured_enabled:
        try:
            text = unstructured_to_text(BytesIO(pdf_bytes), file_name)
            if text and text.strip():
                logger.info(f"OCR_USED=true engine=unstructured file={file_name!r}")
                return text
        except Exception as e:
            logger.warning(
                f"Unstructured OCR failed for {file_name!r}: {e}. Falling back to Tesseract."
            )

    # 2. Local Tesseract fallback.
    try:
        import pytesseract  # type: ignore
        from pdf2image import convert_from_bytes  # type: ignore
    except Exception as e:
        logger.warning(
            f"OCR fallback unavailable for {file_name!r} (missing pytesseract/pdf2image: {e}). "
            "Scanned PDF will index without text."
        )
        return ""

    try:
        images = convert_from_bytes(
            pdf_bytes, dpi=PDF_OCR_DPI, first_page=1, last_page=PDF_OCR_MAX_PAGES
        )
    except Exception as e:
        logger.warning(f"pdf2image rendering failed for {file_name!r}: {e}")
        return ""

    page_texts: list[str] = []
    for page_num, image in enumerate(images, start=1):
        try:
            page_texts.append(pytesseract.image_to_string(image) or "")
        except Exception as e:
            logger.warning(
                f"Tesseract OCR failed on page {page_num} of {file_name!r}: {e}"
            )

    ocr_text = TEXT_SECTION_SEPARATOR.join(t for t in page_texts if t.strip())
    if ocr_text.strip():
        logger.info(
            f"OCR_USED=true engine=tesseract pages={len(page_texts)} file={file_name!r}"
        )
    return ocr_text


def read_pdf_file(
    file: IO[Any],
    pdf_pass: str | None = None,
    extract_images: bool = False,
    image_callback: Callable[[bytes, str], None] | None = None,
) -> tuple[str, dict[str, Any], Sequence[tuple[bytes, str]]]:
    """
    Returns the text, basic PDF metadata, and optionally extracted images.
    """
    from pypdf import PdfReader
    from pypdf.errors import PdfStreamError

    metadata: dict[str, Any] = {}
    extracted_images: list[tuple[bytes, str]] = []
    try:
        pdf_reader = PdfReader(file)

        if pdf_reader.is_encrypted:
            # Try the explicit password first, then fall back to an empty
            # string.  Owner-password-only PDFs (permission restrictions but
            # no open password) decrypt successfully with "".
            # See https://github.com/docubrain-dot-app/docubrain/issues/9754
            passwords = [p for p in [pdf_pass, ""] if p is not None]
            decrypt_success = False
            for pw in passwords:
                try:
                    if pdf_reader.decrypt(pw) != 0:
                        decrypt_success = True
                        break
                except Exception:
                    pass

            if not decrypt_success:
                logger.error(
                    "Encrypted PDF could not be decrypted, returning empty text."
                )
                return "", metadata, []

        # Basic PDF metadata
        if pdf_reader.metadata is not None:
            for key, value in pdf_reader.metadata.items():
                clean_key = key.lstrip("/")
                if isinstance(value, str) and value.strip():
                    metadata[clean_key] = value
                elif isinstance(value, list) and all(
                    isinstance(item, str) for item in value
                ):
                    metadata[clean_key] = ", ".join(value)

        text = TEXT_SECTION_SEPARATOR.join(
            page.extract_text() for page in pdf_reader.pages
        )

        # Scanned / image-only PDFs have no text layer; OCR instead of
        # dropping the document.
        from docubrain.configs.app_configs import ENABLE_PDF_OCR

        if ENABLE_PDF_OCR and not text.strip():
            try:
                file.seek(0)
                pdf_bytes = file.read()
            except Exception:
                pdf_bytes = b""
            ocr_text = ocr_pdf_to_text(pdf_bytes, file_name="")
            if ocr_text.strip():
                text = ocr_text

        if extract_images:
            for page_num, page in enumerate(pdf_reader.pages):
                for image_file_object in page.images:
                    image = Image.open(io.BytesIO(image_file_object.data))
                    img_byte_arr = io.BytesIO()
                    image.save(img_byte_arr, format=image.format)
                    img_bytes = img_byte_arr.getvalue()

                    image_format = image.format.lower() if image.format else "png"
                    image_name = f"page_{page_num + 1}_image_{image_file_object.name}.{image_format}"
                    if image_callback is not None:
                        # Stream image out immediately
                        image_callback(img_bytes, image_name)
                    else:
                        extracted_images.append((img_bytes, image_name))

        return text, metadata, extracted_images

    except PdfStreamError:
        logger.exception("Invalid PDF file")
    except Exception:
        logger.exception("Failed to read PDF")

    return "", metadata, []


def extract_docx_images(docx_bytes: IO[Any]) -> Iterator[tuple[bytes, str]]:
    """
    Given the bytes of a docx file, extract all the images.
    Returns a list of tuples (image_bytes, image_name).
    """
    try:
        with zipfile.ZipFile(docx_bytes) as z:
            for name in z.namelist():
                if name.startswith("word/media/"):
                    yield (z.read(name), name.split("/")[-1])
    except Exception:
        logger.exception("Failed to extract all docx images")


def read_docx_file(
    file: IO[Any],
    file_name: str = "",
    extract_images: bool = False,
    image_callback: Callable[[bytes, str], None] | None = None,
) -> tuple[str, Sequence[tuple[bytes, str]]]:
    """
    Extract text from a docx.
    Return (text_content, list_of_images).

    The caller can choose to provide a callback to handle images with the intent
    of avoiding materializing the list of images in memory.
    The images list returned is empty in this case.
    """
    md = get_markitdown_converter()
    from markitdown import (
        StreamInfo,
        FileConversionException,
        UnsupportedFormatException,
    )

    try:
        doc = md.convert(
            to_bytesio(file), stream_info=StreamInfo(mimetype=WORD_PROCESSING_MIME_TYPE)
        )
    except (
        BadZipFile,
        ValueError,
        FileConversionException,
        UnsupportedFormatException,
    ) as e:
        logger.warning(
            f"Failed to extract docx {file_name or 'docx file'}: {e}. Attempting to read as text file."
        )

        # May be an invalid docx, but still a valid text file
        file.seek(0)
        encoding = detect_encoding(file)
        text_content_raw, _ = read_text_file(
            file, encoding=encoding, ignore_docubrain_metadata=False
        )
        return text_content_raw or "", []

    file.seek(0)

    if extract_images:
        if image_callback is None:
            return doc.markdown, list(extract_docx_images(to_bytesio(file)))
        # If a callback is provided, iterate and stream images without accumulating
        try:
            for img_file_bytes, img_file_name in extract_docx_images(to_bytesio(file)):
                image_callback(img_file_bytes, img_file_name)
        except Exception:
            logger.exception("Failed to stream docx images")
    return doc.markdown, []


def pptx_to_text(file: IO[Any], file_name: str = "") -> str:
    md = get_markitdown_converter()
    from markitdown import (
        StreamInfo,
        FileConversionException,
        UnsupportedFormatException,
    )

    stream_info = StreamInfo(
        mimetype=PRESENTATION_MIME_TYPE, filename=file_name or None, extension=".pptx"
    )
    try:
        presentation = md.convert(to_bytesio(file), stream_info=stream_info)
    except (
        BadZipFile,
        ValueError,
        FileConversionException,
        UnsupportedFormatException,
    ) as e:
        error_str = f"Failed to extract text from {file_name or 'pptx file'}: {e}"
        logger.warning(error_str)
        return ""
    return presentation.markdown


def _worksheet_to_matrix(
    worksheet: Worksheet,
) -> list[list[str]]:
    """
    Converts a singular worksheet to a matrix of values
    """
    rows: list[list[str]] = []
    for worksheet_row in worksheet.iter_rows(min_row=1, values_only=True):
        row = ["" if cell is None else str(cell) for cell in worksheet_row]
        rows.append(row)

    return rows


def _clean_worksheet_matrix(matrix: list[list[str]]) -> list[list[str]]:
    """
    Cleans a worksheet matrix by removing rows if there are N consecutive empty
    rows and removing cols if there are M consecutive empty columns
    """
    MAX_EMPTY_ROWS = 2  # Runs longer than this are capped to max_empty; shorter runs are preserved as-is
    MAX_EMPTY_COLS = 2

    # Row cleanup
    matrix = _remove_empty_runs(matrix, max_empty=MAX_EMPTY_ROWS)

    if not matrix:
        return matrix

    # Column cleanup — determine which columns to keep without transposing.
    num_cols = len(matrix[0])
    keep_cols = _columns_to_keep(matrix, num_cols, max_empty=MAX_EMPTY_COLS)
    if len(keep_cols) < num_cols:
        matrix = [[row[c] for c in keep_cols] for row in matrix]

    return matrix


def _columns_to_keep(
    matrix: list[list[str]], num_cols: int, max_empty: int
) -> list[int]:
    """Return the indices of columns to keep after removing empty-column runs.

    Uses the same logic as ``_remove_empty_runs`` but operates on column
    indices so no transpose is needed.
    """
    kept: list[int] = []
    empty_buffer: list[int] = []

    for col_idx in range(num_cols):
        col_is_empty = all(not row[col_idx] for row in matrix)
        if col_is_empty:
            empty_buffer.append(col_idx)
        else:
            kept.extend(empty_buffer[:max_empty])
            kept.append(col_idx)
            empty_buffer = []

    return kept


def _remove_empty_runs(
    rows: list[list[str]],
    max_empty: int,
) -> list[list[str]]:
    """Removes entire runs of empty rows when the run length exceeds max_empty.

    Leading empty runs are capped to max_empty, just like interior runs.
    Trailing empty rows are always dropped since there is no subsequent
    non-empty row to flush them.
    """
    result: list[list[str]] = []
    empty_buffer: list[list[str]] = []

    for row in rows:
        # Check if empty
        if not any(row):
            if len(empty_buffer) < max_empty:
                empty_buffer.append(row)
        else:
            # Add upto max empty rows onto the result - that's what we allow
            result.extend(empty_buffer[:max_empty])
            # Add the new non-empty row
            result.append(row)
            empty_buffer = []

    return result


def xlsx_to_text(file: IO[Any], file_name: str = "") -> str:
    from markitdown import StreamInfo
    from docubrain.file_processing.file_types import SPREADSHEET_MIME_TYPE
    import concurrent.futures

    # 1. Try markitdown conversion with timeout to avoid infinite hangs
    try:
        file_bytes = to_bytesio(file)
        md = get_markitdown_converter()
        stream_info = StreamInfo(
            mimetype=SPREADSHEET_MIME_TYPE, filename=file_name or None, extension=".xlsx"
        )
        
        def _run_markitdown() -> str:
            return md.convert(file_bytes, stream_info=stream_info).markdown

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run_markitdown)
            # Timeout set to 10 seconds. Note: ThreadPoolExecutor does not terminate hung threads,
            # so repeated hangs could slowly exhaust threads. However, this ensures indexing is not blocked.
            return future.result(timeout=10)
    except concurrent.futures.TimeoutError:
        logger.warning(f"Timeout (10s) using markitdown for {file_name or 'xlsx file'}. Falling back to openpyxl markdown.")
        file.seek(0)
    except Exception as e:
        logger.warning(f"markitdown parsing failed for {file_name or 'xlsx file'}: {e}. Falling back to openpyxl markdown.")
        file.seek(0)
        
    # 2. Fallback to Openpyxl-based Markdown Table logic
    try:
        workbook = openpyxl.load_workbook(file, read_only=True)
    except BadZipFile as e:
        error_str = f"Failed to extract text from {file_name or 'xlsx file'}: {e}"
        if file_name.startswith("~"):
            logger.debug(error_str + " (this is expected for files with ~)")
        else:
            logger.warning(error_str)
        return ""
    except Exception as e:
        if any(s in str(e) for s in KNOWN_OPENPYXL_BUGS):
            logger.error(
                f"Failed to extract text from {file_name or 'xlsx file'}. This happens due to a bug in openpyxl. {e}"
            )
            return ""
        raise

    text_content = []
    for sheet in workbook.worksheets:
        matrix = _worksheet_to_matrix(sheet)
        if not matrix:
            continue
            
        # Clean and escape matrix cells
        clean_matrix: list[list[str]] = []
        max_cols = 0
        for row in matrix:
            clean_row = []
            for cell in row:
                if cell is None:
                    cell_str = ""
                else:
                    cell_str = str(cell)
                # Ensure no newlines or pipes break markdown table layout
                cell_str = cell_str.replace('\n', ' ').replace('\r', ' ').replace('|', '\\|').strip()
                clean_row.append(cell_str)
            if len(clean_row) > max_cols:
                max_cols = len(clean_row)
            clean_matrix.append(clean_row)

        if not clean_matrix or max_cols == 0:
            continue
            
        # Pad rows to uniform width
        for row in clean_matrix:
            if len(row) < max_cols:
                row.extend([""] * (max_cols - len(row)))

        lines = []
        header = clean_matrix[0]
        # Skip completely empty sheets
        if all(c == "" for c in header) and len(clean_matrix) == 1:
            continue
            
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "|".join(["---"] * max_cols) + "|")
        
        for row in clean_matrix[1:]:
            lines.append("| " + " | ".join(row) + " |")
            
        text_content.append("\n".join(lines))
        
    return TEXT_SECTION_SEPARATOR.join(text_content)


# Strip thousands separators / whitespace (incl. non-breaking space) before
# attempting numeric coercion.
_NUMERIC_CLEAN_RE = re.compile(r"[,\s ]")
# Leading currency symbol (USD/EUR/GBP/INR/JPY) that should not defeat numeric
# detection of an otherwise-numeric cell.
_CURRENCY_PREFIX_RE = re.compile(r"^[\$€£₹¥]\s?")
_NUMBER_RE = re.compile(r"-?\d+(\.\d+)?")
_DATE_RE = re.compile(r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}")


def coerce_cell_value(raw: str) -> tuple[str, float | None]:
    """Classify a spreadsheet cell value for typed/structured indexing.

    Returns ``(cell_type, numeric_value)`` where ``cell_type`` is one of
    ``"number" | "date" | "bool" | "text" | "empty"`` and ``numeric_value`` is
    the parsed float for numeric cells (else ``None``). Currency symbols,
    thousands separators, and a trailing percent sign are tolerated so that
    ``"₹12,300"`` and ``"12300"`` are both recognized as numbers — enabling
    numeric filtering/aggregation downstream instead of treating every cell as
    opaque text.
    """
    val = (raw or "").strip()
    if not val:
        return "empty", None

    if val.lower() in ("true", "false", "yes", "no"):
        return "bool", None

    candidate = _CURRENCY_PREFIX_RE.sub("", val).rstrip("%")
    candidate = _NUMERIC_CLEAN_RE.sub("", candidate)
    if candidate and _NUMBER_RE.fullmatch(candidate):
        try:
            return "number", float(candidate)
        except ValueError:
            pass

    if _DATE_RE.fullmatch(val):
        return "date", None

    return "text", None


def _typed_fields(
    fields: dict[str, str],
) -> tuple[dict[str, str], dict[str, float]]:
    """Derive ``field_types`` and ``numeric_fields`` from raw string fields."""
    field_types: dict[str, str] = {}
    numeric_fields: dict[str, float] = {}
    for header, value in fields.items():
        cell_type, numeric_value = coerce_cell_value(value)
        field_types[header] = cell_type
        if numeric_value is not None:
            numeric_fields[header] = numeric_value
    return field_types, numeric_fields


def xlsx_to_row_records(
    file: IO[Any],
    file_name: str = "",
) -> list[dict[str, Any]]:
    """Parse an XLSX/XLS file into per-row record dicts for structured indexing.

    Each returned dict has:
      - ``text``: a natural-language representation of the row
        (e.g. "Name: Abhiram Chowdary Y | Designation: Software Developer - Intern")
      - ``sheet_name``: originating worksheet name
      - ``row_index``: 0-based data-row index (excludes header)
      - ``fields``: dict mapping column header → cell value
      - ``file_name``: the source file name
    """
    try:
        workbook = openpyxl.load_workbook(file, read_only=True)
    except BadZipFile as e:
        logger.warning(f"xlsx_to_row_records: bad zip for {file_name}: {e}")
        return []
    except Exception as e:
        if any(s in str(e) for s in KNOWN_OPENPYXL_BUGS):
            logger.error(f"xlsx_to_row_records: openpyxl bug for {file_name}: {e}")
            return []
        raise

    records: list[dict[str, Any]] = []

    for sheet in workbook.worksheets:
        matrix = _worksheet_to_matrix(sheet)
        if not matrix or len(matrix) < 2:
            continue

        headers = [
            cell.strip() if cell else f"Column_{i}"
            for i, cell in enumerate(matrix[0])
        ]

        # Skip sheets where the header row is entirely empty
        if all(h.startswith("Column_") for h in headers):
            continue

        for row_idx, row in enumerate(matrix[1:]):
            fields: dict[str, str] = {}
            all_empty = True
            for col_idx, cell in enumerate(row):
                header = headers[col_idx] if col_idx < len(headers) else f"Column_{col_idx}"
                val = cell.strip() if cell else ""
                val = val.replace("\n", " ").replace("\r", " ")
                fields[header] = val
                if val:
                    all_empty = False

            if all_empty:
                continue

            # Build natural-language text: "Header1: Value1 | Header2: Value2 | ..."
            text_parts = [f"{k}: {v}" for k, v in fields.items() if v]
            record_text = " | ".join(text_parts)
            record_text = f"Record from {file_name} — {sheet.title}:\n{record_text}"

            field_types, numeric_fields = _typed_fields(fields)
            records.append({
                "text": record_text,
                "sheet_name": sheet.title,
                "row_index": row_idx,
                "fields": fields,
                "field_types": field_types,
                "numeric_fields": numeric_fields,
                "file_name": file_name,
            })

    return records


def csv_text_to_row_records(
    csv_text: str,
    file_name: str = "",
    sheet_name: str = "Sheet1",
) -> list[dict[str, Any]]:
    """Parse CSV text (e.g. from Google Sheets export) into per-row records.

    Same output format as ``xlsx_to_row_records``.
    """
    records: list[dict[str, Any]] = []
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    if len(rows) < 2:
        return records

    headers = [h.strip() if h.strip() else f"Column_{i}" for i, h in enumerate(rows[0])]
    if all(h.startswith("Column_") for h in headers):
        return records

    for row_idx, row in enumerate(rows[1:]):
        fields: dict[str, str] = {}
        all_empty = True
        for col_idx, cell in enumerate(row):
            header = headers[col_idx] if col_idx < len(headers) else f"Column_{col_idx}"
            val = cell.strip().replace("\n", " ").replace("\r", " ")
            fields[header] = val
            if val:
                all_empty = False

        if all_empty:
            continue

        text_parts = [f"{k}: {v}" for k, v in fields.items() if v]
        record_text = " | ".join(text_parts)
        record_text = f"Record from {file_name} — {sheet_name}:\n{record_text}"

        field_types, numeric_fields = _typed_fields(fields)
        records.append({
            "text": record_text,
            "sheet_name": sheet_name,
            "row_index": row_idx,
            "fields": fields,
            "field_types": field_types,
            "numeric_fields": numeric_fields,
            "file_name": file_name,
        })

    return records


def eml_to_text(file: IO[Any]) -> str:
    encoding = detect_encoding(file)
    text_file = io.TextIOWrapper(file, encoding=encoding)
    parser = EmailParser()
    try:
        message = parser.parse(text_file)
    finally:
        try:
            # Keep underlying upload handle open for downstream consumers.
            raw_file = text_file.detach()
        except Exception as detach_error:
            logger.warning(
                f"Failed to detach TextIOWrapper for EML upload, using original file: {detach_error}"
            )
            raw_file = file
        try:
            raw_file.seek(0)
        except Exception:
            pass

    text_content = []
    for part in message.walk():
        if part.get_content_type().startswith("text/plain"):
            payload = part.get_payload()
            if isinstance(payload, str):
                text_content.append(payload)
            elif isinstance(payload, list):
                text_content.extend(item for item in payload if isinstance(item, str))
            else:
                logger.warning(f"Unexpected payload type: {type(payload)}")
    return TEXT_SECTION_SEPARATOR.join(text_content)


def epub_to_text(file: IO[Any]) -> str:
    with zipfile.ZipFile(file) as epub:
        text_content = []
        for item in epub.infolist():
            if item.filename.endswith(".xhtml") or item.filename.endswith(".html"):
                with epub.open(item) as html_file:
                    text_content.append(parse_html_page_basic(html_file))
        return TEXT_SECTION_SEPARATOR.join(text_content)


def file_io_to_text(file: IO[Any]) -> str:
    encoding = detect_encoding(file)
    file_content, _ = read_text_file(file, encoding=encoding)
    return file_content


def extract_file_text(
    file: IO[Any],
    file_name: str,
    break_on_unprocessable: bool = True,
    extension: str | None = None,
) -> str:
    """
    Legacy function that returns *only text*, ignoring embedded images.
    For backward-compatibility in code that only wants text.

    NOTE: Ignoring seems to be defined as returning an empty string for files it can't
    handle (such as images).
    """
    extension_to_function: dict[str, Callable[[IO[Any]], str]] = {
        ".pdf": pdf_to_text,
        ".docx": lambda f: read_docx_file(f, file_name)[0],  # no images
        ".pptx": lambda f: pptx_to_text(f, file_name),
        ".xlsx": lambda f: xlsx_to_text(f, file_name),
        ".eml": eml_to_text,
        ".epub": epub_to_text,
        ".html": parse_html_page_basic,
    }

    try:
        if get_unstructured_api_key():
            try:
                return unstructured_to_text(file, file_name)
            except Exception as unstructured_error:
                logger.error(
                    f"Failed to process with Unstructured: {str(unstructured_error)}. Falling back to normal processing."
                )
        if extension is None:
            extension = get_file_ext(file_name)

        if extension in DocubrainFileExtensions.TEXT_AND_DOCUMENT_EXTENSIONS:
            func = extension_to_function.get(extension, file_io_to_text)
            file.seek(0)
            return func(file)

        # If unknown extension, maybe it's a text file
        file.seek(0)
        if is_text_file(file):
            return file_io_to_text(file)

        raise ValueError("Unknown file extension or not recognized as text data")

    except Exception as e:
        if break_on_unprocessable:
            raise RuntimeError(
                f"Failed to process file {file_name or 'Unknown'}: {str(e)}"
            ) from e
        logger.warning(f"Failed to process file {file_name or 'Unknown'}: {str(e)}")
        return ""


class ExtractionResult(NamedTuple):
    """Structured result from text and image extraction from various file types."""

    text_content: str
    embedded_images: Sequence[tuple[bytes, str]]
    metadata: dict[str, Any]


def extract_result_from_text_file(file: IO[Any]) -> ExtractionResult:
    encoding = detect_encoding(file)
    text_content_raw, file_metadata = read_text_file(
        file, encoding=encoding, ignore_docubrain_metadata=False
    )
    return ExtractionResult(
        text_content=text_content_raw,
        embedded_images=[],
        metadata=file_metadata,
    )


def extract_text_and_images(
    file: IO[Any],
    file_name: str,
    pdf_pass: str | None = None,
    content_type: str | None = None,
    image_callback: Callable[[bytes, str], None] | None = None,
) -> ExtractionResult:
    """
    Primary new function for the updated connector.
    Returns structured extraction result with text content, embedded images, and metadata.

    Args:
        file: File-like object to extract content from.
        file_name: Name of the file (used to determine extension/type).
        pdf_pass: Optional password for encrypted PDFs.
        content_type: Optional MIME type override for the file.
        image_callback: Optional callback for streaming image extraction. When provided,
            embedded images are passed to this callback one at a time as (bytes, filename)
            instead of being accumulated in the returned ExtractionResult.embedded_images
            list. This is a memory optimization for large documents with many images -
            the caller can process/store each image immediately rather than holding all
            images in memory. When using a callback, ExtractionResult.embedded_images
            will be an empty list.

    Returns:
        ExtractionResult containing text_content, embedded_images (empty if callback used),
        and metadata extracted from the file.
    """
    res = _extract_text_and_images(
        file, file_name, pdf_pass, content_type, image_callback
    )
    # Clean up any temporary objects and force garbage collection
    unreachable = gc.collect()
    logger.info(f"Unreachable objects: {unreachable}")

    return res


def _extract_text_and_images(
    file: IO[Any],
    file_name: str,
    pdf_pass: str | None = None,
    content_type: str | None = None,
    image_callback: Callable[[bytes, str], None] | None = None,
) -> ExtractionResult:
    file.seek(0)

    if get_unstructured_api_key():
        try:
            text_content = unstructured_to_text(file, file_name)
            return ExtractionResult(
                text_content=text_content, embedded_images=[], metadata={}
            )
        except Exception as e:
            logger.error(
                f"Failed to process with Unstructured: {str(e)}. Falling back to normal processing."
            )
            file.seek(0)  # Reset file pointer just in case

    # When we upload a document via a connector or MyDocuments, we extract and store the content of files
    # with content types in UploadMimeTypes.DOCUMENT_MIME_TYPES as plain text files.
    # As a result, the file name extension may differ from the original content type.
    # We process files with a plain text content type first to handle this scenario.
    if content_type in DocubrainMimeTypes.TEXT_MIME_TYPES:
        return extract_result_from_text_file(file)

    # Default processing
    try:
        extension = get_file_ext(file_name)
        # docx example for embedded images
        if extension == ".docx":
            text_content, images = read_docx_file(
                file, file_name, extract_images=True, image_callback=image_callback
            )
            return ExtractionResult(
                text_content=text_content, embedded_images=images, metadata={}
            )

        # PDF example: we do not show complicated PDF image extraction here
        # so we simply extract text for now and skip images.
        if extension == ".pdf":
            text_content, pdf_metadata, images = read_pdf_file(
                file,
                pdf_pass,
                extract_images=get_image_extraction_and_analysis_enabled(),
                image_callback=image_callback,
            )
            return ExtractionResult(
                text_content=text_content, embedded_images=images, metadata=pdf_metadata
            )

        # For PPTX, XLSX, EML, etc., we do not show embedded image logic here.
        # You can do something similar to docx if needed.
        if extension == ".pptx":
            return ExtractionResult(
                text_content=pptx_to_text(file, file_name=file_name),
                embedded_images=[],
                metadata={},
            )

        if extension == ".xlsx":
            return ExtractionResult(
                text_content=xlsx_to_text(file, file_name=file_name),
                embedded_images=[],
                metadata={},
            )

        if extension == ".eml":
            return ExtractionResult(
                text_content=eml_to_text(file), embedded_images=[], metadata={}
            )

        if extension == ".epub":
            return ExtractionResult(
                text_content=epub_to_text(file), embedded_images=[], metadata={}
            )

        if extension == ".html":
            return ExtractionResult(
                text_content=parse_html_page_basic(file),
                embedded_images=[],
                metadata={},
            )

        # If we reach here and it's a recognized text extension
        if extension in DocubrainFileExtensions.PLAIN_TEXT_EXTENSIONS:
            return extract_result_from_text_file(file)

        # If it's an image file or something else, we do not parse embedded images from them
        # just return empty text
        return ExtractionResult(text_content="", embedded_images=[], metadata={})

    except Exception as e:
        logger.exception(f"Failed to extract text/images from {file_name}: {e}")
        return ExtractionResult(text_content="", embedded_images=[], metadata={})


def docx_to_txt_filename(file_path: str) -> str:
    return file_path.rsplit(".", 1)[0] + ".txt"
