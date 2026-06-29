from __future__ import annotations

from chatbot.features.chat.phone.rate_limit import RateLimiter


def test_allows_up_to_max_then_blocks() -> None:
    clock = {"t": 0.0}
    rl = RateLimiter(max_calls=3, window_seconds=60, now=lambda: clock["t"])
    assert [rl.allow("ip") for _ in range(3)] == [True, True, True]
    assert rl.allow("ip") is False  # 4th within the window is blocked


def test_window_slides_and_frees_capacity() -> None:
    clock = {"t": 0.0}
    rl = RateLimiter(max_calls=2, window_seconds=60, now=lambda: clock["t"])
    assert rl.allow("ip") is True
    assert rl.allow("ip") is True
    assert rl.allow("ip") is False
    clock["t"] = 61.0  # the first two calls are now outside the window
    assert rl.allow("ip") is True


def test_keys_are_independent() -> None:
    rl = RateLimiter(max_calls=1, window_seconds=60, now=lambda: 0.0)
    assert rl.allow("1.2.3.4") is True
    assert rl.allow("5.6.7.8") is True
    assert rl.allow("1.2.3.4") is False  # first key exhausted, second unaffected
