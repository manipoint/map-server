"""Shared disposable PostgreSQL fixtures; never connect to application databases."""

import os
import socket
import subprocess
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.database.base import Base


@pytest.fixture
def postgres_url(tmp_path):
    """Never use application credentials or an existing database."""
    configured = os.environ.get("TEST_POSTGRES_BIN")
    if not configured:
        pytest.skip("Set TEST_POSTGRES_BIN to run isolated PostgreSQL tests")
    binaries = Path(configured)
    data = tmp_path / "data"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    subprocess.run(
        [
            str(binaries / "initdb"),
            "-D",
            str(data),
            "-U",
            "catalogue_test",
            "-A",
            "trust",
            "--no-locale",
            "-E",
            "UTF8",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    subprocess.run(
        [
            str(binaries / "pg_ctl"),
            "-D",
            str(data),
            "-l",
            str(tmp_path / "postgres.log"),
            "-o",
            f"-h 127.0.0.1 -p {port} -F -c unix_socket_directories=''",
            "-w",
            "start",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    try:
        yield f"postgresql+asyncpg://catalogue_test@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run(
            [
                str(binaries / "pg_ctl"),
                "-D",
                str(data),
                "-m",
                "immediate",
                "-w",
                "stop",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )


def migrate(connection):
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[3] / "alembic")
    )
    scripts = ScriptDirectory.from_config(config)
    revisions = list(reversed(list(scripts.walk_revisions())))
    with Operations.context(
        MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
    ):
        for revision in revisions:
            if revision.revision == "d4f8a2c7e910":
                connection.execute(
                    text(
                        "INSERT INTO app.users (id,email,password_hash) VALUES "
                        "('90000000-0000-4000-8000-000000000001','migration@example.com','test-hash')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO app.user_preferences "
                        "(user_id,travel_style,budget_tier,trip_pace,recommendation_scope) VALUES "
                        "('90000000-0000-4000-8000-000000000001','nature','mid_range','balanced','both')"
                    )
                )
            revision.module.upgrade()
        media = scripts.get_revision("c8e3a9f21064")
        safety = scripts.get_revision("d92af5b43107")
        deduplication = scripts.get_revision("e71c09ab624f")
        deduplication.module.downgrade()
        safety.module.downgrade()
        safety.module.upgrade()
        deduplication.module.upgrade()
        media.module.downgrade()
        media.module.upgrade()
        # Exercise actual populated catalogue downgrade and upgrade.
        catalogue = scripts.get_revision("d4f8a2c7e910")
        indexes = scripts.get_revision("b7e2f9a41063")
        indexes.module.downgrade()
        catalogue.module.downgrade()
        catalogue.module.upgrade()
        indexes.module.upgrade()
    assert (
        connection.execute(
            text(
                "SELECT travel_style FROM app.user_travel_styles WHERE "
                "user_id='90000000-0000-4000-8000-000000000001'"
            )
        ).scalar_one()
        == "nature"
    )
    context = MigrationContext.configure(
        connection,
        opts={
            "target_metadata": Base.metadata,
            "include_schemas": True,
            "compare_type": True,
            "compare_server_default": True,
        },
    )
    assert compare_metadata(context, Base.metadata) == []


@pytest.fixture
def migrate_database():
    return migrate
