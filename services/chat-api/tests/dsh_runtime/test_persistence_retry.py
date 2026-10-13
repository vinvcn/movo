from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from bson.errors import InvalidDocument
from pymongo.errors import AutoReconnect, BulkWriteError

from app.dsh_runtime.events.persistence_retry import (
    PersistenceRetryPolicy,
    is_retryable_persistence_error,
    retry_persistence,
)
from app.dsh_runtime.events.repository import (
    MESSAGE_STREAM_COUNTER,
    KernelEventRepository,
    KernelEventWrite,
)


def test_transient_failure_is_retried_until_success() -> None:
    async def run() -> None:
        attempts = 0
        delays: list[float] = []

        async def operation() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise AutoReconnect("temporary disconnect")
            return "persisted"

        async def sleep(delay: float) -> None:
            delays.append(delay)

        result = await retry_persistence(
            operation,
            stage="test",
            policy=PersistenceRetryPolicy(
                max_attempts=4,
                initial_delay_seconds=0.01,
                max_delay_seconds=0.1,
            ),
            sleep=sleep,
        )
        assert result == "persisted"
        assert attempts == 3
        assert delays == [0.01, 0.02]

    asyncio.run(run())


def test_non_retryable_serialization_failure_fails_immediately() -> None:
    async def run() -> None:
        attempts = 0

        async def operation() -> None:
            nonlocal attempts
            attempts += 1
            raise InvalidDocument("invalid BSON")

        with pytest.raises(InvalidDocument):
            await retry_persistence(operation, stage="test")
        assert attempts == 1

    asyncio.run(run())


def test_duplicate_upsert_race_is_retryable() -> None:
    error = BulkWriteError(
        {
            "writeErrors": [{"index": 0, "code": 11000, "errmsg": "duplicate key"}],
            "writeConcernErrors": [],
            "nInserted": 0,
            "nUpserted": 0,
            "nMatched": 0,
            "nModified": 0,
            "nRemoved": 0,
            "upserted": [],
        }
    )
    assert is_retryable_persistence_error(error) is True


def test_partial_batch_success_replays_both_idempotent_collections() -> None:
    async def run() -> None:
        class _Collection:
            def __init__(self, *, fail_first: bool = False) -> None:
                self.fail_first = fail_first
                self.calls = 0

            async def bulk_write(self, _operations, ordered: bool) -> None:
                assert ordered is True
                self.calls += 1
                if self.fail_first and self.calls == 1:
                    raise AutoReconnect("projection connection dropped")

            async def find_one_and_update(self, _query, update, **_kwargs):
                # chat_messages stream-ordinal reservation
                # (KernelEventRepository.reserve_stream_ordinals). Kept out of
                # `calls`, which counts the batch writes under test.
                increment = int((update.get("$inc") or {}).get(MESSAGE_STREAM_COUNTER, 1))
                self.next_stream_seq = getattr(self, "next_stream_seq", 0) + increment
                return {"message_id": "message", MESSAGE_STREAM_COUNTER: self.next_stream_seq}

        inbox = _Collection()
        projections = _Collection(fail_first=True)

        class _Database:
            def __getitem__(self, name: str) -> _Collection:
                return inbox if name == KernelEventRepository.INBOX else projections

        event = SimpleNamespace(
            event_id="event-1",
            session_id="session",
            runtime_id="runtime",
            profile_version="profile",
            cursor=1,
            model_dump=lambda **_kwargs: {"event_id": "event-1", "cursor": 1},
        )
        repository = KernelEventRepository(_Database())
        await repository.persist_batch(
            [
                KernelEventWrite(
                    event=event,
                    projected={"event_id": "event-1", "type": "run.started"},
                )
            ],
            tenant_id="tenant",
            user_id="user",
            conversation_id="conversation",
            message_id="message",
        )

        # The inbox succeeded before the first projection failure. Replaying
        # both stable upserts repairs the projection without duplicating data.
        assert inbox.calls == 2
        assert projections.calls == 2

    asyncio.run(run())
