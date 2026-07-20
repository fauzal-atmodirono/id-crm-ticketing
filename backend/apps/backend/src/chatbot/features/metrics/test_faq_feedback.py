from datetime import UTC, datetime

import pytest

from chatbot.features.metrics.faq_feedback import FaqFeedback, NoOpFaqFeedback


@pytest.mark.asyncio
async def test_noop_never_raises() -> None:
    fb = FaqFeedback(
        article_id="A1",
        session_id="whatsapp-+60",
        helpful=True,
        score=5,
        at=datetime.now(UTC),
    )
    await NoOpFaqFeedback().record_feedback(fb)  # must not raise
