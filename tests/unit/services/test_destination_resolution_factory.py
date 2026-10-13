import asyncio
from unittest.mock import MagicMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.country_recognition_service import CountryRecognitionService
from app.services.destination_resolution_factory import DestinationResolutionFactory


def make_session() -> MagicMock:
    session = MagicMock(spec=AsyncSession)
    session.__aenter__.return_value = session
    session.__aexit__.return_value = False
    return session


def test_factory_opens_separate_sessions_and_reuses_country_index():
    first_session = make_session()
    second_session = make_session()
    sessions = Mock(side_effect=[first_session, second_session])
    recognition = CountryRecognitionService()
    factory = DestinationResolutionFactory(
        session_factory=sessions,
        country_recognition=recognition,
    )

    async def exercise():
        async with factory.open() as first:
            assert first.repository.session is first_session
            assert first.country_recognition is recognition

        async with factory.open() as second:
            assert second.repository.session is second_session
            assert second.country_recognition is recognition

    asyncio.run(exercise())

    assert sessions.call_count == 2
    first_session.__aexit__.assert_awaited_once()
    second_session.__aexit__.assert_awaited_once()


def test_factory_exits_session_when_operation_fails():
    session = make_session()
    factory = DestinationResolutionFactory(
        session_factory=Mock(return_value=session),
        country_recognition=CountryRecognitionService(),
    )

    async def exercise():
        async with factory.open():
            raise RuntimeError("synthetic operation failure")

    with pytest.raises(RuntimeError, match="synthetic operation failure"):
        asyncio.run(exercise())

    session.__aexit__.assert_awaited_once()
    assert session.__aexit__.await_args.args[0] is RuntimeError


def test_factory_exits_session_on_cancellation():
    session = make_session()
    factory = DestinationResolutionFactory(
        session_factory=Mock(return_value=session),
        country_recognition=CountryRecognitionService(),
    )

    async def exercise():
        async with factory.open():
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(exercise())

    session.__aexit__.assert_awaited_once()
    assert session.__aexit__.await_args.args[0] is asyncio.CancelledError
