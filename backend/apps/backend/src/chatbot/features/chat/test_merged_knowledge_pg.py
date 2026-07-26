# test_merged_knowledge_pg.py
from chatbot.features.chat.adapters.merged_knowledge import MergedKnowledgeAdapter
from chatbot.features.chat.models import KbArticle


class _Base:
    async def search_kb(self, query, limit=2):
        return [KbArticle(title="Base", content="from-vertex", url=None)]


class _Pg:
    async def search_kb(self, query, limit=2):
        return [KbArticle(title="PgDoc", content="from-pgvector", url=None, source_type="pgvector")]


async def test_pg_results_included_and_first() -> None:
    merged = MergedKnowledgeAdapter(_Base(), None, None, pg_port=_Pg())
    out = await merged.search_kb("q", limit=5)
    titles = [a.title for a in out]
    assert "PgDoc" in titles and "Base" in titles
    assert titles[0] == "PgDoc"  # operator-authored pgvector ranks first


async def test_no_pg_port_is_backwards_compatible() -> None:
    merged = MergedKnowledgeAdapter(_Base(), None, None)
    out = await merged.search_kb("q", limit=5)
    assert [a.title for a in out] == ["Base"]
