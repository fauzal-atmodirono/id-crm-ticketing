"""The idle-warning message interpolates the effective close grace via {{minutes}}."""

from app.services import lifecycle


def test_render_replaces_minutes_token():
    out = lifecycle.render_idle_warning(
        "Your chat will close in {{minutes}} minutes if we do not hear from you.", 10
    )
    assert out == "Your chat will close in 10 minutes if we do not hear from you."


def test_render_without_token_is_unchanged():
    assert lifecycle.render_idle_warning("No token here.", 7) == "No token here."


def test_default_warning_contains_the_token():
    # The shipped default must carry the token so the value is dynamic.
    assert "{{minutes}}" in lifecycle.IDLE_WARNING_DEFAULT
