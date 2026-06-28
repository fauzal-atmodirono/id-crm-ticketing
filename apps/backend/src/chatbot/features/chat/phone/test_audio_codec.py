import audioop

from chatbot.features.chat.phone.audio_codec import (
    mulaw8k_to_pcm16k,
    pcm24k_to_mulaw8k,
)


def test_mulaw8k_to_pcm16k_doubles_sample_count() -> None:
    # 160 μ-law bytes = 160 samples @ 8kHz (20ms). PCM16 @16kHz = 320 samples * 2 bytes.
    mulaw = b"\xff" * 160
    pcm = mulaw8k_to_pcm16k(mulaw)
    assert len(pcm) == 640  # 320 samples * 2 bytes


def test_pcm24k_to_mulaw8k_thirds_sample_count() -> None:
    # 240 samples @24kHz PCM16 (480 bytes, 10ms) -> 80 samples @8kHz μ-law (80 bytes).
    pcm = b"\x00\x00" * 240
    mulaw = pcm24k_to_mulaw8k(pcm)
    assert len(mulaw) == 80


def test_roundtrip_is_audio_shaped() -> None:
    # A non-trivial tone survives a round trip without raising and stays non-empty.
    pcm16k = audioop.lin2lin(b"\x10\x20" * 160, 2, 2)
    mulaw = pcm24k_to_mulaw8k(audioop.ratecv(pcm16k, 2, 1, 16000, 24000, None)[0])
    assert len(mulaw) > 0
    back = mulaw8k_to_pcm16k(mulaw)
    assert len(back) > 0
