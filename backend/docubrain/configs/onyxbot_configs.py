import os

#####
# DocuBrain Slack Bot Configs
#####
DOCUBRAIN_BOT_NUM_RETRIES = int(os.environ.get("DOCUBRAIN_BOT_NUM_RETRIES", "5"))
# Number of docs to display in "Reference Documents"
DOCUBRAIN_BOT_NUM_DOCS_TO_DISPLAY = int(os.environ.get("DOCUBRAIN_BOT_NUM_DOCS_TO_DISPLAY", "5"))
# If the LLM fails to answer, DocuBrain can still show the "Reference Documents"
DOCUBRAIN_BOT_DISABLE_DOCS_ONLY_ANSWER = os.environ.get(
    "DOCUBRAIN_BOT_DISABLE_DOCS_ONLY_ANSWER", ""
).lower() not in ["false", ""]
# When DocuBrain is considering a message, what emoji does it react with
DOCUBRAIN_BOT_REACT_EMOJI = os.environ.get("DOCUBRAIN_BOT_REACT_EMOJI") or "eyes"
# When User needs more help, what should the emoji be
DOCUBRAIN_BOT_FOLLOWUP_EMOJI = os.environ.get("DOCUBRAIN_BOT_FOLLOWUP_EMOJI") or "sos"
# What kind of message should be shown when someone gives an AI answer feedback to DocubrainBot
# Defaults to Private if not provided or invalid
# Private: Only visible to user clicking the feedback
# Anonymous: Public but anonymous
# Public: Visible with the user name who submitted the feedback
DOCUBRAIN_BOT_FEEDBACK_VISIBILITY = (
    os.environ.get("DOCUBRAIN_BOT_FEEDBACK_VISIBILITY") or "private"
)
# Should DocubrainBot send an apology message if it's not able to find an answer
# That way the user isn't confused as to why DocubrainBot reacted but then said nothing
# Off by default to be less intrusive (don't want to give a notif that just says we couldnt help)
NOTIFY_SLACKBOT_NO_ANSWER = (
    os.environ.get("NOTIFY_SLACKBOT_NO_ANSWER", "").lower() == "true"
)
# Mostly for debugging purposes but it's for explaining what went wrong
# if DocubrainBot couldn't find an answer
DOCUBRAIN_BOT_DISPLAY_ERROR_MSGS = os.environ.get(
    "DOCUBRAIN_BOT_DISPLAY_ERROR_MSGS", ""
).lower() not in [
    "false",
    "",
]

# Maximum Questions Per Minute, Default Uncapped
DOCUBRAIN_BOT_MAX_QPM = int(os.environ.get("DOCUBRAIN_BOT_MAX_QPM") or 0) or None
# Maximum time to wait when a question is queued
DOCUBRAIN_BOT_MAX_WAIT_TIME = int(os.environ.get("DOCUBRAIN_BOT_MAX_WAIT_TIME") or 180)

# Time (in minutes) after which a Slack message is sent to the user to remind him to give feedback.
# Set to 0 to disable it (default)
DOCUBRAIN_BOT_FEEDBACK_REMINDER = int(os.environ.get("DOCUBRAIN_BOT_FEEDBACK_REMINDER") or 0)

# DOCUBRAIN_BOT_RESPONSE_LIMIT_PER_TIME_PERIOD is the number of
# responses DocubrainBot can send in a given time period.
# Set to 0 to disable the limit.
DOCUBRAIN_BOT_RESPONSE_LIMIT_PER_TIME_PERIOD = int(
    os.environ.get("DOCUBRAIN_BOT_RESPONSE_LIMIT_PER_TIME_PERIOD", "5000")
)
# DOCUBRAIN_BOT_RESPONSE_LIMIT_TIME_PERIOD_SECONDS is the number
# of seconds until the response limit is reset.
DOCUBRAIN_BOT_RESPONSE_LIMIT_TIME_PERIOD_SECONDS = int(
    os.environ.get("DOCUBRAIN_BOT_RESPONSE_LIMIT_TIME_PERIOD_SECONDS", "86400")
)
