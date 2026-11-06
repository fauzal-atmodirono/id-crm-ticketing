import email
import email.policy
from datetime import date

import pytest
from google.adk.runners import InMemoryRunner
from google.genai import types

from logisly_agents.config import configure_vertex_env, get_settings
from logisly_agents.uc2 import store
from logisly_agents.uc2.evaluate import compare
from logisly_agents.uc2.pricing import PricingRepo
from logisly_agents.uc2.rules import load_rules

pytestmark = pytest.mark.live

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FILES = [
    "TND-NAL-2026-11_Undangan_Tender.pdf",
    "Penawaran_PT_Trans_Cahaya_Logistik_Q-TCL-1.pdf",
    "Penawaran_CV_Mitra_Armada_Jaya_Q-MAJ-1.xlsx",
    "Penawaran_PT_Lintas_Jawa_Ekspres_Q-LJE-1.pdf",
    "Penawaran_PT_Garuda_Angkut_Sejahtera_Q-GAS-1.pdf",
    "Penawaran_PT_Prima_Fleet_Nusantara_Q-PFN-1.xlsx",
    "Penawaran_PT_Trans_Cahaya_Logistik_Q-TCL-2.pdf",
]


def _parts() -> list[types.Part]:
    inbox = get_settings().data_dir / "uc2" / "inbox"
    parts = [
        types.Part.from_text(
            text="Ini dokumen tender dan penawaran untuk RFQ-LGS-2611-007. Tolong catat dan beri rekomendasi."
        )
    ]
    for name in FILES:
        mime = XLSX if name.endswith(".xlsx") else "application/pdf"
        blob = types.Blob(data=(inbox / "attachments" / name).read_bytes(), mime_type=mime, display_name=name)
        parts.append(types.Part(inline_data=blob))
    msg = email.message_from_bytes((inbox / "04_quote_sti.eml").read_bytes(), policy=email.policy.default)
    body = msg.get_body(preferencelist=("plain",)).get_content()
    parts.append(types.Part.from_text(text=f"Penawaran dari email (isi email, tanpa lampiran):\n\n{body}"))
    return parts


async def _say(runner, session, parts) -> None:
    msg = types.Content(role="user", parts=parts)
    async for _ in runner.run_async(user_id="u", session_id=session.id, new_message=msg):
        pass


async def _state(runner, session):
    got = await runner.session_service.get_session(app_name="uc2_live", user_id="u", session_id=session.id)
    return got.state


async def test_uc2_agent_live():
    configure_vertex_env()
    from logisly_agents.uc2.agent import build_uc2_agent

    settings = get_settings()
    runner = InMemoryRunner(agent=build_uc2_agent(settings), app_name="uc2_live")
    session = await runner.session_service.create_session(app_name="uc2_live", user_id="u")
    await _say(runner, session, _parts())
    state = await _state(runner, session)
    if not (state.get("uc2:tender") and len(state.get("uc2:quotations", [])) >= 7):
        await _say(runner, session, [types.Part.from_text(text="Ya, lanjutkan.")])
        state = await _state(runner, session)

    tender = store.get_tender(state)
    assert tender is not None and len(tender.lanes) == 4
    quotes = store.list_quotations(state)
    assert len(quotes) == 7, [(q["quote_id"], q["transporter"], q["document_name"]) for q in quotes]

    repo = PricingRepo(settings.data_dir / "uc2" / "pricing" / "pricing.db")
    rules = load_rules(settings.data_dir / "uc2" / "business_rules.yaml")
    res = compare(tender, quotes, repo, rules, as_of=date(2026, 11, 9))
    rec = {k: v["recommended_transporter"] for k, v in res["lanes"].items()}
    assert all("Trans Cahaya" in rec[k] for k in ("T1", "T2", "T3")), rec
    assert "Prima Fleet" in rec["T4"], rec
