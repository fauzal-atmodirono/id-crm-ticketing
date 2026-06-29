from datetime import UTC, datetime

from chatbot.features.chat.adapters.bigquery_metrics import NoOpMetrics
from chatbot.features.metrics.events import build_turn_event


async def test_noop_emit_turn_is_a_noop() -> None:
    ev = build_turn_event("sim-1", datetime.now(UTC), 100, 1, False, False)
    # Must not raise and must return None.
    assert await NoOpMetrics().emit_turn(ev) is None
