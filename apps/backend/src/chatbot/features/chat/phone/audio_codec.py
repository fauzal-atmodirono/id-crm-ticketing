"""Pure audio conversions between Twilio (μ-law 8 kHz) and Gemini Live (PCM).

Gemini Live input is 16 kHz PCM16; output is 24 kHz PCM16. Twilio Media Streams
is 8 kHz μ-law. Stateless per-frame resampling (audioop.ratecv state=None) is
sufficient for short 20 ms frames in this POC.

NOTE: `audioop` is deprecated in 3.12 and removed in 3.13. If the runtime moves
to 3.13, swap to the `audioop-lts` backport (drop-in) or a numpy implementation.
"""

from __future__ import annotations

import audioop

_WIDTH = 2  # 16-bit PCM
_CHANNELS = 1
_MAX_RESAMPLE_SHORTFALL = 2  # at most 1 sample (2 bytes) due to ratecv filter startup


def mulaw8k_to_pcm16k(mulaw: bytes) -> bytes:
    """Twilio inbound μ-law 8 kHz → PCM16 16 kHz for Gemini Live input."""
    pcm8k = audioop.ulaw2lin(mulaw, _WIDTH)
    pcm16k, _ = audioop.ratecv(pcm8k, _WIDTH, _CHANNELS, 8000, 16000, None)
    # ratecv may produce one sample fewer due to filter startup latency;
    # zero-pad to the exact 2x sample count. The shortfall is expected to be at
    # most one sample (2 bytes) — anything larger means a malformed input, so fail loudly.
    target = len(pcm8k) * 2
    shortfall = target - len(pcm16k)
    if shortfall > 0:
        if shortfall > _MAX_RESAMPLE_SHORTFALL:
            raise ValueError(f"unexpected resample shortfall: {shortfall} bytes")
        pcm16k += b"\x00" * shortfall
    return pcm16k


def pcm24k_to_mulaw8k(pcm: bytes) -> bytes:
    """Gemini Live output PCM16 24 kHz → μ-law 8 kHz for Twilio playback."""
    pcm8k, _ = audioop.ratecv(pcm, _WIDTH, _CHANNELS, 24000, 8000, None)
    return audioop.lin2ulaw(pcm8k, _WIDTH)
