"""The video size cap must have a WhatsApp-sized default so an oversized clip
is skipped rather than sent to Gemini and rejected mid-turn."""

from app.config import get_settings


def test_video_max_bytes_defaults_to_whatsapp_limit():
    assert get_settings().whatsapp_video_max_bytes == 16 * 1024 * 1024
