"""Replay a scripted conversation against the real AI prompt, offline.

Why this exists
---------------
Until now the only way to see what Gemini actually says to a nasabah was to
pick up a handset and WhatsApp the Twilio number. Every test in `agent/tests`
stubs the model (`tests/conftest.py` injects fake clients), so the suite pins
*plumbing* — that the profile reaches the prompt — and says nothing about the
thing the demo is judged on, which is whether the conversation is any good.

That made prompt work close to untestable: one persona at a time, six turns
per attempt, a live 24-hour WhatsApp window burned per iteration, and no way
at all to ask "does this wording hold up across all 25 nasabah".

This harness closes that loop. It builds the **real** system prompt by
importing `_build_system_prompt` and `format_customer_context` from the agent
service, and calls the **real** `gemini.decide`. Nothing about the AI path is
re-implemented here, which is the point: a prompt that reads well under this
script is the prompt production will use, because it is literally the same
string. What is faked is only the transport — no Chatwoot, no Twilio, no
webhook, no database.

Faithfulness, and its two deliberate limits
-------------------------------------------
The message dicts fed to `_build_context` mirror Chatwoot's shape
(`message_type` 0 incoming / 1 outgoing, `private`, `sender`), so the context
string is byte-identical to production's for the same history.

**The replay stops when the model hands off or escalates.** That is not a
shortcut, it is the production rule: `orchestrator._is_eligible` only acts on
conversations whose status is `pending`, and both of those actions move the
conversation to a human. A replay that kept talking after a handoff would be
simulating a bot that does not exist.

**`AGENT_MODE` is reported, not simulated.** In `suggest` mode a `send_reply`
becomes a private note for a human rather than a message to the customer; the
*text Gemini produced* is identical either way, and that text is what this
tool exists to inspect.

Usage
-----
Run it with the agent service's interpreter — it needs `google-genai` and the
agent package, both of which live in that venv:

    agent/.venv/bin/python deploy/scripts/bahana_replay.py --slug moderat

    # the transcript that prompted this tool, across every built-in persona
    agent/.venv/bin/python deploy/scripts/bahana_replay.py --all

    # against real warehouse rows instead of the built-in fixtures
    agent/.venv/bin/python deploy/scripts/bahana_replay.py --source bq \\
        --project lv-playground-genai --dataset bahana_demo \\
        --location asia-southeast2 --phone +6281112117038

    # your own turns, and see exactly what the model was told
    agent/.venv/bin/python deploy/scripts/bahana_replay.py --slug agresif \\
        --turn "halo" --turn "ada produk apa buat saya?" --show-prompt

    # the full hello-to-escalation scripts behind the v3 demo guide
    agent/.venv/bin/python deploy/scripts/bahana_replay.py --slug moderat \\
        --script deploy/scripts/demo-scripts/bahana-moderat.txt

Gemini credentials come from the ambient environment exactly as they do in
production (Vertex ADC by default, or `GEMINI_API_KEY` with
`GOOGLE_GENAI_USE_VERTEXAI=false`). Everything else `Settings` demands —
Chatwoot URLs, tokens, the database — is stubbed below, because this process
never opens a socket to any of them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import subprocess
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent

# `Settings` fails fast on missing env vars (app/config.py), by design — a
# half-configured agent container should not boot. This process talks to
# Gemini and nothing else, so the fields describing transports it never uses
# get obviously-fake placeholders. setdefault, never assignment: a real value
# already in the environment must win, so that `--source bq` against a live
# tenant's env file behaves the same as production.
_STUB_ENV = {
    "CHATWOOT_URL": "http://replay.invalid",
    "CHATWOOT_API_TOKEN": "replay",
    "CHATWOOT_PLATFORM_TOKEN": "replay",
    "CHATWOOT_ACCOUNT_ID": "1",
    "CHATWOOT_WEBHOOK_SECRET": "replay",
    "CHATWOOT_BOT_SECRET": "replay",
    "CHATWOOT_BOT_TOKEN": "replay",
    "AGENT_DATABASE_URL": "sqlite+aiosqlite:///:memory:",
}
for _key, _value in _STUB_ENV.items():
    os.environ.setdefault(_key, _value)

sys.path.insert(0, str(_ROOT / "agent"))
sys.path.insert(0, str(_HERE / "seed_demo_data"))

from app.ai import gemini  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.services import demo_persona  # noqa: E402
from app.services.customer_context import format_customer_context  # noqa: E402
from app.services.orchestrator import _build_context, _build_system_prompt  # noqa: E402

# The conversation that prompted this tool: a greeting, a profile question, a
# soft complaint about the portfolio, and then the pushback that killed it.
# Kept verbatim (typos included) because the value of a regression script is
# that it is the real thing, not a tidied paraphrase of it.
DEFAULT_SCRIPT = [
    "haloo admin",
    "bagaimana profile saya?",
    "hmmm portfolio saya gitu gitu aja yaa",
    "tapi saya ingin fokusnya ke saham ajaa, gimana yaa?",
]

_CUSTOMER_EMAIL = "nasabah@example.invalid"


def _message(content: str, *, incoming: bool, sender_name: str) -> dict:
    """One Chatwoot-shaped message dict.

    Only the keys `_build_context` actually reads are populated. Adding more
    would invite the harness and production to drift apart on fields nobody
    checks; leaving them out keeps it obvious that this is the whole contract.
    """
    return {
        "message_type": 0 if incoming else 1,
        "content": content,
        "private": False,
        "sender": {
            "name": sender_name,
            "email": _CUSTOMER_EMAIL if incoming else None,
        },
    }


def _load_persona(path: str | None) -> dict | None:
    """An operator-authored assistant persona from a JSON file, or None.

    The persona is edited per-inbox in the CRM's Knowledge settings and is a
    live part of the prompt (`_persona_prompt`), so being able to try a wording
    change here — instead of saving it on a tenant and messaging a handset — is
    most of the point of this tool.
    """
    if not path:
        return None
    with open(path, encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        sys.exit(f"--persona-json must contain a JSON object, got {type(loaded).__name__}")
    return loaded


def _profiles_from_slugs(slugs: list[str]) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for slug in slugs:
        attributes = demo_persona.attributes_for(slug)
        name = demo_persona.display_name_for(slug)
        if attributes is None or name is None:
            sys.exit(
                f"unknown persona slug {slug!r}; "
                f"known slugs: {', '.join(sorted(demo_persona.PROFILES))}"
            )
        out.append((name, attributes))
    return out


def _profiles_from_bq(
    project: str, dataset: str, location: str, phone: str | None
) -> list[tuple[str, dict]]:
    """Warehouse rows, as (display name, attributes) pairs.

    Reuses the sync script's reader rather than re-issuing the query, so the
    harness can never disagree with the job that actually writes these
    attributes onto contacts about what `v_nasabah_profile` contains.
    """
    from bahana_bq_to_crm_sync import PROFILE_KEYS, fetch_warehouse  # noqa: PLC0415

    rows = fetch_warehouse(project, dataset, location)
    if phone:
        row = rows.get(phone)
        if row is None:
            sys.exit(f"no warehouse row for phone {phone!r} in {project}.{dataset}")
        rows = {phone: row}
    out: list[tuple[str, dict]] = []
    for row in rows.values():
        attributes = {k: row[k] for k in PROFILE_KEYS if row.get(k) is not None}
        out.append((row.get("name") or "Nasabah", attributes))
    return out


def _profiles_from_local(count: int) -> list[tuple[str, dict]]:
    """Freshly generated nasabah, straight from the seeder's generator.

    Lets the harness run against the full synthetic population with no BigQuery
    access and no network at all — useful for checking that a prompt change
    holds up across risk profiles rather than on the one persona in front of
    you.
    """
    from client import build_nasabah_custom_attributes  # noqa: PLC0415
    from nasabah import generate_nasabah  # noqa: PLC0415

    people = generate_nasabah(count, batch_id="replay")
    return [(p.name, build_nasabah_custom_attributes(p, "replay")) for p in people]


async def _replay_one(
    display_name: str,
    attributes: dict,
    turns: list[str],
    persona: dict | None,
    show_prompt: bool,
    handoff_message: str = "",
) -> dict:
    """Drive one persona through the script, returning a structured result."""
    customer_context = format_customer_context(attributes)
    system_prompt = _build_system_prompt(persona, customer_context)

    print(f"\n{'=' * 72}\n{display_name}  ({attributes.get('risk_profile', '?')})\n{'=' * 72}")
    if show_prompt:
        print(f"\n--- system prompt ---\n{system_prompt}\n--- end ---\n")

    message_list: list[dict] = []
    exchanges: list[dict] = []

    for turn in turns:
        message_list.append(_message(turn, incoming=True, sender_name=display_name))
        print(f"\n  \033[1mnasabah\033[0m  {turn}")

        decision = await gemini.decide(system_prompt, _build_context(message_list))
        record = {"customer": turn, "action": decision.action, "args": decision.args}
        exchanges.append(record)

        if decision.action == "send_reply":
            reply = str(decision.args.get("text") or "").strip()
            print(f"  \033[36mbot\033[0m      {reply}")
            message_list.append(_message(reply, incoming=False, sender_name="AI"))
            continue

        # handoff_to_human / escalate_to_ticket both take the conversation out
        # of `pending`, which is the only status the orchestrator acts on. The
        # bot is off the air from here, so the replay ends rather than
        # pretending otherwise.
        #
        # Production posts one more customer-visible message before it goes
        # quiet: `_handoff_to_human_via_chatwoot` sends the assistant persona's
        # `handoff` text (falling back to HANDOFF_DEFAULT_MESSAGE, and posting
        # nothing when both are empty). That message is fetched from the
        # backend, which this process deliberately does not talk to -- so pass
        # it in with --handoff-message when you want the transcript to match
        # what the customer actually sees. Without it the replay would end one
        # message early and quietly misrepresent the handover.
        reason = decision.args.get("reason") or decision.args.get("summary") or ""
        if handoff_message:
            print(f"  \033[36mbot\033[0m      {handoff_message}")
            record["handoff_message"] = handoff_message
        print(f"  \033[33m{decision.action}\033[0m  {reason}")
        print("  (conversation goes to `open` and is routed to a human — the AI stops here)")
        break

    return {
        "name": display_name,
        "risk_profile": attributes.get("risk_profile"),
        "next_best_offer": attributes.get("next_best_offer"),
        "exchanges": exchanges,
    }


async def _main_async(args: argparse.Namespace) -> int:
    if args.source == "bq":
        if not (args.project and args.dataset):
            sys.exit("--source bq requires --project and --dataset")
        profiles = _profiles_from_bq(args.project, args.dataset, args.location, args.phone)
    elif args.source == "local":
        profiles = _profiles_from_local(args.count)
    else:
        slugs = list(demo_persona.PROFILES) if args.all else [args.slug]
        profiles = _profiles_from_slugs(slugs)

    if args.limit:
        profiles = profiles[: args.limit]

    turns = args.turn or (_read_script(args.script) if args.script else DEFAULT_SCRIPT)
    persona = _load_persona(args.persona_json)

    settings = get_settings()
    print(
        f"model={settings.gemini_model}  vertex={settings.google_genai_use_vertexai}  "
        f"agent_mode={settings.agent_mode}  personas={len(profiles)}  turns={len(turns)}"
    )
    if settings.agent_mode == "suggest":
        print("note: AGENT_MODE=suggest — in production each reply below is posted")
        print("      as a private note for a human, not sent to the customer.")

    results = []
    for display_name, attributes in profiles:
        results.append(
            await _replay_one(
                display_name, attributes, turns, persona, args.show_prompt,
                args.handoff_message,
            )
        )

    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nwrote {args.json}")
    return 0


def _read_script(path: str) -> list[str]:
    """One customer turn per line; blank lines and `#` comments ignored."""
    lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    turns = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    if not turns:
        sys.exit(f"--script {path} contained no turns")
    return turns


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay a scripted conversation against the real AI prompt, offline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source",
        choices=("slugs", "bq", "local"),
        default="slugs",
        help="where profiles come from (default: the built-in demo personas)",
    )
    parser.add_argument("--slug", default="moderat", help="persona slug for --source slugs")
    parser.add_argument("--all", action="store_true", help="every built-in persona")
    parser.add_argument("--project", help="BigQuery project, for --source bq")
    parser.add_argument("--dataset", help="BigQuery dataset, for --source bq")
    parser.add_argument("--location", default="asia-southeast2", help="BigQuery location")
    parser.add_argument("--phone", help="replay one warehouse row, by phone")
    parser.add_argument("--count", type=int, default=5, help="how many to generate for --source local")
    parser.add_argument("--limit", type=int, help="cap how many personas are replayed")
    parser.add_argument("--turn", action="append", help="a customer turn (repeatable)")
    parser.add_argument("--script", help="file of customer turns, one per line")
    parser.add_argument("--persona-json", help="operator assistant persona JSON to apply")
    parser.add_argument("--show-prompt", action="store_true", help="dump the system prompt")
    parser.add_argument(
        "--handoff-message",
        default="",
        help=(
            "the assistant persona's handoff text, printed when the model hands "
            "off. Production posts this to the customer before going quiet; this "
            "process never reaches the backend, so supply it here to make the "
            "transcript match what the handset receives."
        ),
    )
    parser.add_argument("--json", help="also write structured results to this path")
    args = parser.parse_args()

    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
