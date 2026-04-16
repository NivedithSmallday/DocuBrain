import functools
from collections.abc import Callable
from typing import Any
from typing import Optional
from typing import TypeVar

from retry import retry
from slack_sdk import WebClient

from docubrain.auth.users import get_anonymous_user
from docubrain.chat.models import ChatBasicResponse
from docubrain.chat.process_message import gather_stream
from docubrain.chat.process_message import handle_stream_message_objects
from docubrain.configs.constants import DEFAULT_PERSONA_ID
from docubrain.configs.constants import MessageType
from docubrain.configs.docubrainbot_configs import DOCUBRAIN_BOT_DISABLE_DOCS_ONLY_ANSWER
from docubrain.configs.docubrainbot_configs import DOCUBRAIN_BOT_DISPLAY_ERROR_MSGS
from docubrain.configs.docubrainbot_configs import DOCUBRAIN_BOT_NUM_RETRIES
from docubrain.configs.docubrainbot_configs import DOCUBRAIN_BOT_REACT_EMOJI
from docubrain.context.search.models import BaseFilters
from docubrain.context.search.models import Tag
from docubrain.db.engine.sql_engine import get_session_with_current_tenant
from docubrain.db.models import SlackChannelConfig
from docubrain.db.models import User
from docubrain.db.persona import get_persona_by_id
from docubrain.db.users import get_user_by_email
from docubrain.docubrainbot.slack.blocks import build_slack_response_blocks
from docubrain.docubrainbot.slack.constants import SLACK_CHANNEL_REF_PATTERN
from docubrain.docubrainbot.slack.handlers.utils import send_team_member_message
from docubrain.docubrainbot.slack.models import SlackMessageInfo
from docubrain.docubrainbot.slack.models import ThreadMessage
from docubrain.docubrainbot.slack.utils import get_channel_from_id
from docubrain.docubrainbot.slack.utils import get_channel_name_from_id
from docubrain.docubrainbot.slack.utils import respond_in_thread_or_channel
from docubrain.docubrainbot.slack.utils import SlackRateLimiter
from docubrain.docubrainbot.slack.utils import update_emote_react
from docubrain.server.query_and_chat.models import ChatSessionCreationRequest
from docubrain.server.query_and_chat.models import MessageOrigin
from docubrain.server.query_and_chat.models import SendMessageRequest
from docubrain.utils.logger import DocubrainLoggingAdapter

srl = SlackRateLimiter()

RT = TypeVar("RT")  # return type


def resolve_channel_references(
    message: str,
    client: WebClient,
    logger: DocubrainLoggingAdapter,
) -> tuple[str, list[Tag]]:
    """Parse Slack channel references from a message, resolve IDs to names,
    replace the raw markup with readable #channel-name, and return channel tags
    for search filtering."""
    tags: list[Tag] = []
    channel_matches = SLACK_CHANNEL_REF_PATTERN.findall(message)
    seen_channel_ids: set[str] = set()

    for channel_id, channel_name_from_markup in channel_matches:
        if channel_id in seen_channel_ids:
            continue
        seen_channel_ids.add(channel_id)

        channel_name = channel_name_from_markup or None

        if not channel_name:
            try:
                channel_info = get_channel_from_id(client=client, channel_id=channel_id)
                channel_name = channel_info.get("name") or None
            except Exception:
                logger.warning(f"Failed to resolve channel name for ID: {channel_id}")

            if not channel_name:
                continue

        # Replace raw Slack markup with readable channel name
        if channel_name_from_markup:
            message = message.replace(
                f"<#{channel_id}|{channel_name_from_markup}>",
                f"#{channel_name}",
            )
        else:
            message = message.replace(
                f"<#{channel_id}>",
                f"#{channel_name}",
            )
        tags.append(Tag(tag_key="Channel", tag_value=channel_name))

    return message, tags


def rate_limits(
    client: WebClient, channel: str, thread_ts: Optional[str]
) -> Callable[[Callable[..., RT]], Callable[..., RT]]:
    def decorator(func: Callable[..., RT]) -> Callable[..., RT]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> RT:
            if not srl.is_available():
                func_randid, position = srl.init_waiter()
                srl.notify(client, channel, position, thread_ts)
                while not srl.is_available():
                    srl.waiter(func_randid)
            srl.acquire_slot()
            return func(*args, **kwargs)

        return wrapper

    return decorator


def build_slack_context_str(
    messages: list[ThreadMessage], channel_name: str | None
) -> str | None:
    if not messages:
        return None

    if channel_name:
        slack_context_str = f"The following is a thread in Slack in channel {channel_name}:\n====================\n"
    else:
        slack_context_str = (
            "The following is a thread from Slack:\n====================\n"
        )

    message_strs: list[str] = []
    for message in messages:
        if message.role == MessageType.USER:
            message_text = f"{message.sender or 'Unknown User'}:\n{message.message}"
        elif message.role == MessageType.ASSISTANT:
            message_text = f"AI:\n{message.message}"
        else:
            message_text = f"{message.role.value.upper()}:\n{message.message}"
        message_strs.append(message_text)

    return slack_context_str + "\n\n".join(message_strs)


def handle_regular_answer(
    message_info: SlackMessageInfo,
    slack_channel_config: SlackChannelConfig,
    receiver_ids: list[str] | None,
    client: WebClient,
    channel: str,
    logger: DocubrainLoggingAdapter,
    feedback_reminder_id: str | None,
    num_retries: int = DOCUBRAIN_BOT_NUM_RETRIES,
    should_respond_with_error_msgs: bool = DOCUBRAIN_BOT_DISPLAY_ERROR_MSGS,
    disable_docs_only_answer: bool = DOCUBRAIN_BOT_DISABLE_DOCS_ONLY_ANSWER,
) -> bool:
    channel_conf = slack_channel_config.channel_config

    messages = message_info.thread_messages

    message_ts_to_respond_to = message_info.msg_to_respond
    is_slash_command = message_info.is_slash_command

    # Capture whether response mode for channel is ephemeral. Even if the channel is set
    # to respond with an ephemeral message, we still send as non-ephemeral if
    # the message is a dm with the DocuBrain bot.
    send_as_ephemeral = (
        slack_channel_config.channel_config.get("is_ephemeral", False)
        or message_info.is_slash_command
    ) and not message_info.is_bot_dm

    # If the channel is configured to respond with an ephemeral message,
    # or the message is a dm to the DocuBrain bot, we should use the proper docubrain user from the email.
    # This will make documents privately accessible to the user available to DocuBrain Bot answers.
    # Otherwise - if not ephemeral or DM to DocuBrain Bot - we use anonymous user to restrict
    # to public docs.

    if message_info.email:
        with get_session_with_current_tenant() as db_session:
            found_user = get_user_by_email(message_info.email, db_session)
            user = found_user if found_user else get_anonymous_user()
    else:
        user = get_anonymous_user()

    target_thread_ts = (
        None
        if send_as_ephemeral and len(message_info.thread_messages) < 2
        else message_ts_to_respond_to
    )
    target_receiver_ids = (
        [message_info.sender_id]
        if message_info.sender_id and send_as_ephemeral
        else receiver_ids
    )

    document_set_names: list[str] | None = None
    # If no persona is specified, use the default search based persona
    # This way slack flow always has a persona
    persona = slack_channel_config.persona
    if not persona:
        logger.warning("No persona found for channel config, using default persona")
        with get_session_with_current_tenant() as db_session:
            persona = get_persona_by_id(DEFAULT_PERSONA_ID, user, db_session)
            document_set_names = [
                document_set.name for document_set in persona.document_sets
            ]
    else:
        logger.info(f"Using persona {persona.name} for channel config")
        document_set_names = [
            document_set.name for document_set in persona.document_sets
        ]

    user_message = messages[-1]
    history_messages = messages[:-1]

    # Resolve any <#CHANNEL_ID> references in the user message to readable
    # channel names and extract channel tags for search filtering
    resolved_message, channel_tags = resolve_channel_references(
        message=user_message.message,
        client=client,
        logger=logger,
    )

    user_message = ThreadMessage(
        message=resolved_message,
        sender=user_message.sender,
        role=user_message.role,
    )

    channel_name, _ = get_channel_name_from_id(
        client=client,
        channel_id=channel,
    )

    # NOTE: only the message history will contain the person asking. This is likely
    # fine since the most common use case for this info is when referring to a user
    # who previously posted in the thread.
    slack_context_str = build_slack_context_str(history_messages, channel_name)

    if not message_ts_to_respond_to and not is_slash_command:
        # if the message is not "/docubrain" command, then it should have a message ts to respond to
        raise RuntimeError(
            "No message timestamp to respond to in `handle_message`. This should never happen."
        )

    @retry(
        tries=num_retries,
        delay=0.25,
        backoff=2,
    )
    @rate_limits(client=client, channel=channel, thread_ts=message_ts_to_respond_to)
    def _get_slack_answer(
        new_message_request: SendMessageRequest,
        slack_context_str: str | None,
        docubrain_user: User,
    ) -> ChatBasicResponse:
        with get_session_with_current_tenant() as db_session:
            packets = handle_stream_message_objects(
                new_msg_req=new_message_request,
                user=docubrain_user,
                db_session=db_session,
                bypass_acl=False,
                additional_context=slack_context_str,
                slack_context=message_info.slack_context,
            )
            answer = gather_stream(packets)

        if answer.error_msg:
            raise RuntimeError(answer.error_msg)

        return answer

    try:
        # By leaving time_cutoff and favor_recent as None, and setting enable_auto_detect_filters
        # it allows the slack flow to extract out filters from the user query
        filters = BaseFilters(
            source_type=None,
            document_set=document_set_names,
            time_cutoff=None,
            tags=channel_tags if channel_tags else None,
        )

        new_message_request = SendMessageRequest(
            message=user_message.message,
            allowed_tool_ids=None,
            forced_tool_id=None,
            file_descriptors=[],
            internal_search_filters=filters,
            deep_research=False,
            origin=MessageOrigin.SLACKBOT,
            chat_session_info=ChatSessionCreationRequest(
                persona_id=persona.id,
            ),
        )

        # if it's a DM or ephemeral message, answer based on private documents.
        # otherwise, answer based on public documents ONLY as to not leak information.
        can_search_over_private_docs = message_info.is_bot_dm or send_as_ephemeral
        answer = _get_slack_answer(
            new_message_request=new_message_request,
            docubrain_user=user if can_search_over_private_docs else get_anonymous_user(),
            slack_context_str=slack_context_str,
        )

        # If a channel filter was applied but no results were found, override
        # the LLM response to avoid hallucinated answers about unindexed channels
        if channel_tags and not answer.citation_info and not answer.top_documents:
            channel_names = ", ".join(f"#{tag.tag_value}" for tag in channel_tags)
            answer.answer = (
                f"No indexed data found for {channel_names}. "
                "This channel may not be indexed, or there may be no messages "
                "matching your query within it."
            )

    except Exception as e:
        logger.exception(
            f"Unable to process message - did not successfully answer in {num_retries} attempts"
        )
        # Optionally, respond in thread with the error message, Used primarily
        # for debugging purposes
        if should_respond_with_error_msgs:
            respond_in_thread_or_channel(
                client=client,
                channel=channel,
                receiver_ids=target_receiver_ids,
                text=f"Encountered exception when trying to answer: \n\n```{e}```",
                thread_ts=target_thread_ts,
                send_as_ephemeral=send_as_ephemeral,
            )

        # In case of failures, don't keep the reaction there permanently
        update_emote_react(
            emoji=DOCUBRAIN_BOT_REACT_EMOJI,
            channel=message_info.channel_to_respond,
            message_ts=message_info.msg_to_respond,
            remove=True,
            client=client,
        )

        return True

    # Got an answer at this point, can remove reaction and give results
    if not is_slash_command:  # Slash commands don't have reactions
        update_emote_react(
            emoji=DOCUBRAIN_BOT_REACT_EMOJI,
            channel=message_info.channel_to_respond,
            message_ts=message_info.msg_to_respond,
            remove=True,
            client=client,
        )

    if not answer.answer and disable_docs_only_answer:
        logger.notice(
            "Unable to find answer - not responding since the `DOCUBRAIN_BOT_DISABLE_DOCS_ONLY_ANSWER` env variable is set"
        )
        return True

    only_respond_if_citations = (
        channel_conf
        and "well_answered_postfilter" in channel_conf.get("answer_filters", [])
    )

    if (
        only_respond_if_citations
        and not answer.citation_info
        and not message_info.bypass_filters
        and not channel_tags
    ):
        logger.error(
            f"Unable to find citations to answer: '{answer.answer}' - not answering!"
        )
        # Optionally, respond in thread with the error message
        # Used primarily for debugging purposes
        if should_respond_with_error_msgs:
            respond_in_thread_or_channel(
                client=client,
                channel=channel,
                receiver_ids=target_receiver_ids,
                text="Found no citations or quotes when trying to answer.",
                thread_ts=target_thread_ts,
                send_as_ephemeral=send_as_ephemeral,
            )
        return True

    if (
        send_as_ephemeral
        and target_receiver_ids is not None
        and len(target_receiver_ids) == 1
    ):
        offer_ephemeral_publication = True
        skip_ai_feedback = True
    else:
        offer_ephemeral_publication = False
        skip_ai_feedback = False

    all_blocks = build_slack_response_blocks(
        message_info=message_info,
        answer=answer,
        channel_conf=channel_conf,
        feedback_reminder_id=feedback_reminder_id,
        offer_ephemeral_publication=offer_ephemeral_publication,
        skip_ai_feedback=skip_ai_feedback,
    )

    # NOTE(rkuo): Slack has a maximum block list size of 50.
    # we should modify build_slack_response_blocks to respect the max
    # but enforcing the hard limit here is the last resort.
    all_blocks = all_blocks[:50]

    try:
        respond_in_thread_or_channel(
            client=client,
            channel=channel,
            receiver_ids=target_receiver_ids,
            text="Hello! DocuBrain has some results for you!",
            blocks=all_blocks,
            thread_ts=target_thread_ts,
            # don't unfurl, since otherwise we will have 5+ previews which makes the message very long
            unfurl=False,
            send_as_ephemeral=send_as_ephemeral,
        )

        # For DM (ephemeral message), we need to create a thread via a normal message so the user can see
        # the ephemeral message. This also will give the user a notification which ephemeral message does not.
        # if there is no message_ts_to_respond_to, and we have made it this far, then this is a /docubrain message
        # so we shouldn't send_team_member_message
        if (
            target_receiver_ids
            and message_ts_to_respond_to is not None
            and not send_as_ephemeral
            and target_thread_ts is not None
        ):
            send_team_member_message(
                client=client,
                channel=channel,
                thread_ts=target_thread_ts,
                receiver_ids=target_receiver_ids,
                send_as_ephemeral=send_as_ephemeral,
            )

        return False

    except Exception:
        logger.exception(
            f"Unable to process message - could not respond in slack in {num_retries} attempts"
        )
        return True
