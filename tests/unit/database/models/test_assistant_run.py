"""Tests for the assistant response processing lease model."""

from sqlalchemy import CheckConstraint, UniqueConstraint

from app.database.models import AssistantRun


def test_assistant_run_uses_application_schema() -> None:
    """Assistant runs should live in the application schema."""

    assert AssistantRun.__table__.schema == "app"
    assert AssistantRun.__table__.name == "assistant_runs"
    assert AssistantRun.__table__.fullname == "app.assistant_runs"


def test_assistant_run_contains_required_columns() -> None:
    """A processing lease should retain claim, result, and retry metadata."""

    assert set(AssistantRun.__table__.columns.keys()) == {
        "id",
        "user_message_id",
        "assistant_message_id",
        "status",
        "claim_token",
        "lease_expires_at",
        "attempt_count",
        "last_error_code",
        "created_at",
        "updated_at",
    }
    assert AssistantRun.__table__.c.id.primary_key is True
    assert AssistantRun.__table__.c.user_message_id.nullable is False
    assert AssistantRun.__table__.c.assistant_message_id.nullable is True
    assert AssistantRun.__table__.c.status.nullable is False
    assert AssistantRun.__table__.c.claim_token.nullable is True
    assert AssistantRun.__table__.c.lease_expires_at.nullable is True
    assert AssistantRun.__table__.c.attempt_count.nullable is False
    assert AssistantRun.__table__.c.last_error_code.nullable is True


def test_assistant_run_message_foreign_keys_cascade_on_delete() -> None:
    """Deleting either referenced message should remove its processing record."""

    user_message_foreign_key = next(
        iter(AssistantRun.__table__.c.user_message_id.foreign_keys)
    )
    assistant_message_foreign_key = next(
        iter(AssistantRun.__table__.c.assistant_message_id.foreign_keys)
    )

    assert user_message_foreign_key.target_fullname == "app.messages.id"
    assert user_message_foreign_key.ondelete == "CASCADE"
    assert assistant_message_foreign_key.target_fullname == "app.messages.id"
    assert assistant_message_foreign_key.ondelete == "CASCADE"


def test_assistant_run_message_references_are_unique() -> None:
    """A user request and assistant response should each belong to one run."""

    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in AssistantRun.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert unique_columns == {
        ("assistant_message_id",),
        ("user_message_id",),
    }


def test_assistant_run_has_lifecycle_constraints() -> None:
    """Database metadata should enforce valid processing lifecycle states."""

    constraint_names = {
        constraint.name
        for constraint in AssistantRun.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert constraint_names == {
        "ck_assistant_runs_attempt_count_positive",
        "ck_assistant_runs_error_code_length",
        "ck_assistant_runs_lease_after_creation",
        "ck_assistant_runs_state_shape",
        "ck_assistant_runs_status",
    }


def test_assistant_run_has_status_lease_index() -> None:
    """Expired processing leases should be searchable efficiently."""

    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in AssistantRun.__table__.indexes
    }

    assert indexes == {
        "ix_assistant_runs_status_lease": (
            "status",
            "lease_expires_at",
        )
    }


def test_assistant_run_uses_processing_and_first_attempt_defaults() -> None:
    """A newly claimed run should begin processing on its first attempt."""

    status = AssistantRun.__table__.c.status
    attempt_count = AssistantRun.__table__.c.attempt_count

    assert status.default is not None
    assert status.default.arg == "processing"
    assert status.server_default is not None
    assert str(status.server_default.arg) == "'processing'"
    assert attempt_count.default is not None
    assert attempt_count.default.arg == 1
    assert attempt_count.server_default is not None
    assert str(attempt_count.server_default.arg) == "1"


def test_assistant_run_timestamps_and_lease_are_timezone_aware() -> None:
    """Lease comparisons and audit timestamps should preserve timezones."""

    lease_expires_at = AssistantRun.__table__.c.lease_expires_at
    created_at = AssistantRun.__table__.c.created_at
    updated_at = AssistantRun.__table__.c.updated_at

    assert lease_expires_at.type.timezone is True
    assert created_at.type.timezone is True
    assert updated_at.type.timezone is True
    assert created_at.server_default is not None
    assert updated_at.server_default is not None
    assert updated_at.onupdate is not None
