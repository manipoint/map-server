"""Tests for the authentication session persistence model."""

from sqlalchemy import CheckConstraint, UniqueConstraint

from app.database.models import AuthSession


def test_auth_session_uses_application_schema() -> None:
    """Authentication sessions should live in the application schema."""

    assert AuthSession.__table__.schema == "app"
    assert AuthSession.__table__.name == "auth_sessions"
    assert AuthSession.__table__.fullname == "app.auth_sessions"


def test_auth_session_contains_required_columns() -> None:
    """Authentication sessions should contain security and device fields."""

    assert set(AuthSession.__table__.columns.keys()) == {
        "id",
        "user_id",
        "refresh_token_hash",
        "token_family_id",
        "device_id",
        "device_name",
        "created_at",
        "last_used_at",
        "expires_at",
        "rotated_at",
        "revoked_at",
        "revoke_reason",
        "replaced_by_session_id",
        "ip_address",
        "user_agent",
    }


def test_auth_session_foreign_keys_define_delete_behavior() -> None:
    """User deletion and rotation links should have explicit behavior."""

    user_foreign_key = next(iter(AuthSession.__table__.c.user_id.foreign_keys))
    replacement_foreign_key = next(
        iter(AuthSession.__table__.c.replaced_by_session_id.foreign_keys)
    )
    assert user_foreign_key.target_fullname == "app.users.id"
    assert user_foreign_key.ondelete == "CASCADE"

    assert replacement_foreign_key.target_fullname == "app.auth_sessions.id"
    assert replacement_foreign_key.ondelete == "SET NULL"


def test_refresh_token_hash_is_unique() -> None:
    """Each refresh-token hash should identify one session record."""

    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in AuthSession.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("refresh_token_hash",) in unique_columns


def test_auth_session_has_security_constraints() -> None:
    """Session timestamps and rotation state should be constrained."""
    constraint_names = {
        constraint.name
        for constraint in AuthSession.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert constraint_names == {
        "ck_auth_sessions_expiration_after_creation",
        "ck_auth_sessions_rotation_after_creation",
        "ck_auth_sessions_revocation_after_creation",
        "ck_auth_sessions_replacement_requires_rotation",
        "ck_auth_sessions_cannot_replace_itself",
        "ck_auth_sessions_reason_requires_revocation",
    }


def test_auth_session_has_lookup_indexes() -> None:
    """Session lookup and cleanup queries should have indexes."""
    indexed_columns = {
        tuple(column.name for column in index.columns)
        for index in AuthSession.__table__.indexes
    }
    assert ("user_id", "revoked_at") in indexed_columns
    assert ("token_family_id",) in indexed_columns
    assert ("expires_at",) in indexed_columns
