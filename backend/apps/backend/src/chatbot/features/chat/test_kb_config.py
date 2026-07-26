# test_kb_config.py
from chatbot.platform.config import Settings


def test_knowledge_pg_defaults_off() -> None:
    s = Settings()
    assert s.knowledge_pg_enabled is False
    assert s.knowledge_database_url == ""
    assert s.kb_chunk_size_tokens == 800
    assert s.kb_chunk_overlap_tokens == 100
    assert s.kb_score_floor == 0.55


def test_knowledge_pg_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_PG_ENABLED", "true")
    monkeypatch.setenv("KNOWLEDGE_DATABASE_URL", "postgresql://x/y")
    s = Settings()
    assert s.knowledge_pg_enabled is True
    assert s.knowledge_database_url == "postgresql://x/y"
