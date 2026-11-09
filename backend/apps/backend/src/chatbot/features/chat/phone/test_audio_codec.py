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
    # the non-padded portion is the genuine resampler output, not zero-fill
    expected_body = audioop.ratecv(audioop.ulaw2lin(mulaw, 2), 2, 1, 8000, 16000, None)[0]
    assert pcm[: len(expected_body)] == expected_body
    assert len(expected_body) >= 638  # ratecv yields 638-640; padding tops up to 640


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


def test_wav_to_mulaw8k_resamples_tts_output() -> None:
    import io
    import wave

    from chatbot.features.chat.phone.audio_codec import wav_to_mulaw8k

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x10\x00" * 24000)  # 1 s
    out = wav_to_mulaw8k(buf.getvalue())
    assert abs(len(out) - 8000) <= 2  # 1 s at 8 kHz, 1 byte per sample


def test_wav_to_mulaw8k_rejects_non_wav() -> None:
    import pytest

    from chatbot.features.chat.phone.audio_codec import wav_to_mulaw8k

    with pytest.raises(ValueError):
        wav_to_mulaw8k(b"ID3 not a wav")
