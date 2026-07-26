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
