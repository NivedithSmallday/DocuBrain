"""Test bulk invite limit for free trial tenants."""

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from docubrain.server.manage.models import EmailInviteStatus
from docubrain.server.manage.users import bulk_invite_users


@patch("docubrain.server.manage.users.MULTI_TENANT", True)
@patch("docubrain.server.manage.users.is_tenant_on_trial_fn", return_value=True)
@patch("docubrain.server.manage.users.get_current_tenant_id", return_value="test_tenant")
@patch("docubrain.server.manage.users.get_invited_users", return_value=[])
@patch("docubrain.server.manage.users.get_all_users", return_value=[])
@patch("docubrain.server.manage.users.NUM_FREE_TRIAL_USER_INVITES", 5)
def test_trial_tenant_cannot_exceed_invite_limit(*_mocks: None) -> None:
    """Trial tenants cannot invite more users than the configured limit."""
    emails = [f"user{i}@example.com" for i in range(6)]

    with pytest.raises(HTTPException) as exc_info:
        bulk_invite_users(emails=emails)

    assert exc_info.value.status_code == 403
    assert "invite limit" in exc_info.value.detail.lower()


@patch("docubrain.server.manage.users.MULTI_TENANT", True)
@patch("docubrain.server.manage.users.DEV_MODE", True)
@patch("docubrain.server.manage.users.ENABLE_EMAIL_INVITES", False)
@patch("docubrain.server.manage.users.is_tenant_on_trial_fn", return_value=True)
@patch("docubrain.server.manage.users.get_current_tenant_id", return_value="test_tenant")
@patch("docubrain.server.manage.users.get_invited_users", return_value=[])
@patch("docubrain.server.manage.users.get_all_users", return_value=[])
@patch("docubrain.server.manage.users.write_invited_users", return_value=3)
@patch("docubrain.server.manage.users.enforce_seat_limit")
@patch("docubrain.server.manage.users.NUM_FREE_TRIAL_USER_INVITES", 5)
@patch(
    "docubrain.server.manage.users.fetch_ee_implementation_or_noop",
    return_value=lambda *_args: None,
)
def test_trial_tenant_can_invite_within_limit(*_mocks: None) -> None:
    """Trial tenants can invite users when under the limit."""
    emails = ["user1@example.com", "user2@example.com", "user3@example.com"]

    result = bulk_invite_users(emails=emails)

    assert result.invited_count == 3
    assert result.email_invite_status == EmailInviteStatus.DISABLED


# --- email_invite_status tests ---

_COMMON_PATCHES = [
    patch("docubrain.server.manage.users.MULTI_TENANT", False),
    patch("docubrain.server.manage.users.get_current_tenant_id", return_value="test_tenant"),
    patch("docubrain.server.manage.users.get_invited_users", return_value=[]),
    patch("docubrain.server.manage.users.get_all_users", return_value=[]),
    patch("docubrain.server.manage.users.write_invited_users", return_value=1),
    patch("docubrain.server.manage.users.enforce_seat_limit"),
]


def _with_common_patches(fn: object) -> object:
    for p in reversed(_COMMON_PATCHES):
        fn = p(fn)  # type: ignore
    return fn


@_with_common_patches
@patch("docubrain.server.manage.users.ENABLE_EMAIL_INVITES", False)
def test_email_invite_status_disabled(*_mocks: None) -> None:
    """When email invites are disabled, status is disabled."""
    result = bulk_invite_users(emails=["user@example.com"])

    assert result.email_invite_status == EmailInviteStatus.DISABLED


@_with_common_patches
@patch("docubrain.server.manage.users.ENABLE_EMAIL_INVITES", True)
@patch("docubrain.server.manage.users.EMAIL_CONFIGURED", False)
def test_email_invite_status_not_configured(*_mocks: None) -> None:
    """When email invites are enabled but no server is configured, status is not_configured."""
    result = bulk_invite_users(emails=["user@example.com"])

    assert result.email_invite_status == EmailInviteStatus.NOT_CONFIGURED


@_with_common_patches
@patch("docubrain.server.manage.users.ENABLE_EMAIL_INVITES", True)
@patch("docubrain.server.manage.users.EMAIL_CONFIGURED", True)
@patch("docubrain.server.manage.users.send_user_email_invite")
def test_email_invite_status_sent(mock_send: MagicMock, *_mocks: None) -> None:
    """When email invites are enabled and configured, status is sent."""
    result = bulk_invite_users(emails=["user@example.com"])

    mock_send.assert_called_once()
    assert result.email_invite_status == EmailInviteStatus.SENT


@_with_common_patches
@patch("docubrain.server.manage.users.ENABLE_EMAIL_INVITES", True)
@patch("docubrain.server.manage.users.EMAIL_CONFIGURED", True)
@patch(
    "docubrain.server.manage.users.send_user_email_invite",
    side_effect=Exception("SMTP auth failed"),
)
def test_email_invite_status_send_failed(*_mocks: None) -> None:
    """When email sending throws, status is send_failed and invite is still saved."""
    result = bulk_invite_users(emails=["user@example.com"])

    assert result.email_invite_status == EmailInviteStatus.SEND_FAILED
    assert result.invited_count == 1
