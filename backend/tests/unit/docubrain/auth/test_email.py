import pytest

from docubrain.auth.email_utils import build_user_email_invite
from docubrain.auth.email_utils import send_email
from docubrain.configs.constants import AuthType
from docubrain.configs.constants import DOCUBRAIN_DEFAULT_APPLICATION_NAME
from docubrain.db.engine.sql_engine import SqlEngine
from docubrain.server.runtime.docubrain_runtime import DocubrainRuntime


@pytest.mark.skip(
    reason="This sends real emails, so only run when you really want to test this!"
)
def test_send_user_email_invite() -> None:
    SqlEngine.init_engine(pool_size=20, max_overflow=5)

    application_name = DOCUBRAIN_DEFAULT_APPLICATION_NAME

    docubrain_file = DocubrainRuntime.get_emailable_logo()

    subject = f"Invitation to Join {application_name} Organization"

    FROM_EMAIL = "noreply@docubrain.app"
    TO_EMAIL = "support@docubrain.app"
    text_content, html_content = build_user_email_invite(
        FROM_EMAIL, TO_EMAIL, DOCUBRAIN_DEFAULT_APPLICATION_NAME, AuthType.CLOUD
    )

    send_email(
        TO_EMAIL,
        subject,
        html_content,
        text_content,
        mail_from=FROM_EMAIL,
        inline_png=("logo.png", docubrain_file.data),
    )
