"""Shared disposable PostgreSQL fixtures; never connect to application databases."""

import os
import socket
import subprocess
import time
from pathlib import Path
from uuid import uuid4

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
    """Use only a disposable local PostgreSQL instance."""
    configured = os.environ.get("TEST_POSTGRES_BIN")

    if configured:
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
        return

    if os.environ.get("TEST_POSTGRES_DOCKER") != "1":
        pytest.skip(
            "Set TEST_POSTGRES_BIN or TEST_POSTGRES_DOCKER=1 "
            "to run isolated PostgreSQL tests"
        )

    image = os.environ.get("TEST_POSTGRES_DOCKER_IMAGE", "postgres:17")
    name = f"map-server-test-postgres-{uuid4().hex}"

    started = subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--pull=never",
            "--name",
            name,
            "--env",
            "POSTGRES_USER=catalogue_test",
            "--env",
            "POSTGRES_DB=postgres",
            "--env",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            "--publish",
            "127.0.0.1::5432",
            image,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    container_id = started.stdout.strip()

    try:
        deadline = time.monotonic() + 45
        while True:
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    container_id,
                    "pg_isready",
                    "-U",
                    "catalogue_test",
                    "-d",
                    "postgres",
                ],
                capture_output=True,
                timeout=5,
            )
            if ready.returncode == 0:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "Disposable PostgreSQL container did not become ready"
                )
            time.sleep(0.25)

        published = subprocess.run(
            ["docker", "port", container_id, "5432/tcp"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Docker publishes this container only on 127.0.0.1.
        port = int(published.stdout.strip().rsplit(":", 1)[1])

        yield f"postgresql+asyncpg://catalogue_test@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container_id],
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
