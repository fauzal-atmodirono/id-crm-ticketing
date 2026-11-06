import pytest

import serve

NAMES = {"uc2": "logisly_purchasing", "uc3": "logisly_product_manager"}


@pytest.mark.parametrize("which", ["uc2", "uc3"])
def test_card_advertises_file_inputs(which):
    card = serve.build_card(serve._root_agent(which), "https://example.run.app:443/")
    assert card.name == NAMES[which]
    assert card.default_input_modes == serve.INPUT_MODES
    for mode in ("application/pdf", "message/rfc822", "image/png", "image/jpeg"):
        assert mode in card.default_input_modes
    assert any("spreadsheetml" in m for m in card.default_input_modes)
    assert any("wordprocessingml" in m for m in card.default_input_modes)
    assert card.url.startswith("https://example.run.app")
    assert len(card.skills) == 1
    skill = card.skills[0]
    assert skill.id == NAMES[which]
    assert skill.input_modes == serve.INPUT_MODES
    assert skill.name and skill.tags
    # A short summary, not the full instruction or a tool list.
    assert len(skill.description) < 300
    assert "Urutan kerja" not in skill.description and "Alur kerja" not in skill.description
    assert "\u2014" not in skill.description and "Proton" not in skill.description


@pytest.mark.parametrize("which", ["uc2", "uc3"])
def test_app_builds(which, monkeypatch):
    monkeypatch.setenv("LOGISLY_AGENT", which)
    monkeypatch.setenv("LOGISLY_PUBLIC_URL", "https://x.run.app")
    assert serve.build_app() is not None
