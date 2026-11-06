import os
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
    for k in ("GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"):
        monkeypatch.setenv(k, "x")
        monkeypatch.delenv(k)
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    configure_vertex_env()
    assert os.environ["GOOGLE_CLOUD_LOCATION"] == "us-central1"
    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "lv-playground-genai"
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION")
    configure_vertex_env()
    assert os.environ["GOOGLE_CLOUD_LOCATION"] == "global"
