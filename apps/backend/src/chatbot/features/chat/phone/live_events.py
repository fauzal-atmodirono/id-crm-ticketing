"""Normalized Gemini Live events, decoupling the bridge from the raw SDK shape."""

from __future__ import annotations

from dataclasses import dataclass, field

from google.genai import types


@dataclass(frozen=True)
class AudioOut:
    pcm: bytes  # PCM16 24 kHz from Gemini


@dataclass(frozen=True)
class InputTranscript:
    text: str


@dataclass(frozen=True)
class OutputTranscript:
    text: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Interrupted:
    pass


@dataclass(frozen=True)
class TurnComplete:
    pass


LiveEvent = AudioOut | InputTranscript | OutputTranscript | ToolCall | Interrupted | TurnComplete


def normalize_server_message(msg: types.LiveServerMessage) -> list[LiveEvent]:
    """Map one raw LiveServerMessage to zero or more normalized events."""
    events: list[LiveEvent] = []
    sc = msg.server_content
    if sc is not None:
        if sc.model_turn is not None:
            for part in sc.model_turn.parts or []:
                blob = part.inline_data
                if blob is not None and blob.data:
                    events.append(AudioOut(blob.data))
        if sc.input_transcription is not None and sc.input_transcription.text:
            events.append(InputTranscript(sc.input_transcription.text))
        if sc.output_transcription is not None and sc.output_transcription.text:
            events.append(OutputTranscript(sc.output_transcription.text))
        if sc.interrupted:
            events.append(Interrupted())
        if sc.turn_complete:
            events.append(TurnComplete())
    if msg.tool_call is not None:
        for fc in msg.tool_call.function_calls or []:
            events.append(ToolCall(id=fc.id or "", name=fc.name or "", args=dict(fc.args or {})))
    return events
