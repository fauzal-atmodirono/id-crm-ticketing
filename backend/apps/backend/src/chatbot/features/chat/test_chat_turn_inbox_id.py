import inspect
from chatbot.features.chat.router import ChatTurnRequest
from chatbot.features.chat.service import OrchestratorService


def test_chat_turn_request_accepts_inbox_id():
    r = ChatTurnRequest(session_id="s", text="hi", inbox_id=3)
    assert r.inbox_id == 3
    assert ChatTurnRequest(session_id="s", text="hi").inbox_id is None


def test_handle_turn_accepts_inbox_id_param():
    sig = inspect.signature(OrchestratorService.handle_turn)
    assert "inbox_id" in sig.parameters
    assert sig.parameters["inbox_id"].default is None


def test_chat_turn_request_accepts_media_fields():
    req = ChatTurnRequest(
        session_id="s1",
        text="hi",
        audio_base64="abc",
        audio_mime_type="audio/ogg",
    )
    assert req.audio_base64 == "abc"
    assert req.audio_mime_type == "audio/ogg"
    assert req.image_base64 is None
    assert req.image_mime_type is None

    defaults = ChatTurnRequest(session_id="s", text="hi")
    assert defaults.audio_base64 is None
    assert defaults.image_base64 is None


def test_handle_turn_accepts_media_params():
    sig = inspect.signature(OrchestratorService.handle_turn)
    for name in ("audio_base64", "audio_mime_type", "image_base64", "image_mime_type"):
        assert name in sig.parameters
        assert sig.parameters[name].default is None
