from typing import Any
from typing import TYPE_CHECKING
from datetime import datetime
from datetime import timezone

from docubrain.configs.constants import DocumentSource
from docubrain.db.models import Credential

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
else:
    Session = Any


def get_user_google_workspace_credential(
    *,
    db_session: Session,
    user: Any,
    source: DocumentSource,
) -> Any | None:
    credential_model = _credential_model()
    statement = _select(credential_model).where(
        credential_model.user_id == user.id,
        credential_model.source == source,
    ).order_by(credential_model.id.desc())
    results = db_session.execute(statement).scalars().all()
    if not results:
        return None
    # If duplicate credentials exist, return the most recent one
    return results[0]


def get_user_google_workspace_credential_json(
    *,
    db_session: Session,
    user: Any,
    source: DocumentSource,
) -> dict[str, Any] | None:
    credential = get_user_google_workspace_credential(
        db_session=db_session,
        user=user,
        source=source,
    )
    if credential is None or credential.credential_json is None:
        return None
    return credential.credential_json.get_value(apply_mask=False)


def get_user_google_workspace_credential_json_core(
    *,
    db_session: Session,
    user: Any,
    source: DocumentSource,
) -> dict[str, Any] | None:
    """Return the decrypted credential JSON without loading the Credential ORM
    instance into the session identity map.

    This is critical for the disconnect flow: loading the ORM instance triggers
    ``cascade="all, delete-orphan"`` bookkeeping on ``Credential.connectors``
    which eventually cascades NULL into ``IndexAttempt.connector_credential_pair_id``
    and causes a ``NotNullViolation`` on commit/autoflush.

    The ``EncryptedJson`` column type's ``process_result_value`` still runs on
    Core-only column selects, so we get the ``SensitiveValue`` wrapper back and
    can decrypt it without ever touching the ORM.
    """
    from sqlalchemy import select as _sa_select

    from docubrain.db.models import Credential

    result = db_session.execute(
        _sa_select(Credential.credential_json).where(
            Credential.user_id == user.id,
            Credential.source == source,
        ).order_by(Credential.id.desc()).limit(1)
    ).scalar_one_or_none()

    if result is None:
        return None
    # result is a SensitiveValue wrapping the encrypted dict
    return result.get_value(apply_mask=False)


def update_user_google_workspace_credential_json(
    *,
    db_session: Session,
    credential_id: int,
    user: Any,
    credential_json: dict[str, Any],
) -> bool:
    credential_model = _credential_model()
    statement = _select(credential_model).where(
        credential_model.id == credential_id,
        credential_model.user_id == user.id,
    )
    credential = db_session.execute(statement).scalar_one_or_none()
    if credential is None:
        return False

    credential.credential_json = credential_json
    db_session.commit()
    return True


def upsert_user_google_workspace_credential_json(
    *,
    db_session: Session,
    user: Any,
    source: DocumentSource,
    credential_json: dict[str, Any],
    name: str,
) -> Credential:
    credential = get_user_google_workspace_credential(
        db_session=db_session,
        user=user,
        source=source,
    )
    if credential is None:
        credential = Credential(
            name=name,
            source=source,
            credential_json=credential_json,
            user_id=user.id,
            admin_public=False,
            curator_public=False,
        )
        db_session.add(credential)
    else:
        credential.name = name
        credential.credential_json = credential_json
        credential.admin_public = False
        credential.curator_public = False
    db_session.flush()
    return credential


def delete_user_google_workspace_credential(
    *,
    db_session: Session,
    user: Any,
    source: DocumentSource,
) -> None:
    """Disconnect Google Workspace OAuth for the given user/source.

    If the underlying ``Credential`` row is referenced by one or more
    ``ConnectorCredentialPair`` rows (e.g. the user also configured a Gmail or
    Drive connector that shares this credential), we cannot simply delete the
    row: the FK constraint ``connector_credential_pair_credential_id_fkey`` has
    no ``ON DELETE CASCADE``. Instead we clear the encrypted token JSON which
    is sufficient to mark the MCP integration as disconnected (the status API
    treats ``credential_json IS NULL`` as disconnected) while preserving the
    connector linkage. When there are no CCPs, we remove the row entirely.

    IMPORTANT: This function uses Core SQL exclusively. Loading the Credential
    ORM model into the session triggers ``cascade="all, delete-orphan"`` on the
    ``Credential.connectors`` relationship which cascades into IndexAttempt
    rows and causes NOT NULL violations. See comment below for full explanation.
    """
    from docubrain.db.models import ConnectorCredentialPair, Credential
    from docubrain.db.models import GoogleDriveSyncState
    from sqlalchemy import delete as _sa_delete
    from sqlalchemy import func
    from sqlalchemy import select as _sa_select
    from sqlalchemy import update as _sa_update

    # Step 1: Find the credential ID using a Core-only scalar query
    # (avoids loading the Credential ORM instance into the session identity map)
    credential_id = db_session.execute(
        _sa_select(Credential.id).where(
            Credential.user_id == user.id,
            Credential.source == source,
        ).order_by(Credential.id.desc()).limit(1)
    ).scalar_one_or_none()

    if credential_id is None:
        return

    if source == DocumentSource.GOOGLE_DRIVE:
        cc_pair_ids = db_session.execute(
            _sa_select(ConnectorCredentialPair.id).where(
                ConnectorCredentialPair.credential_id == credential_id
            )
        ).scalars()
        cc_pair_id_list = list(cc_pair_ids)
        if cc_pair_id_list:
            db_session.execute(
                _sa_update(GoogleDriveSyncState)
                .where(
                    GoogleDriveSyncState.connector_credential_pair_id.in_(
                        cc_pair_id_list
                    )
                )
                .values(
                    full_sync_required=True,
                    token_invalid=True,
                    error_message="Google Drive MCP credential disconnected",
                    updated_at=datetime.now(timezone.utc),
                )
            )

    # Step 2: Check if the credential has any ConnectorCredentialPair links
    has_ccp = db_session.execute(
        _sa_select(func.count())
        .select_from(ConnectorCredentialPair)
        .where(ConnectorCredentialPair.credential_id == credential_id)
    ).scalar_one()

    # Step 3: Either clear the token JSON (preserving the CCP link) or
    # delete the row entirely — all via Core SQL to bypass ORM cascades.
    if has_ccp:
        db_session.execute(
            _sa_update(Credential)
            .where(Credential.id == credential_id)
            .values(credential_json=None)
        )
    else:
        db_session.execute(
            _sa_delete(Credential).where(Credential.id == credential_id)
        )


def _credential_model() -> Any:
    from docubrain.db.models import Credential

    return Credential


def _select(*entities: Any) -> Any:
    from sqlalchemy import select

    return select(*entities)
