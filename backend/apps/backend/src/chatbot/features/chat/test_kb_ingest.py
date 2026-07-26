# test_kb_ingest.py
from chatbot.features.chat.kb_ingest import ingest_file_document, ingest_text_document
from chatbot.features.chat.kb_repository import InMemoryKbRepository


class _Embedder:
    async def embed(self, text): return [float(len(text)), 1.0]


class _FailEmbedder:
    async def embed(self, text): return []


async def _new_doc(repo):
    return await repo.create_document(
        title="T", source_type="text",
        original_filename=None, mime_type=None, char_count=0,
    )


async def test_ingest_text_marks_indexed_with_chunks() -> None:
    repo = InMemoryKbRepository()
    doc_id = await _new_doc(repo)
    await ingest_text_document(repo, _Embedder(), doc_id, "hello world " * 50,
                               max_chars=40, overlap_chars=8)
    row = await repo.get_document(doc_id)
    assert row.status == "indexed" and row.chunk_count > 1


async def test_ingest_empty_text_marks_failed() -> None:
    repo = InMemoryKbRepository()
    doc_id = await _new_doc(repo)
    await ingest_text_document(repo, _Embedder(), doc_id, "   ",
                               max_chars=40, overlap_chars=8)
    row = await repo.get_document(doc_id)
    assert row.status == "failed"


async def test_ingest_embedding_failure_marks_failed() -> None:
    repo = InMemoryKbRepository()
    doc_id = await _new_doc(repo)
    await ingest_text_document(repo, _FailEmbedder(), doc_id, "some real text",
                               max_chars=40, overlap_chars=8)
    row = await repo.get_document(doc_id)
    assert row.status == "failed"


async def test_ingest_unsupported_file_marks_failed() -> None:
    repo = InMemoryKbRepository()
    doc_id = await _new_doc(repo)
    await ingest_file_document(repo, _Embedder(), doc_id, "x.png", "image/png",
                               b"\x89PNG", max_chars=40, overlap_chars=8)
    row = await repo.get_document(doc_id)
    assert row.status == "failed"
