# Development Commands

This document is the command reference for local development. Run commands from the repository root unless a section says otherwise.

## Project setup

Install the locked runtime and default development dependencies:

```bash
uv sync --locked
```

Confirm the active Python and uv versions:

```bash
uv run python --version
uv --version
```

## Dependency workflow

`pyproject.toml` is the source of direct dependencies, `uv.lock` records the resolved dependency graph, and `requirements.txt` is a generated compatibility export. Do not edit `requirements.txt` manually.

### Add a runtime package

```bash
uv add package-name
uv export --locked --format requirements.txt --no-hashes --output-file requirements.txt
```

Example:

```bash
uv add fastapi "uvicorn[standard]"
uv export --locked --format requirements.txt --no-hashes --output-file requirements.txt
```

### Add a development package

```bash
uv add --dev package-name
uv export --locked --format requirements.txt --no-hashes --output-file requirements.txt
```

Examples:

```bash
uv add --dev pytest ruff httpx2
uv export --locked --format requirements.txt --no-hashes --output-file requirements.txt
```

### Verify dependency synchronization

```bash
uv lock --check
uv sync --locked
```

Check that an expected package is installed:

```bash
uv pip show package-name
```

### Production-only installation

Exclude development dependencies in a production environment:

```bash
uv sync --locked --no-dev
```

## Configuration checks

Confirm settings load successfully without printing secret values:

```bash
uv run python -c "from app.config import get_settings; s=get_settings(); print(s.app_name, s.app_env, s.debug, bool(s.weather_api_key))"
```

Never print `SecretStr.get_secret_value()` output during ordinary diagnostics. Do not paste `.env` contents into chat, logs, issues, or commits.

Application debug mode uses the prefixed environment variable `APP_DEBUG`. Generic ambient variables such as `DEBUG` are deliberately ignored.

## Ruff linting and formatting

Automatically fix safe lint findings, then format the code:

```bash
uv run ruff check --fix app tests
uv run ruff format app tests
```

Run non-mutating verification:

```bash
uv run ruff check app tests
uv run ruff format --check app tests
```

Check one file while developing:

```bash
uv run ruff check app/main.py
uv run ruff format --check app/main.py
```

## Tests

Run the complete test suite:

```bash
uv run pytest
```

Run tests with verbose names:

```bash
uv run pytest -v
```

Run one test file:

```bash
uv run pytest tests/unit/api/routes/test_health.py
```

Run one test function:

```bash
uv run pytest tests/unit/api/routes/test_health.py::test_liveness_check_returns_ok
```

Run the structured-logging tests:

```bash
uv run pytest tests/unit/observability/test_logging.py
```

Stop after the first failure:

```bash
uv run pytest -x
```

Re-run only tests that failed in the previous run:

```bash
uv run pytest --last-failed
```

The project configures pytest with `--import-mode=importlib`, so test files in different directories may safely use the same filename.

## Local API server

Start FastAPI with automatic reload:

```bash
uv run uvicorn app.main:app --reload
```

Use explicit configured host and port when needed:

```bash
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Check process liveness:

```bash
curl http://127.0.0.1:8000/health/live
```

Expected response:

```json
{"status":"ok"}
```

Open the generated API documentation at `http://127.0.0.1:8000/docs`.

## Before completing a coding step

Run the following quality gate:

```bash
uv run ruff check app tests
uv run ruff format --check app tests
uv run pytest
uv lock --check
```

If a package was added or removed, regenerate `requirements.txt` before committing:

```bash
uv export --locked --format requirements.txt --no-hashes --output-file requirements.txt
```
