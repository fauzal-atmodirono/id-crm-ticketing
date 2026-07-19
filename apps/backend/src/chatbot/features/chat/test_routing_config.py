from chatbot.platform.config import Settings


def test_routing_defaults_off() -> None:
    s = Settings()
    assert s.routing_enabled is False
    assert s.routing_admin_api_key == ""


def test_routing_can_be_enabled() -> None:
    s = Settings(routing_enabled=True, routing_admin_api_key="secret")
    assert s.routing_enabled is True
    assert s.routing_admin_api_key == "secret"
