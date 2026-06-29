"""Seed realistic DUMMY demo data into the bot-metrics BigQuery tables so every
Looker tile is populated, without manual per-channel input. Real channel data
still ingests normally (Zendesk sync for `conversations`; streaming for
`turn_events`; POST /qa/label for `qa_labels`).

Demo rows are clearly marked and removable:
  - conversations / qa_labels: conversation_id like 'demo-%'
  - turn_events: session_id like '%-demo-%'
  - qa_labels: reviewer = 'demo-qa'

The script is idempotent: it deletes prior demo rows, then re-appends. It also
provisions every table + view (incl. P3 `v_nps` and P4 `qa_labels`/`v_quality`)
and adds the `conversations.nps_score` column if missing (ALLOW_FIELD_ADDITION),
preserving any real rows already there.

NOTE: a real `sync_zendesk_metrics.py` run WRITE_TRUNCATEs `conversations` and
wipes the demo rows there (turn_events/qa_labels persist) — re-run this script
afterwards to top the demo data back up.

Usage:  .venv/bin/python scripts/seed_demo_metrics.py
"""

# ruff: noqa: S311  # demo-data generation uses `random` for variety, not security

from __future__ import annotations

import random
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from google.cloud import bigquery

from chatbot.features.metrics.bigquery_schema import CONVERSATIONS_SCHEMA, view_ddls
from chatbot.features.metrics.qa_schema import QA_LABELS_SCHEMA, qa_view_ddls
from chatbot.features.metrics.turn_schema import TURN_EVENTS_SCHEMA, turn_view_ddls
from chatbot.platform.config import get_settings

random.seed(42)

CHANNELS = ["WhatsApp", "Email", "Phone", "Web"]
# session_id prefix per channel so channel_from_external_id maps turn_events correctly
PREFIX = {"WhatsApp": "whatsapp", "Email": "email", "Phone": "phone", "Web": "sim"}
LATENCY = {
    "WhatsApp": (800, 3500),
    "Email": (1500, 6000),
    "Phone": (1000, 9000),
    "Web": (300, 1800),
}
NOTES = [
    "accurate, on-policy",
    "minor tone issue",
    "missed one spec detail",
    "excellent resolution",
    "slightly verbose but correct",
    "good empathy, correct answer",
]
_BASE = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)

# Number of demo conversations (override with: seed_demo_metrics.py <N>).
# Each conversation also yields ~3 turn_events and ~0.55 qa_labels.
N_CONVERSATIONS = 600

# Demo-tuning probabilities (share of conversations / per-turn rates).
P_BOT = 0.66  # bot-resolved share
P_FALLBACK = 0.06  # per-turn fallback rate
P_HANDOFF = 0.08  # last-turn handoff rate
P_QA = 0.55  # share of conversations that get a QA label


def _ts(days_ago: float) -> str:
    return (_BASE - timedelta(days=days_ago)).isoformat()


def generate(n: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    conv: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []
    qa: list[dict[str, Any]] = []
    now = datetime.now(UTC).isoformat()
    for i in range(n):
        ch = random.choices(CHANNELS, weights=[34, 26, 16, 24])[0]
        pfx = PREFIX[ch]
        cid = f"demo-{pfx}-{i}"
        sid = f"{pfx}-demo-{i}"
        days = random.uniform(0, 175)  # ~6 months of history for the volume trend
        bot = random.random() < P_BOT
        status = (
            random.choice(["solved", "closed"])
            if bot
            else random.choice(["open", "pending", "hold"])
        )
        csat = random.choices([None, 5, 4, 3, 2, 1], weights=[30, 30, 20, 10, 6, 4])[0]
        nps = random.choices(
            [None, 10, 9, 8, 7, 6, 5, 3, 0], weights=[45, 14, 13, 8, 6, 5, 4, 3, 2]
        )[0]
        conv.append(
            {
                "conversation_id": cid,
                "channel": ch,
                "created_at": _ts(days),
                "updated_at": _ts(max(0.0, days - random.uniform(0, 0.5))),
                "status": status,
                "resolved_by": "bot" if bot else "agent",
                "csat_score": csat,
                "nps_score": nps,
                "synced_at": now,
            }
        )
        n_turns = random.choices([1, 2, 3, 4, 5, 6], weights=[18, 22, 22, 16, 12, 10])[0]
        lo, hi = LATENCY[ch]
        for t in range(1, n_turns + 1):
            turns.append(
                {
                    "event_id": uuid.uuid4().hex,
                    "occurred_at": _ts(days),
                    "channel": ch,
                    "session_id": sid,
                    "latency_ms": random.randint(lo, hi),
                    "is_first_turn": t == 1,
                    "is_fallback": random.random() < P_FALLBACK,
                    "handed_off": (t == n_turns) and random.random() < P_HANDOFF,
                }
            )
        if random.random() < P_QA:
            qa.append(
                {
                    "conversation_id": cid,
                    "accuracy": random.randint(72, 99),
                    "quality": random.randint(70, 98),
                    "reviewer": "demo-qa",
                    "notes": random.choice(NOTES),
                    "labeled_at": _ts(random.uniform(0, 30)),
                }
            )
    return conv, turns, qa


def main() -> None:
    s = get_settings()
    proj, dataset = s.bigquery_project_id, s.bigquery_dataset
    ds = f"{proj}.{dataset}"
    client = bigquery.Client(project=proj)

    # Ensure all tables exist (conversations is created by the sync; create the rest).
    client.create_dataset(ds, exists_ok=True)
    client.create_table(
        bigquery.Table(f"{ds}.conversations", schema=CONVERSATIONS_SCHEMA), exists_ok=True
    )
    client.create_table(
        bigquery.Table(f"{ds}.turn_events", schema=TURN_EVENTS_SCHEMA), exists_ok=True
    )
    client.create_table(bigquery.Table(f"{ds}.qa_labels", schema=QA_LABELS_SCHEMA), exists_ok=True)

    # Idempotent: clear prior demo rows (best-effort — table may be brand-new).
    for sql in (
        f"DELETE FROM `{ds}.conversations` WHERE conversation_id LIKE 'demo-%'",  # noqa: S608
        f"DELETE FROM `{ds}.turn_events` WHERE session_id LIKE '%-demo-%'",  # noqa: S608
        f"DELETE FROM `{ds}.qa_labels` WHERE reviewer = 'demo-qa'",  # noqa: S608
    ):
        try:
            client.query(sql).result()
        except Exception as e:
            print("  (skip delete:", str(e)[:70], ")")

    count = int(sys.argv[1]) if len(sys.argv) > 1 else N_CONVERSATIONS
    conv, turns, qa = generate(count)

    # conversations: append + allow adding the nps_score column to a pre-P3 table.
    client.load_table_from_json(
        conv,
        f"{ds}.conversations",
        job_config=bigquery.LoadJobConfig(
            schema=CONVERSATIONS_SCHEMA,
            write_disposition="WRITE_APPEND",
            schema_update_options=["ALLOW_FIELD_ADDITION"],
        ),
    ).result()
    client.load_table_from_json(
        turns,
        f"{ds}.turn_events",
        job_config=bigquery.LoadJobConfig(
            schema=TURN_EVENTS_SCHEMA, write_disposition="WRITE_APPEND"
        ),
    ).result()
    client.load_table_from_json(
        qa,
        f"{ds}.qa_labels",
        job_config=bigquery.LoadJobConfig(
            schema=QA_LABELS_SCHEMA, write_disposition="WRITE_APPEND"
        ),
    ).result()
    print(f"seeded: {len(conv)} conversations, {len(turns)} turn_events, {len(qa)} qa_labels")

    # Ensure every view (P1 + P2 + P3 v_nps + P4 v_quality).
    ddls: dict[str, str] = {}
    ddls.update(view_ddls(proj, dataset))
    ddls.update(turn_view_ddls(proj, dataset))
    ddls.update(qa_view_ddls(proj, dataset))
    for ddl in ddls.values():
        client.query(ddl).result()
    print(f"ensured {len(ddls)} views:", ", ".join(sorted(ddls)))


if __name__ == "__main__":
    main()
