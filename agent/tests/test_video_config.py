"""The inline-media budget must leave room for base64 inflation: google-genai
sends inline_data as base64 in a JSON REST body (~1.335x) against Gemini's
~20 MB inline request cap, so the raw cap has to sit well under 20 MB — 16 MB
would encode to ~21.4 MB and be rejected mid-turn, which is exactly what this
guard exists to prevent."""

from app.config import get_settings


def test_video_max_bytes_leaves_headroom_for_base64_inflation():
    cap = get_settings().whatsapp_video_max_bytes
    assert cap == 14 * 1024 * 1024
    # Encoded size must stay under Gemini's ~20 MB inline request limit.
    assert cap * 4 / 3 < 20 * 1000 * 1000
