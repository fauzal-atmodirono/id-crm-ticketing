from datetime import date

from logisly_agents.config import configure_vertex_env, get_settings


def test_defaults_point_at_demo_data():
    s = get_settings()
    assert (s.data_dir / "uc2" / "business_rules.yaml").exists()
    assert s.model == "gemini-3.5-flash"


def test_env_override(monkeypatch):
    monkeypatch.setenv("LOGISLY_AS_OF", "2026-12-01")
    assert get_settings().as_of == date(2026, 12, 1)


def test_configure_vertex_env_keeps_existing(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    configure_vertex_env()
    import os

    assert os.environ["GOOGLE_CLOUD_LOCATION"] == "us-central1"
    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "lv-playground-genai"
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION")
    configure_vertex_env()
    assert os.environ["GOOGLE_CLOUD_LOCATION"] == "global"
