"""Post-generation answer-grounding verification.

DocuBrain renders citations (``chat/citation_processor.py``) but never *verifies*
them: an answer can cite ``[7]`` when only 4 documents were retrieved, or assert
claims that no retrieved evidence supports. This module adds a cheap, opt-in
verification stage that runs AFTER answer generation:

1. **Citation validity** (LLM-free, always safe): every ``[n]`` marker in the
   answer must map to a real retrieved document. Markers that don't are
   "phantom" citations — a strong hallucination signal.
2. **Claim grounding** (optional LLM judge): each answer sentence is checked
   against the cited evidence; unsupported sentences are flagged.

Both stages are pure/decoupled: the LLM judge is dependency-injected as a
``complete`` callable so this module has no hard dependency on the concrete LLM
interface and is unit-testable without a model server. Everything degrades
gracefully — any failure returns a "could not verify" result and never blocks
or mutates the answer.

Gated by ``ENABLE_ANSWER_VERIFICATION`` (and ``ENABLE_ANSWER_GROUNDING_JUDGE``
for the LLM stage). See ``docubrain/configs/chat_configs.py``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from typing import TYPE_CHECKING

from pydantic import BaseModel

from docubrain.configs.chat_configs import ENABLE_ANSWER_GROUNDING_JUDGE
from docubrain.configs.chat_configs import ENABLE_ANSWER_VERIFICATION
from docubrain.utils.logger import setup_logger

if TYPE_CHECKING:
    # Typing-only: avoids pulling heavy modules at import time and keeps the
    # citation/grounding logic unit-testable in isolation.
    from docubrain.context.search.models import SearchDoc
    from docubrain.llm.interfaces import LLM

logger = setup_logger()

# Matches inline citation markers like [1], [12]. Deliberately narrow: only
# bracketed integers, mirroring DynamicCitationProcessor's marker format.
_CITATION_RE = re.compile(r"\[(\d+)\]")

# Split an answer into rough sentences for per-claim grounding. Intentionally
# simple (no NLP dependency); the LLM judge tolerates imperfect boundaries.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# Cap evidence sent to the judge to keep the verification call bounded.
_MAX_EVIDENCE_CHARS = 6000

# Vespa match-highlight markup wrapping matched terms, e.g. "the <hi>answer</hi>".
_HI_TAG_RE = re.compile(r"</?hi>")


class CitationValidation(BaseModel):
    """Result of the LLM-free citation-validity check."""

    cited_numbers: list[int]
    # Cited markers that DO map to a retrieved document.
    valid_numbers: list[int]
    # Cited markers with no corresponding retrieved document ("phantom"
    # citations) — a strong hallucination signal.
    invalid_numbers: list[int]
    # Retrieved documents that were never cited (informational, not an error).
    uncited_numbers: list[int]

    @property
    def has_phantom_citations(self) -> bool:
        return bool(self.invalid_numbers)


class GroundingResult(BaseModel):
    """Result of the optional LLM grounding judge."""

    ran: bool
    supported: bool
    unsupported_claims: list[str] = []
    # 0..1 fraction of checked sentences that were judged supported.
    grounding_score: float = 1.0


class AnswerVerification(BaseModel):
    """Composite verification result attached to an answer for observability."""

    enabled: bool
    citation: CitationValidation
    grounding: GroundingResult

    @property
    def is_trustworthy(self) -> bool:
        """Best-effort single signal: no phantom citations and (if the judge
        ran) no unsupported claims."""
        if self.citation.has_phantom_citations:
            return False
        if self.grounding.ran and not self.grounding.supported:
            return False
        return True


def extract_citation_numbers(text: str) -> list[int]:
    """Return the distinct citation numbers referenced in ``text``, in first-seen order."""
    seen: dict[int, None] = {}
    for match in _CITATION_RE.finditer(text or ""):
        seen.setdefault(int(match.group(1)), None)
    return list(seen.keys())


def validate_citations(
    answer: str,
    citation_mapping: dict[int, "SearchDoc"] | dict[int, Any],
) -> CitationValidation:
    """Check that every ``[n]`` in ``answer`` maps to a retrieved document.

    Pure and LLM-free. ``citation_mapping`` is the citation-number → document
    mapping produced during retrieval (``CitationMapping``); only its keys are
    used here, so any mapping-like object works.
    """
    cited = extract_citation_numbers(answer)
    available = set(citation_mapping.keys())
    valid = [n for n in cited if n in available]
    invalid = [n for n in cited if n not in available]
    uncited = sorted(available - set(cited))

    if invalid:
        logger.warning(
            "Answer verification: phantom citations %s not in retrieved set %s",
            invalid,
            sorted(available),
        )
    return CitationValidation(
        cited_numbers=cited,
        valid_numbers=valid,
        invalid_numbers=invalid,
        uncited_numbers=uncited,
    )


def _clean_highlight(text: str) -> str:
    """Strip Vespa ``<hi>`` highlight markup from a match-highlight snippet."""
    return _HI_TAG_RE.sub("", text or "").strip()


def _doc_evidence_text(doc: Any) -> str:
    """Extract the fullest available content for one cited document.

    Preference order: full ``content`` (present on ``SavedSearchDocWithContent``)
    → cleaned ``match_highlights`` (the actually-matched passages — substantially
    richer than the blurb) → ``blurb``. Returns "" when nothing is available.
    """
    content = getattr(doc, "content", None)
    if content:
        return str(content).strip()

    highlights = getattr(doc, "match_highlights", None)
    if highlights:
        cleaned = [_clean_highlight(h) for h in highlights if _clean_highlight(h)]
        if cleaned:
            return " … ".join(cleaned)

    blurb = getattr(doc, "blurb", None)
    return str(blurb).strip() if blurb else ""


def build_evidence(
    citation_mapping: dict[int, "SearchDoc"] | dict[int, Any],
    cited_numbers: list[int] | None = None,
    max_chars: int = _MAX_EVIDENCE_CHARS,
) -> str:
    """Assemble grounding evidence from the cited documents.

    Each block is labelled with its citation number and document title so the
    judge can align answer claims to specific cited evidence. When
    ``cited_numbers`` is given, only those documents are included (the evidence
    the answer actually claims to rely on); otherwise all mapped documents are
    used. Output is capped at ``max_chars``.
    """
    numbers = cited_numbers if cited_numbers else sorted(citation_mapping.keys())
    parts: list[str] = []
    total = 0
    for n in numbers:
        doc = citation_mapping.get(n)
        if doc is None:
            continue
        text = _doc_evidence_text(doc)
        if not text:
            continue
        label = getattr(doc, "semantic_identifier", None) or f"Document {n}"
        block = f"[{n}] {label}: {text}"
        if total + len(block) > max_chars:
            remaining = max_chars - total
            if remaining > 0:
                parts.append(block[:remaining])
            break
        parts.append(block)
        total += len(block) + 2  # account for the "\n\n" join separator
    return "\n\n".join(parts)


def _build_grounding_prompt(answer: str, evidence: str) -> str:
    return (
        "You are a strict fact-checker. Given EVIDENCE and an ANSWER, list any "
        "sentences in the ANSWER that are NOT supported by the EVIDENCE.\n"
        "Only judge factual claims; ignore greetings, hedging, and questions.\n"
        "Respond with one unsupported sentence per line, verbatim. If every "
        "claim is supported, respond with exactly: SUPPORTED\n\n"
        f"EVIDENCE:\n{evidence[:_MAX_EVIDENCE_CHARS]}\n\n"
        f"ANSWER:\n{answer}\n\n"
        "UNSUPPORTED SENTENCES:"
    )


def _parse_grounding_response(response: str) -> tuple[bool, list[str]]:
    """Parse the judge output into (supported, unsupported_claims)."""
    cleaned = (response or "").strip()
    if not cleaned or cleaned.upper().startswith("SUPPORTED"):
        return True, []
    unsupported = [
        line.strip()
        for line in cleaned.splitlines()
        if line.strip() and line.strip().upper() != "SUPPORTED"
    ]
    return (len(unsupported) == 0), unsupported


def run_grounding_judge(
    answer: str,
    evidence: str,
    complete: Callable[[str], str],
) -> GroundingResult:
    """Run the optional LLM grounding judge.

    ``complete`` maps a prompt string to the model's text response — injected by
    the caller so this module stays decoupled from the concrete LLM interface.
    Any error degrades to ``ran=False, supported=True`` so answers are never
    blocked by a verifier failure.
    """
    if not answer.strip() or not evidence.strip():
        return GroundingResult(ran=False, supported=True)

    try:
        raw = complete(_build_grounding_prompt(answer, evidence))
    except Exception:
        logger.warning("Answer grounding judge failed; skipping.", exc_info=True)
        return GroundingResult(ran=False, supported=True)

    supported, unsupported = _parse_grounding_response(raw)

    sentences = [s for s in _SENTENCE_SPLIT_RE.split(answer.strip()) if s.strip()]
    total = max(len(sentences), 1)
    score = max(0.0, 1.0 - len(unsupported) / total)

    return GroundingResult(
        ran=True,
        supported=supported,
        unsupported_claims=unsupported,
        grounding_score=score,
    )


def verify_answer(
    answer: str,
    citation_mapping: dict[int, "SearchDoc"] | dict[int, Any],
    evidence: str = "",
    complete: Callable[[str], str] | None = None,
    *,
    enabled: bool = ENABLE_ANSWER_VERIFICATION,
    run_judge: bool = ENABLE_ANSWER_GROUNDING_JUDGE,
) -> AnswerVerification:
    """Verify a generated ``answer`` against its retrieved evidence.

    No-op (returns ``enabled=False`` with empty/optimistic sub-results) unless
    ``enabled``. The LLM grounding judge runs only when ``run_judge`` is set AND
    a ``complete`` callable is supplied. Never raises and never mutates the
    answer — purely advisory output for logging / stream metadata.
    """
    if not enabled:
        empty = CitationValidation(
            cited_numbers=[], valid_numbers=[], invalid_numbers=[], uncited_numbers=[]
        )
        return AnswerVerification(
            enabled=False,
            citation=empty,
            grounding=GroundingResult(ran=False, supported=True),
        )

    citation = validate_citations(answer, citation_mapping)

    grounding = GroundingResult(ran=False, supported=True)
    if run_judge and complete is not None:
        # Prefer caller-supplied evidence; otherwise reconstruct it from the
        # cited documents' full content (richer than a blurb summary).
        judge_evidence = evidence or build_evidence(
            citation_mapping, citation.cited_numbers
        )
        grounding = run_grounding_judge(answer, judge_evidence, complete)

    return AnswerVerification(enabled=True, citation=citation, grounding=grounding)


def make_llm_complete(llm: "LLM") -> Callable[[str], str]:
    """Adapt the project ``LLM`` into a simple ``prompt -> text`` callable.

    Used to inject the grounding judge's model without coupling this module to
    the concrete LLM interface (imports are local so the pure citation/grounding
    helpers stay testable without the LLM stack). Mirrors the single-shot,
    reasoning-off invocation convention used by the other secondary LLM flows.
    """
    from docubrain.llm.models import ReasoningEffort
    from docubrain.llm.models import UserMessage

    def _complete(prompt: str) -> str:
        response = llm.invoke(
            prompt=[UserMessage(content=prompt)],
            reasoning_effort=ReasoningEffort.OFF,
        )
        return response.choice.message.content or ""

    return _complete
