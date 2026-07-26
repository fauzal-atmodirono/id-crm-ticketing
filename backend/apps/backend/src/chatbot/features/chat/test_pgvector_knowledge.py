# test_pgvector_knowledge.py
from chatbot.features.chat.adapters.pgvector_knowledge import PgVectorKnowledgeAdapter
from chatbot.features.chat.kb_repository import ChunkHit


class _Embedder:
    def __init__(self, vec): self._vec = vec
    async def embed(self, text): return self._vec


class _Repo:
    def __init__(self, hits): self._hits = hits
    async def search_chunks(self, embedding, limit): return self._hits[:limit]


class _FailingEmbedder:
    async def embed(self, text): return []


async def test_returns_best_chunk_per_document_above_floor() -> None:
    repo = _Repo([
        ChunkHit("Warranty", "chunk-a", 0.90),
        ChunkHit("Warranty", "chunk-b", 0.70),   # same doc, lower score -> dropped
        ChunkHit("Pricing", "chunk-c", 0.60),
        ChunkHit("Noise", "chunk-d", 0.40),      # below floor -> dropped
    ])
    adapter = PgVectorKnowledgeAdapter(repo, _Embedder([1.0]), score_floor=0.55)
    out = await adapter.search_kb("q", limit=5)
    titles = [a.title for a in out]
    assert titles == ["Warranty", "Pricing"]
    assert out[0].content == "chunk-a"
    assert out[0].source_type == "pgvector"


async def test_empty_embedding_returns_empty() -> None:
    adapter = PgVectorKnowledgeAdapter(_Repo([]), _FailingEmbedder(), score_floor=0.55)
    assert await adapter.search_kb("q") == []
