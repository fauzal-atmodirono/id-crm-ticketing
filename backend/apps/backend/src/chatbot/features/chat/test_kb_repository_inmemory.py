from chatbot.features.chat.kb_repository import InMemoryKbRepository


async def test_create_list_delete_roundtrip() -> None:
    repo = InMemoryKbRepository()
    doc_id = await repo.create_document(
        title="Warranty", source_type="text",
        original_filename=None, mime_type=None, char_count=10,
    )
    docs = await repo.list_documents()
    assert len(docs) == 1 and docs[0].status == "pending" and docs[0].chunk_count == 0

    await repo.add_chunks(doc_id, [(0, "warranty is 5 years", [1.0, 0.0], 19)])
    await repo.set_status(doc_id, "indexed")
    docs = await repo.list_documents()
    assert docs[0].status == "indexed" and docs[0].chunk_count == 1

    assert await repo.delete_document(doc_id) is True
    assert await repo.list_documents() == []


async def test_search_ranks_by_cosine() -> None:
    repo = InMemoryKbRepository()
    d = await repo.create_document(
        title="Doc", source_type="text",
        original_filename=None, mime_type=None, char_count=0,
    )
    await repo.add_chunks(d, [
        (0, "near", [1.0, 0.0], 4),
        (1, "far", [0.0, 1.0], 3),
    ])
    await repo.set_status(d, "indexed")
    hits = await repo.search_chunks([1.0, 0.0], limit=2)
    assert hits[0].content == "near"
    assert hits[0].score > hits[1].score
