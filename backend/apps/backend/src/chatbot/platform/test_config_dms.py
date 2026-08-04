"""DMS/TSP integration shell settings.

`DMS_MOCK_CLIENT_ENABLED` is the single flag between fabricated demo records
and a real customer's Customer 360 panel: Phase 1 ships no real DMS adapter,
so it is the only thing that can put anything at all in the `dms` block. It
used to be a raw `os.getenv` in main.py -- not a Settings field, not in
`deploy/tenants/example.env`, and untested -- which made it invisible to
anyone auditing a tenant's env and hard to find for anyone who needed to turn
it off.
"""

from chatbot.platform.config import Settings


def test_dms_mock_client_is_off_by_default() -> None:
    assert Settings().dms_mock_client_enabled is False


def test_dms_mock_client_can_be_enabled_by_env(monkeypatch) -> None:
    """Pins that the field maps to the documented env var name verbatim --
    a rename on either side would silently leave the flag stuck off (or, if
    the default ever flipped, stuck on)."""
    monkeypatch.setenv("DMS_MOCK_CLIENT_ENABLED", "true")
    assert Settings().dms_mock_client_enabled is True
