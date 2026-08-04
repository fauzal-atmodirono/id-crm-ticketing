"""Transcript fragments batch into readable blocks. Two triggers: a speaker
change (a completed turn), or the flush interval elapsing during a long
monologue. Time is injected so the tests are deterministic."""

from __future__ import annotations

from chatbot.features.chat.phone.transcript_sink import TranscriptSink


def _sink(clock):
    return TranscriptSink(flush_seconds=15.0, now=lambda: clock[0])


def test_nothing_is_due_before_a_turn_completes():
    clock = [0.0]
    s = _sink(clock)
    s.add("USER", "hello ")
    s.add("USER", "there")
    assert s.take_if_due() is None


def test_speaker_change_flushes_the_completed_turn():
    clock = [0.0]
    s = _sink(clock)
    s.add("USER", "hello there")
    s.add("ASSISTANT", "hi")
    block = s.take_if_due()
    assert block == "USER: hello there"


def test_long_monologue_flushes_on_the_timer():
    clock = [0.0]
    s = _sink(clock)
    s.add("USER", "a very long complaint")
    clock[0] = 20.0
    assert s.take_if_due() == "USER: a very long complaint"


def test_taking_twice_does_not_repeat_content():
    clock = [0.0]
    s = _sink(clock)
    s.add("USER", "one")
    s.add("ASSISTANT", "two")
    assert s.take_if_due() == "USER: one"
    assert s.take_if_due() is None


def test_force_flushes_whatever_remains():
    clock = [0.0]
    s = _sink(clock)
    s.add("USER", "trailing words")
    assert s.take_if_due(force=True) == "USER: trailing words"


def test_force_on_empty_buffer_returns_none():
    clock = [0.0]
    s = _sink(clock)
    assert s.take_if_due(force=True) is None


def test_force_after_a_full_take_returns_none():
    clock = [0.0]
    s = _sink(clock)
    s.add("USER", "one")
    s.add("ASSISTANT", "two")
    assert s.take_if_due() == "USER: one"
    assert s.take_if_due(force=True) == "ASSISTANT: two"
    assert s.take_if_due(force=True) is None


def test_rapid_alternating_speakers_format_each_turn_on_its_own_line():
    clock = [0.0]
    s = _sink(clock)
    s.add("USER", "hi")
    s.add("ASSISTANT", "hello")
    s.add("USER", "how are you")
    block = s.take_if_due(force=True)
    assert block == "USER: hi\nASSISTANT: hello\nUSER: how are you"
