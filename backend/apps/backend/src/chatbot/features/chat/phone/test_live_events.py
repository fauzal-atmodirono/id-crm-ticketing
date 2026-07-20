from google.genai import types

from chatbot.features.chat.phone.live_events import (
    AudioOut,
    InputTranscript,
    Interrupted,
    OutputTranscript,
    ToolCall,
    TurnComplete,
    normalize_server_message,
)


def _audio_part(pcm: bytes) -> types.Part:
    return types.Part(inline_data=types.Blob(data=pcm, mime_type="audio/pcm;rate=24000"))


def test_normalize_extracts_audio_from_model_turn() -> None:
    msg = types.LiveServerMessage(
        server_content=types.LiveServerContent(
            model_turn=types.Content(parts=[_audio_part(b"\x01\x02")])
        )
    )
    events = normalize_server_message(msg)
    assert AudioOut(b"\x01\x02") in events


def test_normalize_extracts_output_transcript_and_interrupt() -> None:
    msg = types.LiveServerMessage(
        server_content=types.LiveServerContent(
            output_transcription=types.Transcription(text="hello"),
            interrupted=True,
        )
    )
    events = normalize_server_message(msg)
    assert OutputTranscript("hello") in events
    assert Interrupted() in events


def test_normalize_extracts_input_transcript_and_turn_complete() -> None:
    msg = types.LiveServerMessage(
        server_content=types.LiveServerContent(
            input_transcription=types.Transcription(text="what is the warranty"),
            turn_complete=True,
        )
    )
    events = normalize_server_message(msg)
    assert InputTranscript("what is the warranty") in events
    assert TurnComplete() in events


def test_normalize_extracts_tool_call() -> None:
    msg = types.LiveServerMessage(
        tool_call=types.LiveServerToolCall(
            function_calls=[types.FunctionCall(id="c1", name="kb_search", args={"query": "x"})]
        )
    )
    events = normalize_server_message(msg)
    assert ToolCall(id="c1", name="kb_search", args={"query": "x"}) in events
