"""
Unit tests for telemetry functionality across Gateway and Backend.

Covers:
- TelemetryProducer  (gateway/telemetry.py)
- build_telemetry_event helper  (gateway/telemetry.py)
- process_telemetry_batch Celery task  (backend/core/tasks.py)
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis.asyncio as aioredis

from gateway.telemetry import (
    REDIS_TELEMETRY_KEY,
    TelemetryProducer,
    build_telemetry_event,
)


# ---------------------------------------------------------------------------
# TestTelemetryProducer
# ---------------------------------------------------------------------------

class TestTelemetryProducer:
    """Tests for the TelemetryProducer class in gateway/telemetry.py."""

    @pytest.fixture()
    def mock_redis(self) -> AsyncMock:
        """Async Redis client mock with pipeline support."""
        client = AsyncMock(spec=aioredis.Redis)
        pipe = AsyncMock()
        pipe.lpush = MagicMock()
        pipe.execute = AsyncMock(return_value=[])
        pipe.__aenter__ = AsyncMock(return_value=pipe)
        pipe.__aexit__ = AsyncMock(return_value=False)
        client.pipeline = MagicMock(return_value=pipe)
        return client

    @pytest.fixture()
    def producer(self, mock_redis: AsyncMock) -> TelemetryProducer:
        """TelemetryProducer wired to a mock Redis client."""
        return TelemetryProducer(
            redis_client=mock_redis,
            flush_interval=1.0,
            max_buffer_size=10,
        )

    def test_emit_adds_event_to_buffer(self, producer: TelemetryProducer) -> None:
        event = {"event_type": "request", "model": "gpt-4o-mini"}
        producer.emit(event)
        assert len(producer._buffer) == 1
        assert producer._buffer[0]["event_type"] == "request"

    def test_emit_adds_timestamp_when_missing(self, producer: TelemetryProducer) -> None:
        event = {"event_type": "request"}
        producer.emit(event)
        assert "timestamp" in producer._buffer[0]
        stored_ts = producer._buffer[0]["timestamp"]
        parsed = datetime.fromisoformat(stored_ts)
        assert parsed.tzinfo is not None

    def test_emit_preserves_existing_timestamp(self, producer: TelemetryProducer) -> None:
        fixed_ts = "2025-01-15T12:00:00+00:00"
        event = {"event_type": "request", "timestamp": fixed_ts}
        producer.emit(event)
        assert producer._buffer[0]["timestamp"] == fixed_ts

    def test_emit_never_raises_on_unexpected_error(
        self, mock_redis: AsyncMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        producer = TelemetryProducer(
            redis_client=mock_redis,
            flush_interval=1.0,
            max_buffer_size=10,
        )
        broken_buffer = MagicMock()
        broken_buffer.append.side_effect = RuntimeError("Simulated failure")
        producer._buffer = broken_buffer

        producer.emit({"event_type": "test"})

        broken_buffer.append.assert_called_once()

    def test_buffer_overflow_drops_oldest_events(
        self, producer: TelemetryProducer
    ) -> None:
        for i in range(25):
            producer.emit({"event_type": "request", "seq": i})

        assert len(producer._buffer) <= producer._max_buffer_size * 2

        remaining_seqs = [evt["seq"] for evt in producer._buffer]
        assert remaining_seqs[-1] == 24
        assert remaining_seqs[0] > 0

    @pytest.mark.asyncio
    async def test_flush_to_redis_calls_lpush_via_pipeline(
        self, producer: TelemetryProducer, mock_redis: AsyncMock
    ) -> None:
        producer.emit({"event_type": "request", "model": "gpt-4o-mini"})
        producer.emit({"event_type": "block", "model": "gpt-4o-mini"})

        await producer._flush_to_redis()

        pipe = mock_redis.pipeline.return_value
        pipe.__aenter__.assert_awaited()
        pipe_ctx = pipe.__aenter__.return_value
        assert pipe_ctx.lpush.call_count == 2

        first_call_args = pipe_ctx.lpush.call_args_list[0]
        assert first_call_args[0][0] == REDIS_TELEMETRY_KEY
        parsed = json.loads(first_call_args[0][1])
        assert parsed["event_type"] == "request"

    @pytest.mark.asyncio
    async def test_flush_to_redis_clears_buffer(
        self, producer: TelemetryProducer
    ) -> None:
        producer.emit({"event_type": "request"})
        producer.emit({"event_type": "block"})
        assert len(producer._buffer) == 2

        await producer._flush_to_redis()
        assert len(producer._buffer) == 0

    @pytest.mark.asyncio
    async def test_flush_to_redis_handles_redis_error_gracefully(
        self, producer: TelemetryProducer, mock_redis: AsyncMock
    ) -> None:
        pipe = mock_redis.pipeline.return_value
        pipe.__aenter__.return_value.execute = AsyncMock(
            side_effect=aioredis.RedisError("Connection lost")
        )

        producer.emit({"event_type": "request"})

        await producer._flush_to_redis()

        assert len(producer._buffer) == 0

    @pytest.mark.asyncio
    async def test_flush_to_redis_noop_when_buffer_empty(
        self, producer: TelemetryProducer, mock_redis: AsyncMock
    ) -> None:
        assert len(producer._buffer) == 0
        await producer._flush_to_redis()
        mock_redis.pipeline.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_begins_background_flush_loop(
        self, producer: TelemetryProducer
    ) -> None:
        await producer.start()

        assert producer._running is True
        assert producer._flush_task is not None
        assert not producer._flush_task.done()

        producer._running = False
        producer._flush_task.cancel()
        try:
            await producer._flush_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_start_idempotent(self, producer: TelemetryProducer) -> None:
        await producer.start()
        first_task = producer._flush_task

        await producer.start()
        assert producer._flush_task is first_task

        producer._running = False
        producer._flush_task.cancel()
        try:
            await producer._flush_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_stop_does_final_flush_and_cancels_task(
        self, producer: TelemetryProducer, mock_redis: AsyncMock
    ) -> None:
        await producer.start()
        producer.emit({"event_type": "request"})

        await producer.stop()

        assert producer._running is False
        assert producer._flush_task.cancelled() or producer._flush_task.done()

        pipe = mock_redis.pipeline.return_value
        pipe.__aenter__.return_value.lpush.assert_called()


# ---------------------------------------------------------------------------
# TestBuildTelemetryEvent
# ---------------------------------------------------------------------------

class TestBuildTelemetryEvent:
    """Tests for the build_telemetry_event() helper function."""

    def test_returns_dict_with_all_expected_keys(self) -> None:
        event = build_telemetry_event(event_type="request")
        expected_keys = {
            "timestamp",
            "event_type",
            "model",
            "user_id",
            "project_id",
            "key_prefix",
            "prompt_hash",
            "latency_ms",
            "risk_score",
            "action",
            "threat_type",
            "tokens_used",
            "compliance_tags",
            "metadata",
            "prompt_snippet",
            "endpoint_id",
            "pipeline_stage",
            "intent",
            "organization_id",
            "method",
            "source_ip",
            "user_agent",
            "status_code",
        }
        assert set(event.keys()) == expected_keys

    def test_sets_timestamp_automatically(self) -> None:
        before = datetime.now(timezone.utc)
        event = build_telemetry_event(event_type="request")
        after = datetime.now(timezone.utc)

        event_ts = datetime.fromisoformat(event["timestamp"])
        assert before <= event_ts <= after

    def test_default_values_for_optional_fields(self) -> None:
        event = build_telemetry_event(event_type="scan_hit")

        assert event["event_type"] == "scan_hit"
        assert event["model"] == ""
        assert event["user_id"] is None
        assert event["project_id"] == ""
        assert event["key_prefix"] == ""
        assert event["prompt_hash"] == ""
        assert event["latency_ms"] == 0.0
        assert event["risk_score"] == 0.0
        assert event["action"] == "allow"
        assert event["threat_type"] == ""
        assert event["tokens_used"] == {}
        assert event["compliance_tags"] == []
        assert event["metadata"] == {}

    def test_custom_values_override_defaults(self) -> None:
        tokens = {"prompt_tokens": 100, "completion_tokens": 50}
        tags = ["SOC2", "GDPR"]
        meta = {"scanner": "presidio", "version": "2.0"}

        event = build_telemetry_event(
            event_type="block",
            model="gpt-4o-mini",
            user_id=42,
            project_id="proj-001",
            key_prefix="zs_prod_",
            prompt_hash="abc123",
            latency_ms=150.567,
            risk_score=0.85,
            action="block",
            threat_type="prompt_injection",
            tokens_used=tokens,
            compliance_tags=tags,
            metadata=meta,
        )

        assert event["event_type"] == "block"
        assert event["model"] == "gpt-4o-mini"
        assert event["user_id"] == 42
        assert event["project_id"] == "proj-001"
        assert event["key_prefix"] == "zs_prod_"
        assert event["prompt_hash"] == "abc123"
        assert event["latency_ms"] == 150.57
        assert event["risk_score"] == 0.85
        assert event["action"] == "block"
        assert event["threat_type"] == "prompt_injection"
        assert event["tokens_used"] == tokens
        assert event["compliance_tags"] == tags
        assert event["metadata"] == meta


# ---------------------------------------------------------------------------
# TestTelemetryBatchConsumer
# ---------------------------------------------------------------------------

class TestTelemetryBatchConsumer:
    """Tests for process_telemetry_batch Celery task in backend/core/tasks.py."""

    @patch("core.tasks.redis.Redis")
    @patch("policy.models.EnforcementEvent")
    @patch("django.conf.settings", new_callable=MagicMock)
    def test_drains_events_from_redis(
        self,
        mock_settings: MagicMock,
        mock_enforcement_event_cls: MagicMock,
        mock_redis_cls: MagicMock,
    ) -> None:
        from core.tasks import process_telemetry_batch

        mock_settings.REDIS_URL = "redis://localhost:6379/0"

        events = [
            json.dumps({
                "event_type": "request",
                "action": "allow",
                "model": "gpt-4o-mini",
                "user_id": 1,
                "organization_id": 101,
            }),
            json.dumps({
                "event_type": "block",
                "action": "block",
                "model": "gpt-4o-mini",
                "user_id": 2,
                "threat_type": "prompt_injection",
                "organization_id": 101,
            }),
            None,
        ]

        mock_client = MagicMock()
        mock_redis_cls.from_url.return_value = mock_client
        mock_client.eval = MagicMock(return_value=[events[0], events[1]])
        mock_client.delete = MagicMock()

        mock_enforcement_event_cls.return_value = MagicMock()
        mock_enforcement_event_cls.objects = MagicMock()
        mock_enforcement_event_cls.objects.bulk_create = MagicMock()

        result = process_telemetry_batch(batch_size=50)

        assert result == 2
        mock_client.eval.assert_called_once()
        mock_enforcement_event_cls.objects.bulk_create.assert_called_once()
        created_batch = mock_enforcement_event_cls.objects.bulk_create.call_args[0][0]
        assert len(created_batch) == 2

    @patch("core.tasks.redis.Redis")
    @patch("policy.models.EnforcementEvent")
    @patch("django.conf.settings", new_callable=MagicMock)
    def test_handles_malformed_json_gracefully(
        self,
        mock_settings: MagicMock,
        mock_enforcement_event_cls: MagicMock,
        mock_redis_cls: MagicMock,
    ) -> None:
        from core.tasks import process_telemetry_batch

        mock_settings.REDIS_URL = "redis://localhost:6379/0"

        events = [
            "NOT_VALID_JSON{{{",
            json.dumps({"event_type": "request", "action": "allow", "organization_id": 101}),
            None,
        ]

        mock_client = MagicMock()
        mock_redis_cls.from_url.return_value = mock_client
        mock_client.eval = MagicMock(return_value=[events[0], events[1]])
        mock_client.delete = MagicMock()

        mock_enforcement_event_cls.return_value = MagicMock()
        mock_enforcement_event_cls.objects = MagicMock()
        mock_enforcement_event_cls.objects.bulk_create = MagicMock()

        result = process_telemetry_batch(batch_size=50)

        assert result == 1
        mock_enforcement_event_cls.objects.bulk_create.assert_called_once()

    @patch("core.tasks.redis.Redis")
    @patch("policy.models.EnforcementEvent")
    @patch("django.conf.settings", new_callable=MagicMock)
    def test_skips_unscoped_events_without_organization_id(
        self,
        mock_settings: MagicMock,
        mock_enforcement_event_cls: MagicMock,
        mock_redis_cls: MagicMock,
    ) -> None:
        from core.tasks import process_telemetry_batch

        mock_settings.REDIS_URL = "redis://localhost:6379/0"

        events = [
            json.dumps({"event_type": "request", "action": "allow", "user_id": 1}),
            None,
        ]

        mock_client = MagicMock()
        mock_redis_cls.from_url.return_value = mock_client
        mock_client.eval = MagicMock(return_value=[events[0]])
        mock_client.delete = MagicMock()

        mock_enforcement_event_cls.return_value = MagicMock()
        mock_enforcement_event_cls.objects = MagicMock()
        mock_enforcement_event_cls.objects.bulk_create = MagicMock()

        result = process_telemetry_batch(batch_size=50)

        assert result == 0
        mock_enforcement_event_cls.objects.bulk_create.assert_not_called()

    @patch("core.tasks.redis.Redis")
    @patch("policy.models.EnforcementEvent")
    @patch("django.conf.settings", new_callable=MagicMock)
    def test_handles_empty_redis_list(
        self,
        mock_settings: MagicMock,
        mock_enforcement_event_cls: MagicMock,
        mock_redis_cls: MagicMock,
    ) -> None:
        from core.tasks import process_telemetry_batch

        mock_settings.REDIS_URL = "redis://localhost:6379/0"

        mock_client = MagicMock()
        mock_redis_cls.from_url.return_value = mock_client
        mock_client.eval = MagicMock(return_value=[])

        result = process_telemetry_batch(batch_size=50)

        assert result == 0
        mock_enforcement_event_cls.objects.bulk_create.assert_not_called()

    @patch("core.tasks.redis.Redis")
    @patch("django.conf.settings", new_callable=MagicMock)
    def test_handles_redis_connection_failure(
        self,
        mock_settings: MagicMock,
        mock_redis_cls: MagicMock,
    ) -> None:
        import redis as sync_redis

        from core.tasks import process_telemetry_batch

        mock_settings.REDIS_URL = "redis://localhost:6379/0"

        mock_redis_cls.from_url.side_effect = sync_redis.RedisError(
            "Connection refused"
        )

        result = process_telemetry_batch(batch_size=50)

        assert result == 0
