"""Tests for password hashing and verification."""

import asyncio

from app.auth.passwords import (
    hash_password,
    perform_dummy_password_check,
    verify_password,
    verify_password_and_update,
)


def test_password_is_hashed_with_argon2() -> None:
    """Plain passwords should become Argon2 hashes."""

    password_hash = asyncio.run(hash_password("correct horse battery staple"))

    assert password_hash.startswith("$argon2")
    assert "correct horse battery staple" not in password_hash


def test_same_password_gets_unique_salts() -> None:
    """Repeated hashing should produce different stored values."""

    async def create_hashes() -> tuple[str, str]:
        first = await hash_password("same-password")
        second = await hash_password("same-password")
        return first, second

    first_hash, second_hash = asyncio.run(create_hashes())

    assert first_hash != second_hash


def test_password_verification_accepts_correct_password() -> None:
    """Correct passwords should match their stored hash."""

    async def verify() -> bool:
        password_hash = await hash_password("correct-password")
        return await verify_password("correct-password", password_hash)

    assert asyncio.run(verify()) is True


def test_password_verification_rejects_wrong_password() -> None:
    """Incorrect passwords should not match the stored hash."""

    async def verify() -> bool:
        password_hash = await hash_password("correct-password")
        return await verify_password("wrong-password", password_hash)

    assert asyncio.run(verify()) is False


def test_current_hash_does_not_require_update() -> None:
    """A current Argon2 hash should not be replaced during login."""

    async def verify() -> tuple[bool, str | None]:
        password_hash = await hash_password("correct-password")
        return await verify_password_and_update(
            "correct-password",
            password_hash,
        )

    valid, updated_hash = asyncio.run(verify())

    assert valid is True
    assert updated_hash is None


def test_dummy_password_check_completes() -> None:
    """Unknown-account verification should use the dummy hash."""

    asyncio.run(perform_dummy_password_check("unknown-password"))
