from app.config import get_settings


async def test_scanner_started_when_enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "lifecycle_enabled", True, raising=False)
    started = {"count": 0}

    async def fake_scanner():
        started["count"] += 1
        # Sleep forever so the lifespan has to cancel us.
        import asyncio

        await asyncio.Event().wait()

    from app.services import lifecycle_scanner

    monkeypatch.setattr(lifecycle_scanner, "run_scanner", fake_scanner)

    from app.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        # Give the created task a chance to start.
        import asyncio

        await asyncio.sleep(0)
    assert started["count"] == 1


async def test_scanner_not_started_when_disabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "lifecycle_enabled", False, raising=False)
    started = {"count": 0}

    async def fake_scanner():
        started["count"] += 1

    from app.services import lifecycle_scanner

    monkeypatch.setattr(lifecycle_scanner, "run_scanner", fake_scanner)

    from app.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        pass
    assert started["count"] == 0
