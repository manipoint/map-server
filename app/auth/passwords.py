"""Password hashing and verification."""

from anyio import to_thread
from pwdlib import PasswordHash

_password_hash = PasswordHash.recommended()
_dummy_password_hash = _password_hash.hash(
    "dummy-password-used-only-for-timing-protection"
)


async def hash_password(password: str) -> str:
    """Create an Argon2 hash without blocking the event loop."""
    return await to_thread.run_sync(_password_hash.hash, password)


async def verify_password(password: str, password_hash: str) -> bool:
    """Check a plain password against its stored hash."""
    return await to_thread.run_sync(_password_hash.verify, password, password_hash)


async def verify_password_and_update(
    password: str,
    password_hash: str,
) -> tuple[bool, str | None]:
    """Verify a password and return a newer hash when required."""

    return await to_thread.run_sync(
        _password_hash.verify_and_update,
        password,
        password_hash,
    )


async def perform_dummy_password_check(password: str) -> None:
    """Spend normal verification time when an account does not exist."""
    await verify_password(password, _dummy_password_hash)
