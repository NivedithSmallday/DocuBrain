import json
import re
import time
from collections.abc import Callable
from typing import Any
from typing import Literal

from sqlalchemy.orm import Session

from docubrain.chat.chat_state import ChatStateContainer
from docubrain.chat.chat_utils import create_tool_call_failure_messages
from docubrain.chat.citation_processor import CitationMapping
from docubrain.chat.citation_processor import CitationMode
from docubrain.chat.citation_processor import DynamicCitationProcessor
from docubrain.chat.citation_utils import update_citation_processor_from_tool_response
from docubrain.chat.emitter import Emitter
from docubrain.chat.llm_step import extract_tool_calls_from_response_text
from docubrain.chat.llm_step import looks_like_malformed_tool_call_payload
from docubrain.chat.llm_step import run_llm_step
from docubrain.chat.llm_step import strip_tool_call_payload_text
from docubrain.chat.models import ChatMessageSimple
from docubrain.chat.models import ContextFileMetadata
from docubrain.chat.models import ExtractedContextFiles
from docubrain.chat.models import FileToolMetadata
from docubrain.chat.models import LlmStepResult
from docubrain.chat.models import ToolCallSimple
from docubrain.chat.prompt_utils import build_reminder_message
from docubrain.chat.prompt_utils import build_system_prompt
from docubrain.chat.prompt_utils import (
    get_default_base_system_prompt,
)
from docubrain.configs.app_configs import INTEGRATION_TESTS_MODE
from docubrain.configs.chat_configs import ENABLE_ANSWER_GROUNDING_JUDGE
from docubrain.configs.chat_configs import ENABLE_ANSWER_VERIFICATION
from docubrain.configs.chat_configs import ENABLE_RETRIEVAL_TRACING
from docubrain.configs.chat_configs import SHOW_CITATIONS
from docubrain.configs.constants import DocumentSource
from docubrain.context.search.retrieval_trace import record_query_trace
from docubrain.configs.constants import MessageType
from docubrain.context.search.models import SearchDoc
from docubrain.context.search.models import SearchDocsResponse
from docubrain.db.engine.sql_engine import get_session_with_current_tenant
from docubrain.db.memory import add_memory
from docubrain.db.memory import update_memory_at_index
from docubrain.db.memory import UserMemoryContext
from docubrain.db.models import Persona
from docubrain.llm.constants import LlmProviderNames
from docubrain.llm.interfaces import LLM
from docubrain.llm.interfaces import LLMUserIdentity
from docubrain.llm.interfaces import ToolChoiceOptions
from docubrain.llm.utils import is_true_openai_model
from docubrain.prompts.chat_prompts import IMAGE_GEN_REMINDER
from docubrain.prompts.chat_prompts import OPEN_URL_REMINDER
from docubrain.secondary_llm_flows.answer_verification import make_llm_complete
from docubrain.secondary_llm_flows.answer_verification import verify_answer
from docubrain.server.query_and_chat.placement import Placement
from docubrain.server.query_and_chat.streaming_models import AgentResponseDelta
from docubrain.server.query_and_chat.streaming_models import AgentResponseStart
from docubrain.server.query_and_chat.streaming_models import OverallStop
from docubrain.server.query_and_chat.streaming_models import Packet
from docubrain.server.query_and_chat.streaming_models import ToolCallDebug
from docubrain.server.query_and_chat.streaming_models import TopLevelBranching
from docubrain.tools.built_in_tools import CITEABLE_TOOLS_NAMES
from docubrain.tools.built_in_tools import STOPPING_TOOLS_NAMES
from docubrain.tools.constants import INTERNET_SEARCH_TOOL_NAME
from docubrain.tools.constants import PYTHON_TOOL_NAME
from docubrain.tools.interface import Tool
from docubrain.tools.models import ChatFile
from docubrain.tools.models import CustomToolCallSummary
from docubrain.tools.models import MemoryToolResponseSnapshot
from docubrain.tools.models import PythonToolRichResponse
from docubrain.tools.models import ToolCallInfo
from docubrain.tools.models import ToolCallKickoff
from docubrain.tools.models import ToolResponse
from docubrain.tools.tool_implementations.memory.models import MemoryToolResponse
from docubrain.tools.tool_implementations.search.search_tool import SearchTool
from docubrain.tools.tool_runner import run_tool_calls
from docubrain.chat.retrieval_router import (
    classify_and_route,
    build_fallback_decision,
    tool_responses_are_empty,
    query_has_email_intent,
    query_has_drive_intent,
    GMAIL_MCP_TOOL_NAMES,
    DRIVE_MCP_TOOL_NAMES,
)
from docubrain.chat.retrieval_router_models import (
    RoutingDecision,
    RoutingStrategy,
)
from docubrain.tracing.framework.create import trace
from docubrain.utils.logger import setup_logger
from shared_configs.contextvars import get_current_tenant_id

logger = setup_logger()

_CASUAL_NO_TOOL_RE = re.compile(
    r"^(?:hi|hii|hiii|hello|hey|yo|sup|hola|namaste|good morning|good afternoon|good evening|"
    r"how are you|how are you doing|what's up|whats up|thanks|thank you|ok|okay|cool|great|"
    r"bye|goodbye|see you)(?:[!.?,\s]*)$",
    re.IGNORECASE,
)
_DOC_INTENT_TERMS = {
    "doc",
    "docs",
    "document",
    "documents",
    "file",
    "files",
    "folder",
    "folders",
    "drive",
    "google drive",
    "mail",
    "mails",
    "email",
    "emails",
    "gmail",
    "inbox",
    "github",
    "repo",
    "repository",
    "readme",
    "page",
    "pages",
    "source",
    "sources",
    "search",
    "find",
    "lookup",
    "look up",
    "retrieve",
    "index",
    "indexed",
    "ingested",
    "knowledge base",
    "kb",
    "mail",
    "mails",
    "email",
    "emails",
    "inbox",
    "received",
    "sent",
    "gmail",
    "drive",
    "google drive",
}


def extract_url_snippet_map(search_docs: list[SearchDoc]) -> dict[str, str]:
    return {
        doc.link: doc.blurb
        for doc in search_docs
        if doc.link and doc.blurb
    }


def _get_latest_user_message_text(
    simple_chat_history: list[ChatMessageSimple],
) -> str | None:
    for msg in reversed(simple_chat_history):
        if msg.message_type == MessageType.USER and msg.message:
            return msg.message
    return None


def _should_skip_tools_for_initial_message(
    simple_chat_history: list[ChatMessageSimple],
    context_files: ExtractedContextFiles,
    custom_agent_prompt: str | None,
) -> bool:
    latest_user_message = _get_latest_user_message_text(simple_chat_history)
    if not latest_user_message:
        return False

    if context_files.file_texts or context_files.file_metadata:
        return False

    # Custom agents may rely on tools/actions for short commands; do not gate them.
    if custom_agent_prompt:
        return False

    normalized_message = re.sub(r"\s+", " ", latest_user_message.strip().lower())
    if not normalized_message:
        return False

    if any(term in normalized_message for term in _DOC_INTENT_TERMS):
        return False

    # Only greetings / pure conversational filler may bypass retrieval. Short
    # factual queries ("PTO policy", "maternity leave", "Q3 revenue") must keep
    # tools available so the LLM can retrieve before answering. The previous
    # word_count<=3 heuristic incorrectly suppressed retrieval for these and was
    # a primary cause of "indexed but unanswered" failures.
    if _CASUAL_NO_TOOL_RE.fullmatch(normalized_message):
        return True

    return False


def _build_missing_workspace_tool_message(
    query: str,
    available_tool_names: set[str],
    has_internal_search: bool,
) -> str | None:
    """Return a system hint when the query needs a Google Workspace tool that
    isn't available to this assistant, else None.

    Prevents the model from confabulating a "privacy/security" refusal when the
    real cause is that the Gmail/Drive action simply isn't attached (or its
    connection failed to load). Gmail is never covered by internal_search, so a
    missing Gmail tool is always a hard miss; a missing Drive tool only matters
    when there is also no indexed (internal_search) fallback.
    """
    has_gmail = bool(available_tool_names & GMAIL_MCP_TOOL_NAMES)
    has_drive = bool(available_tool_names & DRIVE_MCP_TOOL_NAMES)

    needs_email = query_has_email_intent(query) and not has_gmail
    needs_drive = (
        query_has_drive_intent(query) and not has_drive and not has_internal_search
    )

    missing: list[str] = []
    if needs_email:
        missing.append("Gmail")
    if needs_drive:
        missing.append("Google Drive")
    if not missing:
        return None

    which = " and ".join(missing)
    return (
        f"SYSTEM: The user's request requires the {which} action, but it is NOT "
        f"available to you in this conversation. Do NOT refuse for privacy or "
        f"security reasons and do NOT pretend you accessed it. Tell the user "
        f"honestly that the {which} action is not currently enabled for this "
        f"assistant, and that they can enable it in Settings → Assistants → "
        f"Actions (or reconnect Google under Settings → Connectors). Keep the "
        f"reply short and actionable."
    )


class EmptyLLMResponseError(RuntimeError):
    """Raised when the streamed LLM response completes without a usable answer."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        tool_choice: ToolChoiceOptions,
        client_error_msg: str,
        error_code: str = "EMPTY_LLM_RESPONSE",
        is_retryable: bool = True,
    ) -> None:
        super().__init__(client_error_msg)
        self.provider = provider
        self.model = model
        self.tool_choice = tool_choice
        self.client_error_msg = client_error_msg
        self.error_code = error_code
        self.is_retryable = is_retryable


def _build_empty_llm_response_error(
    llm: LLM,
    llm_step_result: LlmStepResult,
    tool_choice: ToolChoiceOptions,
) -> EmptyLLMResponseError:
    provider = llm.config.model_provider
    model = llm.config.model_name

    # OpenAI quota exhaustion has reached us as a streamed "stop" with zero content.
    # When the stream is completely empty and there is no reasoning/tool output, surface
    # the likely account-level cause instead of a generic tool-calling error.
    if (
        not llm_step_result.reasoning
        and provider == LlmProviderNames.OPENAI
        and is_true_openai_model(provider, model)
    ):
        return EmptyLLMResponseError(
            provider=provider,
            model=model,
            tool_choice=tool_choice,
            client_error_msg=(
                "The selected OpenAI model returned an empty streamed response "
                "before producing any tokens. This commonly happens when the API "
                "key or project has no remaining quota or billing is not enabled. "
                "Verify quota and billing for this key and try again."
            ),
            error_code="BUDGET_EXCEEDED",
            is_retryable=False,
        )

    return EmptyLLMResponseError(
        provider=provider,
        model=model,
        tool_choice=tool_choice,
        client_error_msg=(
            "The selected model returned no final answer before the stream "
            "completed. No text or tool calls were received from the upstream "
            "provider."
        ),
    )


def _parse_nested_json(value: Any) -> Any:
    """Parse JSON strings nested inside custom MCP tool responses."""
    parsed = value
    for _ in range(3):
        if not isinstance(parsed, str):
            break
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            break
    return parsed


def _extract_custom_tool_payload(tool_response: ToolResponse) -> tuple[str | None, Any]:
    if tool_response.tool_call:
        tool_name = tool_response.tool_call.tool_name
    elif isinstance(tool_response.rich_response, CustomToolCallSummary):
        tool_name = tool_response.rich_response.tool_name
    else:
        tool_name = None

    if isinstance(tool_response.rich_response, CustomToolCallSummary):
        payload = tool_response.rich_response.tool_result
    else:
        payload = tool_response.llm_facing_response

    payload = _parse_nested_json(payload)
    if isinstance(payload, dict) and "tool_result" in payload:
        payload = _parse_nested_json(payload["tool_result"])

    return tool_name, payload


def _build_recent_email_fallback_answer(
    tool_name: str | None,
    payload: Any,
) -> str | None:
    if tool_name not in {"get_recent_emails", "search_gmail", "search_emails"}:
        return None

    if not isinstance(payload, dict):
        return None

    if error := payload.get("error"):
        return (
            "I could not retrieve your recent emails.\n\n"
            f"Reason: {error}\n\n"
            "The email MCP tool returned this error directly."
        )

    results = payload.get("results") or payload.get("messages") or payload.get("emails")
    if not results:
        return "No recent emails were found in your inbox."

    lines = ["Here are your recent emails:"]
    for index, item in enumerate(results[:10], start=1):
        if not isinstance(item, dict):
            continue
        subject = (
            item.get("subject")
            or item.get("title")
            or item.get("semantic_identifier")
            or "(no subject)"
        )
        sender = item.get("sender") or item.get("from") or item.get("from_email")
        received_at = item.get("received_at") or item.get("date") or item.get("timestamp")
        snippet = item.get("snippet") or item.get("summary") or item.get("content")

        detail_parts = []
        if sender:
            detail_parts.append(f"from {sender}")
        if received_at:
            detail_parts.append(str(received_at))

        detail = f" ({', '.join(detail_parts)})" if detail_parts else ""
        lines.append(f"{index}. {subject}{detail}")
        if snippet:
            lines.append(f"   {str(snippet)[:220]}")

    lines.append(
        "\nNote: the selected model returned an empty final answer after the "
        "mail tool completed, so I used the tool result directly."
    )
    return "\n".join(lines)


_DRIVE_TOOL_NAMES = {
    "list_recent_files",
    "search_drive_files",
    "search_google_drive",
    "search_drive_content",
    "list_folder_contents",
    "list_shared_drives",
}


def _build_drive_fallback_answer(
    tool_name: str | None,
    payload: Any,
) -> str | None:
    if tool_name not in _DRIVE_TOOL_NAMES:
        logger.debug(f"Drive fallback: tool_name '{tool_name}' not in DRIVE_TOOL_NAMES")
        return None

    if not isinstance(payload, dict):
        logger.info(f"Drive fallback: payload is not dict, type={type(payload).__name__}")
        return None
    logger.info(f"Drive fallback: payload keys={list(payload.keys())[:10]}")

    if error := payload.get("error"):
        return f"The Google Drive tool ran, but it returned an error: {error}"

    results = payload.get("results") or payload.get("files")
    if not isinstance(results, list):
        return None

    if not results:
        return "I checked Google Drive and did not find any files matching the request."

    lines = ["Here are the Google Drive files found:", ""]
    for index, result in enumerate(results[:15], start=1):
        if not isinstance(result, dict):
            continue
        title = str(
            result.get("title")
            or result.get("name")
            or result.get("fileName")
            or "Untitled"
        )
        mime_type = str(result.get("mimeType") or result.get("type") or "").strip()
        modified = str(
            result.get("modifiedTime")
            or result.get("timestamp")
            or result.get("updated_at")
            or ""
        ).strip()
        link = str(
            result.get("link")
            or result.get("webViewLink")
            or result.get("url")
            or ""
        ).strip()

        metadata_parts: list[str] = []
        if mime_type:
            metadata_parts.append(mime_type.split(".")[-1])
        if modified:
            metadata_parts.append(modified)
        metadata = ", ".join(metadata_parts)
        prefix = f"{index}. {title}"
        lines.append(f"{prefix} ({metadata})" if metadata else prefix)
        if link:
            lines.append(f"   Link: {link}")

    lines.append(
        "\nNote: the selected model returned an empty final answer after the "
        "Drive tool completed, so I used the tool result directly."
    )
    return "\n".join(lines)


_AUTH_ERROR_SIGNALS = frozenset({
    "authentication", "credential", "expired", "revoked",
    "401", "unauthorized", "reconnect", "invalid_grant",
})


def _detect_auth_error_in_tool_responses(
    tool_responses: list[ToolResponse],
) -> str | None:
    """If any tool response indicates a Google auth error, return a user-facing message.

    Returns None if no auth error is detected.
    """
    for tr in tool_responses:
        _, payload = _extract_custom_tool_payload(tr)
        if not isinstance(payload, dict):
            continue

        if payload.get("auth_error"):
            return payload.get("user_action") or (
                "Your Google credentials have expired. "
                "Please reconnect in Settings → Connectors → Google Drive."
            )

        error_msg = str(payload.get("error", "")).lower()
        if error_msg and any(kw in error_msg for kw in _AUTH_ERROR_SIGNALS):
            return (
                "Your Google credentials have expired or been revoked. "
                "Please go to Settings → Connectors → Google Drive and "
                "reconnect your Google account. "
                "Retrying this request will not work until you re-authenticate."
            )

    return None


def _build_tool_response_fallback_answer(
    tool_responses: list[ToolResponse],
) -> str | None:
    if not tool_responses:
        return None

    # Check for auth errors first — give a clear, actionable message
    if auth_msg := _detect_auth_error_in_tool_responses(tool_responses):
        return auth_msg

    tool_name, payload = _extract_custom_tool_payload(tool_responses[-1])
    if email_answer := _build_recent_email_fallback_answer(tool_name, payload):
        return email_answer

    if drive_answer := _build_drive_fallback_answer(tool_name, payload):
        return drive_answer

    if isinstance(payload, dict) and payload.get("error"):
        return f"The tool call failed: {payload['error']}"

    if isinstance(payload, (dict, list)):
        rendered_payload = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        rendered_payload = str(payload)

    return (
        "The tool completed successfully, but the selected model returned an "
        "empty final answer. Here is the tool result:\n\n"
        f"{rendered_payload[:2000]}"
    )


def _looks_like_xml_tool_call_payload(text: str | None) -> bool:
    """Detect XML-style marshaled tool calls emitted as plain text."""
    if not text:
        return False
    lowered = text.lower()
    return (
        "<function_calls" in lowered
        and "<invoke" in lowered
        and "<parameter" in lowered
    )


def _try_fallback_tool_extraction(
    llm_step_result: LlmStepResult,
    tool_choice: ToolChoiceOptions,
    fallback_extraction_attempted: bool,
    tool_defs: list[dict],
    turn_index: int,
) -> tuple[LlmStepResult, bool]:
    """Attempt to extract tool calls from response text as a fallback.

    This is a last resort fallback for low quality LLMs or those that don't have
    tool calling from the serving layer. Also triggers if there's reasoning but
    no answer and no tool calls.

    Args:
        llm_step_result: The result from the LLM step
        tool_choice: The tool choice option used for this step
        fallback_extraction_attempted: Whether fallback extraction was already attempted
        tool_defs: List of tool definitions
        turn_index: The current turn index for placement

    Returns:
        Tuple of (possibly updated LlmStepResult, whether fallback was attempted this call)
    """
    if fallback_extraction_attempted:
        return llm_step_result, False

    no_tool_calls = (
        not llm_step_result.tool_calls or len(llm_step_result.tool_calls) == 0
    )
    reasoning_but_no_answer_or_tools = (
        llm_step_result.reasoning and not llm_step_result.answer and no_tool_calls
    )
    xml_tool_call_text_detected = no_tool_calls and (
        _looks_like_xml_tool_call_payload(llm_step_result.answer)
        or _looks_like_xml_tool_call_payload(llm_step_result.raw_answer)
        or _looks_like_xml_tool_call_payload(llm_step_result.reasoning)
    )
    malformed_tool_call_text_detected = no_tool_calls and (
        looks_like_malformed_tool_call_payload(
            llm_step_result.answer,
            tool_defs,
        )
        or looks_like_malformed_tool_call_payload(
            llm_step_result.raw_answer,
            tool_defs,
        )
        or looks_like_malformed_tool_call_payload(
            llm_step_result.reasoning,
            tool_defs,
        )
    )
    should_try_fallback = (
        (tool_choice == ToolChoiceOptions.REQUIRED and no_tool_calls)
        or reasoning_but_no_answer_or_tools
        or xml_tool_call_text_detected
        or malformed_tool_call_text_detected
    )

    if not should_try_fallback:
        return llm_step_result, False

    # Try to extract from answer first, then fall back to reasoning
    extracted_tool_calls: list[ToolCallKickoff] = []

    if llm_step_result.answer:
        extracted_tool_calls = extract_tool_calls_from_response_text(
            response_text=llm_step_result.answer,
            tool_definitions=tool_defs,
            placement=Placement(turn_index=turn_index),
        )
    if (
        not extracted_tool_calls
        and llm_step_result.raw_answer
        and llm_step_result.raw_answer != llm_step_result.answer
    ):
        extracted_tool_calls = extract_tool_calls_from_response_text(
            response_text=llm_step_result.raw_answer,
            tool_definitions=tool_defs,
            placement=Placement(turn_index=turn_index),
        )
    if not extracted_tool_calls and llm_step_result.reasoning:
        extracted_tool_calls = extract_tool_calls_from_response_text(
            response_text=llm_step_result.reasoning,
            tool_definitions=tool_defs,
            placement=Placement(turn_index=turn_index),
        )
    if extracted_tool_calls:
        sanitized_answer = strip_tool_call_payload_text(
            response_text=llm_step_result.answer,
            tool_definitions=tool_defs,
        )
        sanitized_raw_answer = strip_tool_call_payload_text(
            response_text=llm_step_result.raw_answer,
            tool_definitions=tool_defs,
        )
        logger.info(
            f"Extracted {len(extracted_tool_calls)} tool call(s) from response text as fallback"
        )
        return (
            LlmStepResult(
                reasoning=llm_step_result.reasoning,
                answer=sanitized_answer,
                tool_calls=extracted_tool_calls,
                raw_answer=sanitized_raw_answer,
            ),
            True,
        )

    return llm_step_result, True


# Hardcoded oppinionated value, might breaks down to something like:
# Cycle 1: Calls web_search for something
# Cycle 2: Calls open_url for some results
# Cycle 3: Calls web_search for some other aspect of the question
# Cycle 4: Calls open_url for some results
# Cycle 5: Maybe call open_url for some additional results or because last set failed
# Cycle 6: No more tools available, forced to answer
MAX_LLM_CYCLES = 6


def _build_context_file_citation_mapping(
    file_metadata: list[ContextFileMetadata],
    starting_citation_num: int = 1,
) -> CitationMapping:
    """Build citation mapping for context files.

    Converts context file metadata into SearchDoc objects that can be cited.
    Citation numbers start from the provided starting number.

    Args:
        file_metadata: List of context file metadata
        starting_citation_num: Starting citation number (default: 1)

    Returns:
        Dictionary mapping citation numbers to SearchDoc objects
    """
    citation_mapping: CitationMapping = {}

    for idx, file_meta in enumerate(file_metadata, start=starting_citation_num):
        search_doc = SearchDoc(
            document_id=file_meta.file_id,
            chunk_ind=0,
            semantic_identifier=file_meta.filename,
            link=None,
            blurb=file_meta.file_content,
            source_type=DocumentSource.FILE,
            boost=1,
            hidden=False,
            metadata={},
            score=0.0,
            match_highlights=[file_meta.file_content],
        )
        citation_mapping[idx] = search_doc

    return citation_mapping


def _build_project_message(
    context_files: ExtractedContextFiles | None,
    token_counter: Callable[[str], int] | None,
) -> list[ChatMessageSimple]:
    """Build messages for context-injected / tool-backed files.

    Returns up to two messages:
    1. The full-text files message (if file_texts is populated).
    2. A lightweight metadata message for files the LLM should access via the
       FileReaderTool (e.g. oversized files that don't fit in context).
    """
    if not context_files:
        return []

    messages: list[ChatMessageSimple] = []
    if context_files.file_texts:
        messages.append(
            _create_context_files_message(context_files, token_counter=None)
        )
    if context_files.file_metadata_for_tool and token_counter:
        messages.append(
            _create_file_tool_metadata_message(
                context_files.file_metadata_for_tool, token_counter
            )
        )
    return messages


def construct_message_history(
    system_prompt: ChatMessageSimple | None,
    custom_agent_prompt: ChatMessageSimple | None,
    simple_chat_history: list[ChatMessageSimple],
    reminder_message: ChatMessageSimple | None,
    context_files: ExtractedContextFiles | None,
    available_tokens: int,
    last_n_user_messages: int | None = None,
    token_counter: Callable[[str], int] | None = None,
    all_injected_file_metadata: dict[str, FileToolMetadata] | None = None,
) -> list[ChatMessageSimple]:
    if last_n_user_messages is not None:
        if last_n_user_messages <= 0:
            raise ValueError(
                "filtering chat history by last N user messages must be a value greater than 0"
            )

    # Build the project / file-metadata messages up front so we can use their
    # actual token counts for the budget.
    project_messages = _build_project_message(context_files, token_counter)
    project_messages_tokens = sum(m.token_count for m in project_messages)

    history_token_budget = available_tokens
    history_token_budget -= system_prompt.token_count if system_prompt else 0
    history_token_budget -= (
        custom_agent_prompt.token_count if custom_agent_prompt else 0
    )
    history_token_budget -= project_messages_tokens
    history_token_budget -= reminder_message.token_count if reminder_message else 0

    if history_token_budget < 0:
        raise ValueError("Not enough tokens available to construct message history")

    if system_prompt:
        system_prompt.should_cache = True

    # If no history, build minimal context
    if not simple_chat_history:
        result = [system_prompt] if system_prompt else []
        if custom_agent_prompt:
            result.append(custom_agent_prompt)
        result.extend(project_messages)
        if reminder_message:
            result.append(reminder_message)
        return result

    # If last_n_user_messages is set, filter history to only include the last n user messages
    if last_n_user_messages is not None:
        # Find all user message indices
        user_msg_indices = [
            i
            for i, msg in enumerate(simple_chat_history)
            if msg.message_type == MessageType.USER
        ]

        if not user_msg_indices:
            raise ValueError("No user message found in simple_chat_history")

        # If we have more than n user messages, keep only the last n
        if len(user_msg_indices) > last_n_user_messages:
            # Find the index of the n-th user message from the end
            # For example, if last_n_user_messages=2, we want the 2nd-to-last user message
            nth_user_msg_index = user_msg_indices[-(last_n_user_messages)]
            # Keep everything from that user message onwards
            simple_chat_history = simple_chat_history[nth_user_msg_index:]

    # Find the last USER message in the history
    # The history may contain tool calls and responses after the last user message
    last_user_msg_index = None
    for i in range(len(simple_chat_history) - 1, -1, -1):
        if simple_chat_history[i].message_type == MessageType.USER:
            last_user_msg_index = i
            break

    if last_user_msg_index is None:
        raise ValueError("No user message found in simple_chat_history")

    # Split history into three parts:
    # 1. History before the last user message
    # 2. The last user message
    # 3. Messages after the last user message (tool calls, responses, etc.)
    history_before_last_user = simple_chat_history[:last_user_msg_index]
    last_user_message = simple_chat_history[last_user_msg_index]
    messages_after_last_user = simple_chat_history[last_user_msg_index + 1 :]

    # Calculate tokens needed for the last user message and everything after it
    last_user_tokens = last_user_message.token_count
    after_user_tokens = sum(msg.token_count for msg in messages_after_last_user)

    # Check if we can fit at least the last user message and messages after it
    required_tokens = last_user_tokens + after_user_tokens
    if required_tokens > history_token_budget:
        raise ValueError(
            f"Not enough tokens to include the last user message and subsequent messages. "
            f"Required: {required_tokens}, Available: {history_token_budget}"
        )

    # Calculate remaining budget for history before the last user message
    remaining_budget = history_token_budget - required_tokens

    # Truncate history_before_last_user from the top to fit in remaining budget.
    # Track dropped file messages so we can provide their metadata to the
    # FileReaderTool instead.
    truncated_history_before: list[ChatMessageSimple] = []
    dropped_file_ids: list[str] = []
    current_token_count = 0

    for msg in reversed(history_before_last_user):
        if current_token_count + msg.token_count <= remaining_budget:
            msg.should_cache = True
            truncated_history_before.insert(0, msg)
            current_token_count += msg.token_count
        else:
            # Can't fit this message, stop truncating.
            # This message and everything older is dropped.
            break

    # Collect file_ids from ALL dropped messages (those not in
    # truncated_history_before). The truncation loop above keeps the most
    # recent messages, so the dropped ones are at the start of the original
    # list up to (len(history) - len(kept)).
    num_kept = len(truncated_history_before)
    for msg in history_before_last_user[: len(history_before_last_user) - num_kept]:
        if msg.file_id is not None:
            dropped_file_ids.append(msg.file_id)

    # Also treat "orphaned" metadata entries as dropped -- these are files
    # from messages removed by summary truncation (before convert_chat_history
    # ran), so no ChatMessageSimple was ever tagged with their file_id.
    if all_injected_file_metadata:
        surviving_file_ids = {
            msg.file_id for msg in simple_chat_history if msg.file_id is not None
        }
        for fid in all_injected_file_metadata:
            if fid not in surviving_file_ids and fid not in dropped_file_ids:
                dropped_file_ids.append(fid)

    # Build a forgotten-files metadata message if any file messages were
    # dropped AND we have metadata for them (meaning the FileReaderTool is
    # available). Reserve tokens for this message in the budget.
    forgotten_files_message: ChatMessageSimple | None = None
    if dropped_file_ids and all_injected_file_metadata and token_counter:
        forgotten_meta = [
            all_injected_file_metadata[fid]
            for fid in dropped_file_ids
            if fid in all_injected_file_metadata
        ]
        if forgotten_meta:
            logger.debug(
                f"FileReader: building forgotten-files message for {[(m.file_id, m.filename) for m in forgotten_meta]}"
            )
            forgotten_files_message = _create_file_tool_metadata_message(
                forgotten_meta, token_counter
            )
            # Shrink the remaining budget. If the metadata message doesn't
            # fit we may need to drop more history messages.
            remaining_budget -= forgotten_files_message.token_count
            while truncated_history_before and current_token_count > remaining_budget:
                evicted = truncated_history_before.pop(0)
                current_token_count -= evicted.token_count
                # If the evicted message is itself a file, add it to the
                # forgotten metadata (it's now dropped too).
                if (
                    evicted.file_id is not None
                    and evicted.file_id in all_injected_file_metadata
                    and evicted.file_id not in {m.file_id for m in forgotten_meta}
                ):
                    forgotten_meta.append(all_injected_file_metadata[evicted.file_id])
                    # Rebuild the message with the new entry
                    forgotten_files_message = _create_file_tool_metadata_message(
                        forgotten_meta, token_counter
                    )

    # Attach project images to the last user message
    if context_files and context_files.image_files:
        existing_images = last_user_message.image_files or []
        last_user_message = ChatMessageSimple(
            message=last_user_message.message,
            token_count=last_user_message.token_count,
            message_type=last_user_message.message_type,
            image_files=existing_images + context_files.image_files,
        )

    # Build the final message list according to README ordering:
    # [system], [history_before_last_user], [custom_agent], [context_files],
    # [forgotten_files], [last_user_message], [messages_after_last_user], [reminder]
    result = [system_prompt] if system_prompt else []

    # 1. Add truncated history before last user message
    result.extend(truncated_history_before)

    # 2. Add custom agent prompt (inserted before last user message)
    if custom_agent_prompt:
        result.append(custom_agent_prompt)

    # 3. Add context files / file-metadata messages (inserted before last user message)
    result.extend(project_messages)

    # 4. Add forgotten-files metadata (right before the user's question)
    if forgotten_files_message:
        result.append(forgotten_files_message)

    # 5. Add last user message (with context images attached)
    result.append(last_user_message)

    # 6. Add messages after last user message (tool calls, responses, etc.)
    result.extend(messages_after_last_user)

    # 7. Add reminder message at the very end
    if reminder_message:
        result.append(reminder_message)

    return _drop_orphaned_tool_call_responses(result)


def _drop_orphaned_tool_call_responses(
    messages: list[ChatMessageSimple],
) -> list[ChatMessageSimple]:
    """Drop tool response messages whose tool_call_id is not in prior assistant tool calls.

    This can happen when history truncation drops an ASSISTANT tool-call message but
    leaves a later TOOL_CALL_RESPONSE message in context. Some providers (e.g. Ollama)
    reject such history with an "unexpected tool call id" error.
    """
    known_tool_call_ids: set[str] = set()
    sanitized: list[ChatMessageSimple] = []

    for msg in messages:
        if msg.message_type == MessageType.ASSISTANT and msg.tool_calls:
            for tool_call in msg.tool_calls:
                known_tool_call_ids.add(tool_call.tool_call_id)
            sanitized.append(msg)
            continue

        if msg.message_type == MessageType.TOOL_CALL_RESPONSE:
            if msg.tool_call_id and msg.tool_call_id in known_tool_call_ids:
                sanitized.append(msg)
            else:
                logger.debug(
                    "Dropping orphaned tool response with tool_call_id=%s while constructing message history",
                    msg.tool_call_id,
                )
            continue

        sanitized.append(msg)

    return sanitized


def _create_file_tool_metadata_message(
    file_metadata: list[FileToolMetadata],
    token_counter: Callable[[str], int],
) -> ChatMessageSimple:
    """Build a lightweight metadata-only message listing files available via FileReaderTool.

    Used when files are too large to fit in context and the vector DB is
    disabled, so the LLM must use ``read_file`` to inspect them.
    """
    lines = [
        "You have access to the following files. Use the read_file tool to "
        "read sections of any file. You MUST pass the file_id UUID (not the "
        "filename) to read_file:"
    ]
    for meta in file_metadata:
        lines.append(
            f'- file_id="{meta.file_id}" filename="{meta.filename}" (~{meta.approx_char_count:,} chars)'
        )

    message_content = "\n".join(lines)
    return ChatMessageSimple(
        message=message_content,
        token_count=token_counter(message_content),
        message_type=MessageType.USER,
    )


def _create_context_files_message(
    context_files: ExtractedContextFiles,
    token_counter: Callable[[str], int] | None,  # noqa: ARG001
) -> ChatMessageSimple:
    """Convert context files to a ChatMessageSimple message.

    Format follows the README specification for document representation.
    """
    import json

    # Format as documents JSON as described in README
    documents_list = []
    for idx, file_text in enumerate(context_files.file_texts, start=1):
        title = (
            context_files.file_metadata[idx - 1].filename
            if idx - 1 < len(context_files.file_metadata)
            else None
        )
        entry: dict[str, Any] = {"document": idx}
        if title:
            entry["title"] = title
        entry["contents"] = file_text
        documents_list.append(entry)

    documents_json = json.dumps({"documents": documents_list}, indent=2)
    message_content = f"Here are some documents provided for context, they may not all be relevant:\n{documents_json}"

    # Use pre-calculated token count from context_files
    return ChatMessageSimple(
        message=message_content,
        token_count=context_files.total_token_count,
        message_type=MessageType.USER,
    )


def run_llm_loop(
    emitter: Emitter,
    state_container: ChatStateContainer,
    simple_chat_history: list[ChatMessageSimple],
    tools: list[Tool],
    custom_agent_prompt: str | None,
    context_files: ExtractedContextFiles,
    persona: Persona | None,
    user_memory_context: UserMemoryContext | None,
    llm: LLM,
    token_counter: Callable[[str], int],
    db_session: Session,
    forced_tool_id: int | None = None,
    user_identity: LLMUserIdentity | None = None,
    chat_session_id: str | None = None,
    chat_files: list[ChatFile] | None = None,
    include_citations: bool = True,
    all_injected_file_metadata: dict[str, FileToolMetadata] | None = None,
    inject_memories_in_prompt: bool = True,
) -> None:
    with trace(
        "run_llm_loop",
        group_id=chat_session_id,
        metadata={
            "tenant_id": get_current_tenant_id(),
            "chat_session_id": chat_session_id,
        },
    ):
        # Fix some LiteLLM issues,
        from docubrain.llm.litellm_singleton.config import (
            initialize_litellm,
        )  # Here for lazy load LiteLLM

        initialize_litellm()

        # Track when the loop starts for calculating time-to-answer
        loop_start_time = time.monotonic()

        # Initialize citation processor for handling citations dynamically
        # When include_citations is True, use HYPERLINK mode to format citations as [[1]](url)
        # When include_citations is False, use REMOVE mode to strip citations from output
        # The global SHOW_CITATIONS setting can force citation-free answers
        # regardless of the per-request flag (REMOVE mode + no citation guidance).
        effective_include_citations = include_citations and SHOW_CITATIONS
        citation_processor = DynamicCitationProcessor(
            citation_mode=(
                CitationMode.HYPERLINK
                if effective_include_citations
                else CitationMode.REMOVE
            )
        )

        # Add project file citation mappings if project files are present
        project_citation_mapping: CitationMapping = {}
        if context_files.file_metadata:
            project_citation_mapping = _build_context_file_citation_mapping(
                context_files.file_metadata
            )
            citation_processor.update_citation_mapping(project_citation_mapping)

        llm_step_result = LlmStepResult(
            reasoning=None,
            answer=None,
            tool_calls=None,
            raw_answer=None,
        )

        # Pass the total budget to construct_message_history, which will handle token allocation
        available_tokens = llm.config.max_input_tokens
        tool_choice: ToolChoiceOptions = ToolChoiceOptions.AUTO
        # Initialize gathered_documents with project files if present
        gathered_documents: list[SearchDoc] | None = (
            list(project_citation_mapping.values())
            if project_citation_mapping
            else None
        )
        # TODO allow citing of images in Projects. Since attached to the last user message, it has no text associated with it.
        # One future workaround is to include the images as separate user messages with citation information and process those.
        always_cite_documents: bool = bool(
            context_files.use_as_search_filter or context_files.file_texts
        ) and effective_include_citations
        should_cite_documents: bool = False
        ran_image_gen: bool = False
        just_ran_web_search: bool = False
        has_called_search_tool: bool = False
        # Phase-4 KPI tracking: did this turn invoke ANY retrieval tool
        # (internal_search or a Drive/Gmail MCP tool), and which ones.
        retrieval_invoked: bool = False
        invoked_retrieval_tools: set[str] = set()
        code_interpreter_file_generated: bool = False
        fallback_extraction_attempted: bool = False
        citation_mapping: dict[int, str] = {}  # Maps citation_num -> document_id/URL
        last_successful_tool_responses: list[ToolResponse] = []
        auth_error_injected: bool = False

        # Fetch this in a short-lived session so the long-running stream loop does
        # not pin a connection just to keep read state alive.
        with get_session_with_current_tenant() as prompt_db_session:
            default_base_system_prompt: str = get_default_base_system_prompt(
                prompt_db_session
            )
        system_prompt = None
        custom_agent_prompt_msg = None
        skip_tools_for_initial_message = _should_skip_tools_for_initial_message(
            simple_chat_history=simple_chat_history,
            context_files=context_files,
            custom_agent_prompt=custom_agent_prompt,
        )
        retry_without_tools_after_malformed_payload = False

        # --- Retrieval Router: classify query before entering the loop ---
        routing_decision: RoutingDecision | None = None
        if not skip_tools_for_initial_message and tools:
            latest_query = _get_latest_user_message_text(simple_chat_history)
            if latest_query:
                available_tool_names = {t.name for t in tools}
                _google_ws_names = GMAIL_MCP_TOOL_NAMES | DRIVE_MCP_TOOL_NAMES
                has_google_workspace = bool(available_tool_names & _google_ws_names)
                has_internal_search = any(
                    isinstance(t, SearchTool) for t in tools
                )
                routing_decision = classify_and_route(
                    query=latest_query,
                    available_tool_names=available_tool_names,
                    has_google_workspace=has_google_workspace,
                    has_internal_search=has_internal_search,
                )

                # --- Honest missing-tool guard (anti-confabulation) ---
                # If the user clearly wants Gmail/Drive but the corresponding tool
                # is not attached to this assistant, the model otherwise tends to
                # invent a "privacy" refusal. Inject a system note so it answers
                # honestly and tells the user how to enable the action instead.
                missing_tool_msg = _build_missing_workspace_tool_message(
                    latest_query, available_tool_names, has_internal_search
                )
                if missing_tool_msg:
                    logger.info(
                        "llm_loop: workspace tool missing for query; injecting honest-failure hint"
                    )
                    simple_chat_history.append(
                        ChatMessageSimple(
                            message=missing_tool_msg,
                            token_count=token_counter(missing_tool_msg),
                            message_type=MessageType.SYSTEM,
                            image_files=None,
                        )
                    )

        reasoning_cycles = 0
        for llm_cycle_count in range(MAX_LLM_CYCLES):
            # Handling tool calls based on cycle count and past cycle conditions
            out_of_cycles = llm_cycle_count == MAX_LLM_CYCLES - 1
            if forced_tool_id:
                # Needs to be just the single one because the "required" currently doesn't have a specified tool, just a binary
                final_tools = [tool for tool in tools if tool.id == forced_tool_id]
                if not final_tools:
                    raise ValueError(f"Tool {forced_tool_id} not found in tools")
                tool_choice = ToolChoiceOptions.REQUIRED
                forced_tool_id = None
            elif out_of_cycles or ran_image_gen:
                # Last cycle, no tools allowed, just answer!
                tool_choice = ToolChoiceOptions.NONE
                final_tools = []
            elif llm_cycle_count == 0 and skip_tools_for_initial_message:
                tool_choice = ToolChoiceOptions.NONE
                final_tools = []
            elif retry_without_tools_after_malformed_payload:
                tool_choice = ToolChoiceOptions.NONE
                final_tools = []
                retry_without_tools_after_malformed_payload = False
            elif (
                llm_cycle_count == 0
                and routing_decision is not None
                and routing_decision.confidence >= 0.7
                and routing_decision.strategy != RoutingStrategy.LLM_DECIDES
            ):
                # High-confidence routing: prefer suggested tools, deprioritize others
                tool_choice = ToolChoiceOptions.AUTO
                blocked = set(routing_decision.blocked_tool_names)
                preferred = set(routing_decision.preferred_tool_names)
                if preferred:
                    # Put preferred tools first, keep others (except blocked)
                    preferred_tools = [t for t in tools if t.name in preferred]
                    other_tools = [
                        t for t in tools
                        if t.name not in preferred and t.name not in blocked
                    ]
                    final_tools = preferred_tools + other_tools
                else:
                    final_tools = [t for t in tools if t.name not in blocked]
                # Safety: never leave tools empty
                if not final_tools:
                    final_tools = tools
            else:
                tool_choice = ToolChoiceOptions.AUTO
                final_tools = tools

            # Handling the system prompt and custom agent prompt
            # The section below calculates the available tokens for history a bit more accurately
            # now that project files are loaded in.
            if persona and persona.replace_base_system_prompt:
                # Handles the case where user has checked off the "Replace base system prompt" checkbox
                system_prompt = (
                    ChatMessageSimple(
                        message=persona.system_prompt,
                        token_count=token_counter(persona.system_prompt),
                        message_type=MessageType.SYSTEM,
                    )
                    if persona.system_prompt
                    else None
                )
                custom_agent_prompt_msg = None
            else:
                # If it's an empty string, we assume the user does not want to include it as an empty System message
                if default_base_system_prompt:
                    prompt_memory_context = (
                        user_memory_context
                        if inject_memories_in_prompt
                        else (
                            user_memory_context.without_memories()
                            if user_memory_context
                            else None
                        )
                    )
                    system_prompt_str = build_system_prompt(
                        base_system_prompt=default_base_system_prompt,
                        datetime_aware=persona.datetime_aware if persona else True,
                        user_memory_context=prompt_memory_context,
                        tools=tools,
                        should_cite_documents=should_cite_documents
                        or always_cite_documents,
                        routing_hint=(
                            routing_decision
                            if llm_cycle_count == 0
                            else None
                        ),
                    )
                    system_prompt = ChatMessageSimple(
                        message=system_prompt_str,
                        token_count=token_counter(system_prompt_str),
                        message_type=MessageType.SYSTEM,
                    )
                    custom_agent_prompt_msg = (
                        ChatMessageSimple(
                            message=custom_agent_prompt,
                            token_count=token_counter(custom_agent_prompt),
                            message_type=MessageType.USER,
                        )
                        if custom_agent_prompt
                        else None
                    )
                else:
                    # If there is a custom agent prompt, it replaces the system prompt when the default system prompt is empty
                    system_prompt = (
                        ChatMessageSimple(
                            message=custom_agent_prompt,
                            token_count=token_counter(custom_agent_prompt),
                            message_type=MessageType.SYSTEM,
                        )
                        if custom_agent_prompt
                        else None
                    )
                    custom_agent_prompt_msg = None

            reminder_message_text: str | None
            if ran_image_gen:
                # Some models are trained to give back images to the user for some similar tool
                # This is to prevent it generating things like:
                # [Cute Cat](attachment://a_cute_cat_sitting_playfully.png)
                reminder_message_text = IMAGE_GEN_REMINDER
            elif just_ran_web_search and not out_of_cycles:
                reminder_message_text = OPEN_URL_REMINDER
            else:
                # This is the default case, the LLM at this point may answer so it is important
                # to include the reminder. Potentially this should also mention citation
                reminder_message_text = build_reminder_message(
                    reminder_text=(
                        persona.task_prompt if persona and persona.task_prompt else None
                    ),
                    include_citation_reminder=should_cite_documents
                    or always_cite_documents,
                    include_file_reminder=code_interpreter_file_generated,
                    is_last_cycle=out_of_cycles,
                )

            reminder_msg = (
                ChatMessageSimple(
                    message=reminder_message_text,
                    token_count=token_counter(reminder_message_text),
                    message_type=MessageType.USER_REMINDER,
                )
                if reminder_message_text
                else None
            )

            truncated_message_history = construct_message_history(
                system_prompt=system_prompt,
                custom_agent_prompt=custom_agent_prompt_msg,
                simple_chat_history=simple_chat_history,
                reminder_message=reminder_msg,
                context_files=context_files,
                available_tokens=available_tokens,
                token_counter=token_counter,
                all_injected_file_metadata=all_injected_file_metadata,
            )

            # This calls the LLM, yields packets (reasoning, answers, etc.) and returns the result
            # It also pre-processes the tool calls in preparation for running them
            tool_defs = [tool.tool_definition() for tool in final_tools]
            if llm_cycle_count == 0:
                logger.info(
                    f"run_llm_loop cycle 0: {len(tools)} total tools, "
                    f"{len(final_tools)} final_tools, {len(tool_defs)} tool_defs, "
                    f"skip_tools={skip_tools_for_initial_message}, "
                    f"tool_choice={tool_choice}"
                )

            # Calculate total processing time from loop start until now
            # This measures how long the user waits before the answer starts streaming
            pre_answer_processing_time = time.monotonic() - loop_start_time

            llm_step_result, has_reasoned = run_llm_step(
                emitter=emitter,
                history=truncated_message_history,
                tool_definitions=tool_defs,
                tool_choice=tool_choice,
                llm=llm,
                placement=Placement(turn_index=llm_cycle_count + reasoning_cycles),
                citation_processor=citation_processor,
                state_container=state_container,
                # The rich docs representation is passed in so that when yielding the answer, it can also
                # immediately yield the full set of found documents. This gives us the option to show the
                # final set of documents immediately if desired.
                final_documents=gathered_documents,
                user_identity=user_identity,
                pre_answer_processing_time=pre_answer_processing_time,
            )
            if has_reasoned:
                reasoning_cycles += 1

            # Fallback extraction for LLMs that don't support tool calling natively or are lower quality
            # and might incorrectly output tool calls in other channels
            llm_step_result, attempted = _try_fallback_tool_extraction(
                llm_step_result=llm_step_result,
                tool_choice=tool_choice,
                fallback_extraction_attempted=fallback_extraction_attempted,
                tool_defs=tool_defs,
                turn_index=llm_cycle_count + reasoning_cycles,
            )
            if attempted:
                # To prevent the case of excessive looping with bad models, we only allow one fallback attempt
                fallback_extraction_attempted = True

            malformed_tool_payload_detected = (
                tool_choice != ToolChoiceOptions.NONE
                and not llm_step_result.tool_calls
                and not llm_step_result.answer
                and (
                    looks_like_malformed_tool_call_payload(
                        llm_step_result.raw_answer,
                        tool_defs,
                    )
                    or looks_like_malformed_tool_call_payload(
                        llm_step_result.reasoning,
                        tool_defs,
                    )
                )
            )
            if (
                malformed_tool_payload_detected
                and llm_cycle_count < MAX_LLM_CYCLES - 1
            ):
                logger.warning(
                    "Detected malformed tool-call payload from model %s; retrying the turn without tools.",
                    llm.config.model_name,
                )
                retry_without_tools_after_malformed_payload = True
                continue

            # Save citation mapping after each LLM step for incremental state updates
            state_container.set_citation_mapping(citation_processor.citation_to_doc)

            # Post-generation answer-grounding verification (opt-in, observability
            # only). Runs AFTER the answer has fully streamed, so it NEVER mutates
            # the answer or the stream. Citation validity is LLM-free; the grounding
            # judge (extra LLM call) is gated separately. Fully wrapped so a verifier
            # failure can never break the chat loop. Gated by ENABLE_ANSWER_VERIFICATION.
            if ENABLE_ANSWER_VERIFICATION and llm_step_result.answer:
                try:
                    # Evidence is reconstructed inside verify_answer from the
                    # cited documents' fullest available content (full content →
                    # match highlights → blurb), so no evidence is passed here.
                    verification = verify_answer(
                        answer=llm_step_result.answer,
                        citation_mapping=citation_processor.citation_to_doc,
                        complete=(
                            make_llm_complete(llm)
                            if ENABLE_ANSWER_GROUNDING_JUDGE
                            else None
                        ),
                    )
                    logger.info(
                        "AnswerVerification: trustworthy=%s phantom_citations=%s "
                        "grounding_ran=%s grounding_score=%.2f unsupported_claims=%d",
                        verification.is_trustworthy,
                        verification.citation.invalid_numbers,
                        verification.grounding.ran,
                        verification.grounding.grounding_score,
                        len(verification.grounding.unsupported_claims),
                    )
                except Exception:
                    logger.warning(
                        "Answer verification failed; continuing without it.",
                        exc_info=True,
                    )

            # Run the LLM selected tools, there is some more logic here than a simple execution
            # each tool might have custom logic here
            tool_responses: list[ToolResponse] = []
            tool_calls = llm_step_result.tool_calls or []

            if INTEGRATION_TESTS_MODE and tool_calls:
                for tool_call in tool_calls:
                    emitter.emit(
                        Packet(
                            placement=tool_call.placement,
                            obj=ToolCallDebug(
                                tool_call_id=tool_call.tool_call_id,
                                tool_name=tool_call.tool_name,
                                tool_args=tool_call.tool_args,
                            ),
                        )
                    )

            if len(tool_calls) > 1:
                emitter.emit(
                    Packet(
                        placement=Placement(
                            turn_index=tool_calls[0].placement.turn_index
                        ),
                        obj=TopLevelBranching(num_parallel_branches=len(tool_calls)),
                    )
                )

            # Quick note for why citation_mapping and citation_processors are both needed:
            # 1. Tools return lightweight string mappings, not SearchDoc objects
            # 2. The SearchDoc resolution is deliberately deferred to llm_loop.py
            # 3. The citation_processor operates on SearchDoc objects and can't provide a complete reverse URL lookup for
            # in-flight citations
            # It can be cleaned up but not super trivial or worthwhile right now
            just_ran_web_search = False
            parallel_tool_call_results = run_tool_calls(
                tool_calls=tool_calls,
                tools=final_tools,
                message_history=truncated_message_history,
                user_memory_context=user_memory_context,
                user_info=None,  # TODO, this is part of memories right now, might want to separate it out
                citation_mapping=citation_mapping,
                next_citation_num=citation_processor.get_next_citation_number(),
                max_concurrent_tools=None,
                skip_search_query_expansion=has_called_search_tool,
                chat_files=chat_files,
                url_snippet_map=extract_url_snippet_map(gathered_documents or []),
                inject_memories_in_prompt=inject_memories_in_prompt,
            )
            tool_responses = parallel_tool_call_results.tool_responses
            if tool_responses:
                last_successful_tool_responses = tool_responses
            citation_mapping = parallel_tool_call_results.updated_citation_mapping

            # Failure case, give something reasonable to the LLM to try again
            if tool_calls and not tool_responses:
                failure_messages = create_tool_call_failure_messages(
                    tool_calls, token_counter
                )
                simple_chat_history.extend(failure_messages)
                continue

            for tool_response in tool_responses:
                # Extract tool_call from the response (set by run_tool_calls)
                if tool_response.tool_call is None:
                    raise ValueError("Tool response missing tool_call reference")

                tool_call = tool_response.tool_call
                tab_index = tool_call.placement.tab_index

                # Track if search tool was called (for skipping query expansion on subsequent calls)
                if tool_call.tool_name == SearchTool.NAME:
                    has_called_search_tool = True

                # Phase-4 KPI: record any retrieval-class tool invocation.
                if (
                    tool_call.tool_name == SearchTool.NAME
                    or tool_call.tool_name in GMAIL_MCP_TOOL_NAMES
                    or tool_call.tool_name in DRIVE_MCP_TOOL_NAMES
                ):
                    retrieval_invoked = True
                    invoked_retrieval_tools.add(tool_call.tool_name)

                # Track if code interpreter generated files with download links
                if (
                    tool_call.tool_name == PYTHON_TOOL_NAME
                    and not code_interpreter_file_generated
                ):
                    try:
                        parsed = json.loads(tool_response.llm_facing_response)
                        if parsed.get("generated_files"):
                            code_interpreter_file_generated = True
                    except (json.JSONDecodeError, AttributeError):
                        pass

                # Build a mapping of tool names to tool objects for getting tool_id
                tools_by_name = {tool.name: tool for tool in final_tools}

                # Add the results to the chat history. Even though tools may run in parallel,
                # LLM APIs require linear history, so results are added sequentially.
                # Get the tool object to retrieve tool_id
                tool = tools_by_name.get(tool_call.tool_name)
                if not tool:
                    raise ValueError(
                        f"Tool '{tool_call.tool_name}' not found in tools list"
                    )

                # Extract search_docs if this is a search tool response
                search_docs = None
                displayed_docs = None
                if isinstance(tool_response.rich_response, SearchDocsResponse):
                    search_docs = tool_response.rich_response.search_docs
                    displayed_docs = tool_response.rich_response.displayed_docs

                    # Add ALL search docs to state container for DB persistence
                    if search_docs:
                        state_container.add_search_docs(search_docs)

                    if gathered_documents:
                        gathered_documents.extend(search_docs)
                    else:
                        gathered_documents = search_docs

                    # This is used for the Open URL reminder in the next cycle
                    # only do this if the web search tool yielded results
                    if search_docs and tool_call.tool_name == INTERNET_SEARCH_TOOL_NAME:
                        just_ran_web_search = True

                generated_images = None

                # Extract generated_files if this is a code interpreter response
                generated_files = None
                if isinstance(tool_response.rich_response, PythonToolRichResponse):
                    generated_files = (
                        tool_response.rich_response.generated_files or None
                    )

                # Persist memory if this is a memory tool response
                memory_snapshot: MemoryToolResponseSnapshot | None = None
                if isinstance(tool_response.rich_response, MemoryToolResponse):
                    persisted_memory_id: int | None = None
                    if user_memory_context and user_memory_context.user_id:
                        if tool_response.rich_response.index_to_replace is not None:
                            memory = update_memory_at_index(
                                user_id=user_memory_context.user_id,
                                index=tool_response.rich_response.index_to_replace,
                                new_text=tool_response.rich_response.memory_text,
                                db_session=db_session,
                            )
                            persisted_memory_id = memory.id if memory else None
                        else:
                            memory = add_memory(
                                user_id=user_memory_context.user_id,
                                memory_text=tool_response.rich_response.memory_text,
                                db_session=db_session,
                            )
                            persisted_memory_id = memory.id
                    operation: Literal["add", "update"] = (
                        "update"
                        if tool_response.rich_response.index_to_replace is not None
                        else "add"
                    )
                    memory_snapshot = MemoryToolResponseSnapshot(
                        memory_text=tool_response.rich_response.memory_text,
                        operation=operation,
                        memory_id=persisted_memory_id,
                        index=tool_response.rich_response.index_to_replace,
                    )

                if memory_snapshot:
                    saved_response = json.dumps(memory_snapshot.model_dump())
                elif isinstance(tool_response.rich_response, CustomToolCallSummary):
                    saved_response = json.dumps(
                        tool_response.rich_response.model_dump()
                    )
                elif isinstance(tool_response.rich_response, str):
                    saved_response = tool_response.rich_response
                else:
                    saved_response = tool_response.llm_facing_response

                tool_call_info = ToolCallInfo(
                    parent_tool_call_id=None,  # Top-level tool calls are attached to the chat message
                    turn_index=llm_cycle_count + reasoning_cycles,
                    tab_index=tab_index,
                    tool_name=tool_call.tool_name,
                    tool_call_id=tool_call.tool_call_id,
                    tool_id=tool.id,
                    reasoning_tokens=llm_step_result.reasoning,  # All tool calls from this loop share the same reasoning
                    tool_call_arguments=tool_call.tool_args,
                    tool_call_response=saved_response,
                    search_docs=displayed_docs or search_docs,
                    generated_images=generated_images,
                    generated_files=generated_files,
                )
                # Add to state container for partial save support
                state_container.add_tool_call(tool_call_info)

                # Update citation processor if this was a search tool
                update_citation_processor_from_tool_response(
                    tool_response, citation_processor
                )

            # After processing all tool responses for this turn, add messages to history
            # using OpenAI parallel tool calling format:
            # 1. ONE ASSISTANT message with tool_calls array
            # 2. N TOOL_CALL_RESPONSE messages (one per tool call)
            if tool_responses:
                # Filter to only responses with valid tool_call references
                valid_tool_responses = [
                    tr for tr in tool_responses if tr.tool_call is not None
                ]

                # Build ToolCallSimple list for all tool calls in this turn
                tool_calls_simple: list[ToolCallSimple] = []
                for tool_response in valid_tool_responses:
                    tc = tool_response.tool_call
                    assert (
                        tc is not None
                    )  # Already filtered above, this is just for typing purposes

                    tool_call_message = tc.to_msg_str()
                    tool_call_token_count = token_counter(tool_call_message)

                    tool_calls_simple.append(
                        ToolCallSimple(
                            tool_call_id=tc.tool_call_id,
                            tool_name=tc.tool_name,
                            tool_arguments=tc.tool_args,
                            token_count=tool_call_token_count,
                        )
                    )

                # Create ONE ASSISTANT message with all tool calls for this turn
                total_tool_call_tokens = sum(tc.token_count for tc in tool_calls_simple)
                assistant_with_tools = ChatMessageSimple(
                    message="",  # No text content when making tool calls
                    token_count=total_tool_call_tokens,
                    message_type=MessageType.ASSISTANT,
                    tool_calls=tool_calls_simple,
                    image_files=None,
                )
                simple_chat_history.append(assistant_with_tools)

                # Add TOOL_CALL_RESPONSE messages for each tool call
                for tool_response in valid_tool_responses:
                    tc = tool_response.tool_call
                    assert tc is not None  # Already filtered above

                    tool_response_message = tool_response.llm_facing_response
                    tool_response_token_count = token_counter(tool_response_message)

                    tool_response_msg = ChatMessageSimple(
                        message=tool_response_message,
                        token_count=tool_response_token_count,
                        message_type=MessageType.TOOL_CALL_RESPONSE,
                        tool_call_id=tc.tool_call_id,
                        image_files=None,
                    )
                    simple_chat_history.append(tool_response_msg)

            # --- Auth error: stop retrying, tell user to re-authenticate ---
            if tool_responses and not auth_error_injected:
                auth_error_msg = _detect_auth_error_in_tool_responses(tool_responses)
                if auth_error_msg:
                    logger.warning(
                        "LLM loop: Google auth error detected, injecting re-auth hint"
                    )
                    auth_error_injected = True
                    auth_hint = ChatMessageSimple(
                        message=(
                            "SYSTEM: The Google Workspace tool returned an authentication error. "
                            "Do NOT retry the same tool call — it will fail again. "
                            "Instead, tell the user clearly: " + auth_error_msg
                        ),
                        token_count=token_counter(auth_error_msg) + 30,
                        message_type=MessageType.SYSTEM,
                        image_files=None,
                    )
                    simple_chat_history.append(auth_hint)
                    # Let the LLM generate a final answer with the auth hint
                    continue

            # --- Retrieval Router: fallback if primary returned empty ---
            if (
                routing_decision is not None
                and routing_decision.strategy == RoutingStrategy.FALLBACK_CHAIN
                and routing_decision.fallback_targets
                and tool_responses
                and tool_responses_are_empty(tool_responses)
            ):
                fallback = build_fallback_decision(routing_decision)
                if fallback is not None:
                    logger.info(
                        "RetrievalRouter: primary returned empty, switching to fallback targets=%s",
                        [t.value for t in fallback.primary_targets],
                    )
                    routing_decision = fallback
                    # Don't break — continue to next cycle with updated routing
                    continue

            # If no tool calls, then it must have answered, wrap up
            if not llm_step_result.tool_calls or len(llm_step_result.tool_calls) == 0:
                break

            # Certain tools do not allow further actions, force the LLM wrap up on the next cycle
            if any(
                tool.tool_name in STOPPING_TOOLS_NAMES
                for tool in llm_step_result.tool_calls
            ):
                ran_image_gen = True

            if (
                effective_include_citations
                and llm_step_result.tool_calls
                and any(
                    tool.tool_name in CITEABLE_TOOLS_NAMES
                    for tool in llm_step_result.tool_calls
                )
            ):
                # As long as 1 tool with citeable documents is called at any point, we ask the LLM to try to cite
                should_cite_documents = True

        logger.info(
            f"Loop ended: answer={'yes' if llm_step_result.answer else 'no'}, "
            f"tool_calls={len(llm_step_result.tool_calls) if llm_step_result.tool_calls else 0}, "
            f"last_tool_responses={len(last_successful_tool_responses)}"
        )
        if not llm_step_result.answer and not llm_step_result.tool_calls:
            logger.info("Entering fallback path for empty answer")
            fallback_answer = _build_tool_response_fallback_answer(
                last_successful_tool_responses
            )
            if fallback_answer:
                llm_step_result.answer = fallback_answer
                state_container.set_answer_tokens(fallback_answer)
                fallback_placement = Placement(
                    turn_index=llm_cycle_count + reasoning_cycles
                )
                emitter.emit(
                    Packet(
                        placement=fallback_placement,
                        obj=AgentResponseStart(
                            final_documents=gathered_documents,
                            pre_answer_processing_seconds=time.monotonic()
                            - loop_start_time,
                        ),
                    )
                )
                emitter.emit(
                    Packet(
                        placement=fallback_placement,
                        obj=AgentResponseDelta(content=fallback_answer),
                    )
                )
            else:
                raise _build_empty_llm_response_error(
                    llm=llm,
                    llm_step_result=llm_step_result,
                    tool_choice=tool_choice,
                )

        if not llm_step_result.answer:
            raise RuntimeError(
                "The LLM did not return a final answer after tool execution. "
                "Typically this indicates invalid tool-call output, a model/provider mismatch, "
                "or serving API misconfiguration."
            )

        # Phase-4 KPI: one authoritative per-turn retrieval trace (written even
        # when retrieval never fired) so invocation rate is measurable in prod.
        if ENABLE_RETRIEVAL_TRACING:
            latest_query = _get_latest_user_message_text(simple_chat_history)
            if latest_query:
                record_query_trace(
                    query=latest_query,
                    search_invoked=retrieval_invoked,
                    retrieval_tool_names=sorted(invoked_retrieval_tools),
                    answered=bool(llm_step_result.answer),
                    final_answer=llm_step_result.answer,
                    latency_ms=(time.monotonic() - loop_start_time) * 1000.0,
                    chat_session_id=chat_session_id,
                )

        emitter.emit(
            Packet(
                placement=Placement(turn_index=llm_cycle_count + reasoning_cycles),
                obj=OverallStop(type="stop"),
            )
        )
