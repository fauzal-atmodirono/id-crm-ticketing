import pytest
from google.adk.runners import InMemoryRunner
from google.genai import types

from logisly_agents.config import configure_vertex_env, get_settings
from logisly_agents.uc3.context import read_context
from logisly_agents.uc3.jira import MockJira

pytestmark = pytest.mark.live

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
APP = "uc3_live"


def _parts() -> list[types.Part]:
    src = get_settings().data_dir / "uc3" / "sources"
    parts = [
        types.Part.from_text(
            text="Siapkan PRD checklist follow-up driver per milestone dan backlog Jira dari input ini."
        )
    ]
    for name, mime in (
        ("01_request_milestone-follow-up.docx", DOCX),
        ("03_email_cs_unloading-update.eml", "message/rfc822"),
    ):
        blob = types.Blob(data=(src / name).read_bytes(), mime_type=mime, display_name=name)
        parts.append(types.Part(inline_data=blob))
    for name in ("02_meeting-notes_2026-11-10_product-sync.md", "04_ops-chat-export_ct-followup.txt"):
        text = (src / name).read_text(encoding="utf-8")
        parts.append(types.Part.from_text(text=f"Isi file {name}:\n\n{text}"))
    return parts


async def _say(runner, session, parts) -> str:
    msg = types.Content(role="user", parts=parts)
    out = []
    async for ev in runner.run_async(user_id="u", session_id=session.id, new_message=msg):
        if ev.content and ev.content.parts:
            out += [p.text for p in ev.content.parts if p.text and not getattr(p, "thought", False)]
    return "\n".join(out)


async def test_uc3_agent_live():
    configure_vertex_env()
    from logisly_agents.uc3.agent import build_uc3_agent

    settings = get_settings()
    runner = InMemoryRunner(agent=build_uc3_agent(settings), app_name=APP)
    session = await runner.session_service.create_session(app_name=APP, user_id="u")
    turn1 = await _say(runner, session, _parts())
    await _say(runner, session, [types.Part.from_text(text="Setuju, terapkan semua perubahannya.")])
    got = await runner.session_service.get_session(app_name=APP, user_id="u", session_id=session.id)
    state = got.state

    docs = state.get("uc3:docs") or {}
    assert len(docs) == 1, list(docs)
    assert "Pertanyaan terbuka" in next(iter(docs.values()))

    uc3 = settings.data_dir / "uc3"
    ctx = read_context(uc3)
    jira = MockJira(state, uc3 / "jira" / "issues_snapshot.json", ctx["conventions"], ctx["calendar"])
    assert jira.get("LOG-3417")["status"] == "Won't Do"
    new = [i for i in state["uc3:jira"]["issues"] if i["key"] > "LOG-3420" and i["type"] == "Story"]
    assert len(new) >= 8, [i["summary"] for i in new]

    assert "18 Desember" in turn1, turn1[:3000]
    assert "LOG-3412" in turn1, turn1[:3000]
