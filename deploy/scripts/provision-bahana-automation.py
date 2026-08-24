#!/usr/bin/env python3
"""Provision the Bahana personalization automation rules in Chatwoot.

Design and rationale: `docs/bahana-automation-personalization.md`. Read §5 of
that document before changing the rule set here -- which rules ship active and
which ship inactive is a deliberate safety decision, not a default.

The short version of why this exists: Chatwoot's rule engine and our AI
orchestrator already share one data surface (contact custom attributes in,
labels out), so segmentation, consent gating and offer staging need no code --
only rules. This script writes those rules, plus the labels, team and custom
attribute definition they depend on.

Safety properties, in the order they matter:

* **`--dry-run` is the default.** Nothing is written unless `--apply` is
  passed. The dry run still performs every read, so its report is real.
* **Rules that can silence the AI ship INACTIVE.** The orchestrator only acts
  on `pending` conversations, so any rule that assigns a conversation to a
  human takes the bot off the air. On a tenant whose whole purpose is
  demonstrating the bot, that must be opt-in -- see RULES below.
* **Idempotent.** Everything is matched by name/title/key first; an object that
  already exists is reported `unchanged` and never duplicated. Safe to re-run.
* **`--remove` is a clean undo** for the rules. Labels, team and the custom
  attribute definition are deliberately NOT removed: an operator may have
  applied them by hand, and deleting a label detaches it from every
  conversation that carries it.

Ordering is not incidental. Chatwoot's `condition_validation_service.rb`
rejects a rule whose `attribute_key` is not already a row in
`custom_attribute_definitions` for the matching `attribute_model`, so the
attribute definition and the team must exist before any rule references them.

Usage:

    export CHATWOOT_URL=https://bahana.crm.34-50-103-151.nip.io
    export CHATWOOT_ACCOUNT_ID=1
    export CHATWOOT_API_TOKEN=...        # an admin access token; never echo it

    python3 deploy/scripts/provision-bahana-automation.py            # dry run
    python3 deploy/scripts/provision-bahana-automation.py --apply
    python3 deploy/scripts/provision-bahana-automation.py --remove --apply

Run it against a scratch tenant before a real one. It talks to whatever
CHATWOOT_URL points at and has no idea which tenant that is.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

# ---------------------------------------------------------------------------
# Vocabulary. These values are a contract with the seeder --
# `deploy/scripts/seed_demo_data/nasabah.py` (_RISK_WEIGHTS, _AUM_BANDS) and
# `client.py::build_nasabah_custom_attributes`. A condition that does not match
# a value the seeder actually writes produces a rule that never fires, and a
# rule that never fires looks identical to a rule that is broken.
# ---------------------------------------------------------------------------

RISK_PROFILES = ("Konservatif", "Moderat", "Agresif")

# The two top bands of the seeder's five. "Priority" is a business threshold,
# so it lives here rather than being derived -- the RM lead is expected to
# retune it in the CRM UI, and this script must not fight that.
PRIORITY_AUM_BANDS = ("Rp 500 juta - 1 miliar", "> Rp 1 miliar")
TOP_AUM_BAND = "> Rp 1 miliar"

# Contact attributes the rules condition on. All of these are written by the
# seeder; the script verifies each is DEFINED before writing rules, because a
# missing definition is rejected at rule-create time with an opaque 422.
REQUIRED_CONTACT_ATTRIBUTES = ("risk_profile", "aum_band", "next_best_offer")

# Consent is the one attribute the seeder does not write. It is created here
# because Pattern B (spec §7.3) is theoretical without it: a consent gate you
# cannot express as a condition is a paragraph, not a control.
CONSENT_ATTRIBUTE = {
    "attribute_display_name": "Consent Marketing",
    "attribute_key": "consent_marketing",
    "attribute_display_type": "text",
    "attribute_description": (
        "true/false. Marketing consent under UU PDP 27/2022. When 'false', "
        "the consent automation rule routes the conversation to a human and "
        "the AI never speaks -- see docs/bahana-automation-personalization.md."
    ),
    "attribute_model": "contact_attribute",
}

TEAM_NAME = "RM Prioritas"
TEAM_DESCRIPTION = (
    "Relationship managers who take over conversations the AI must not handle "
    "(opt-out, consent withdrawn, top-band nasabah)."
)

LABELS = (
    ("segmen-konservatif", "Nasabah dengan profil risiko Konservatif", "#3b82f6"),
    ("segmen-moderat", "Nasabah dengan profil risiko Moderat", "#8b5cf6"),
    ("segmen-agresif", "Nasabah dengan profil risiko Agresif", "#f97316"),
    ("nasabah-prioritas", "AUM pada dua pita teratas", "#eab308"),
    ("offer-staged", "Punya next-best-offer yang siap ditawarkan", "#10b981"),
    ("opt-out", "Nasabah meminta berhenti menerima penawaran", "#ef4444"),
)


def _contact_condition(
    key: str, operator: str, values: list[str], query_operator: str | None
) -> dict:
    """One condition against a CONTACT custom attribute.

    `custom_attribute_type` is not optional decoration: Chatwoot defaults the
    lookup to `conversation_attribute`, so omitting it sends the validator
    looking for these keys on the wrong model and the rule is rejected.
    """
    return {
        "attribute_key": key,
        "filter_operator": operator,
        "values": values,
        "query_operator": query_operator,
        "custom_attribute_type": "contact_attribute",
    }


def _rules(team_id: int | None) -> list[dict]:
    """The eight rules. `team_id` may be None only on a dry run.

    Rules whose `active` is False can silence the AI (they assign a
    conversation, which drops it out of `pending`, which is the only status the
    orchestrator acts on). They are provisioned so they can be shown and
    toggled deliberately -- never so they can fire unnoticed.
    """
    rules: list[dict] = []

    for profile in RISK_PROFILES:
        rules.append(
            {
                "name": f"Segmen — {profile}",
                "description": (
                    f"Label percakapan dari nasabah berprofil {profile}. "
                    "Additive only: tidak pernah membungkam AI."
                ),
                "event_name": "conversation_created",
                "active": True,
                "conditions": [
                    _contact_condition("risk_profile", "equal_to", [profile], None)
                ],
                "actions": [
                    {
                        "action_name": "add_label",
                        "action_params": [f"segmen-{profile.lower()}"],
                    }
                ],
            }
        )

    rules.append(
        {
            "name": "Nasabah prioritas",
            "description": (
                "Dua pita AUM teratas. Label saja -- routing ke manusia ada di "
                "rule terpisah yang sengaja nonaktif."
            ),
            "event_name": "conversation_created",
            "active": True,
            "conditions": [
                _contact_condition(
                    "aum_band", "equal_to", list(PRIORITY_AUM_BANDS), None
                )
            ],
            "actions": [
                {"action_name": "add_label", "action_params": ["nasabah-prioritas"]}
            ],
        }
    )

    rules.append(
        {
            "name": "Penawaran tersedia",
            "description": (
                "Kontak membawa next_best_offer. Label ini adalah message bus "
                "Pattern C: penulisan label memicu conversation_updated ke "
                "agent service -- lihat docs §6.1."
            ),
            "event_name": "conversation_created",
            "active": True,
            "conditions": [
                _contact_condition("next_best_offer", "is_present", [], None)
            ],
            "actions": [
                {"action_name": "add_label", "action_params": ["offer-staged"]}
            ],
        }
    )

    optout_actions: list[dict] = [
        {"action_name": "add_label", "action_params": ["opt-out"]}
    ]
    if team_id is not None:
        optout_actions.append(
            {"action_name": "assign_team", "action_params": [team_id]}
        )
    rules.append(
        {
            "name": "Opt-out — BERHENTI/STOP",
            "description": (
                "Permintaan berhenti ditangani manusia, bukan bot. Satu-satunya "
                "rule aktif yang memindahkan percakapan ke manusia, dan itu "
                "memang perilaku yang benar (spec §7.3)."
            ),
            "event_name": "message_created",
            "active": True,
            "conditions": [
                {
                    "attribute_key": "content",
                    "filter_operator": "contains",
                    "values": ["BERHENTI"],
                    "query_operator": "OR",
                },
                {
                    "attribute_key": "content",
                    "filter_operator": "contains",
                    "values": ["STOP"],
                    "query_operator": None,
                },
            ],
            "actions": optout_actions,
        }
    )

    # --- inactive by design, from here down ---------------------------------

    if team_id is not None:
        rules.append(
            {
                "name": "Consent ditolak — serahkan ke manusia",
                "description": (
                    "NONAKTIF secara sengaja. Menyalakan ini membuat AI diam "
                    "untuk nasabah dengan consent_marketing=false. Kondisi "
                    "sengaja equal_to 'false', BUKAN is_not_present -- "
                    "is_not_present akan cocok dengan setiap kontak yang belum "
                    "punya atribut ini dan membungkam AI di seluruh tenant."
                ),
                "event_name": "conversation_created",
                "active": False,
                "conditions": [
                    _contact_condition(
                        "consent_marketing", "equal_to", ["false"], None
                    )
                ],
                "actions": [
                    {"action_name": "assign_team", "action_params": [team_id]}
                ],
            }
        )
        rules.append(
            {
                "name": "Nasabah prioritas — manusia lebih dulu",
                "description": (
                    "NONAKTIF secara sengaja. Menyalakan ini membuat nasabah "
                    "pita AUM teratas selalu ditangani RM lebih dulu; AI tetap "
                    "menyusun draft sebagai private note di AGENT_MODE=suggest."
                ),
                "event_name": "conversation_created",
                "active": False,
                "conditions": [
                    _contact_condition("aum_band", "equal_to", [TOP_AUM_BAND], None)
                ],
                "actions": [
                    {"action_name": "assign_team", "action_params": [team_id]}
                ],
            }
        )

    return rules


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _request(
    method: str, url: str, token: str, body: dict | None = None
) -> tuple[int, Any]:
    payload = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=payload, method=method)
    req.add_header("api_access_token", token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as res:  # noqa: S310
            raw = res.read()
            return res.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            detail = json.loads(raw) if raw else None
        except ValueError:
            detail = (raw or b"").decode(errors="replace")[:400]
        return exc.code, detail


def _listing(data: Any) -> list[dict]:
    """Chatwoot returns bare lists on some endpoints and {payload:[...]} on others."""
    if isinstance(data, dict):
        return list(data.get("payload") or [])
    return list(data or [])


class Api:
    def __init__(self, base: str, account: str, token: str, apply: bool) -> None:
        self.root = f"{base}/api/v1/accounts/{account}"
        self.token = token
        self.apply = apply
        self.failures: list[str] = []

    def get(self, path: str) -> list[dict]:
        status, data = _request("GET", f"{self.root}{path}", self.token)
        if status >= 400:
            raise RuntimeError(f"GET {path} -> HTTP {status}: {data}")
        return _listing(data)

    def create(self, path: str, body: dict, what: str) -> dict | None:
        if not self.apply:
            return None
        status, data = _request("POST", f"{self.root}{path}", self.token, body)
        if status >= 400:
            self.failures.append(f"{what}: HTTP {status} {json.dumps(data)[:300]}")
            return None
        return data if isinstance(data, dict) else None

    def delete(self, path: str, what: str) -> bool:
        if not self.apply:
            return False
        status, data = _request("DELETE", f"{self.root}{path}", self.token)
        if status >= 400:
            self.failures.append(f"{what}: HTTP {status} {json.dumps(data)[:300]}")
            return False
        return True


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def ensure_attribute(api: Api, report: list[str]) -> bool:
    """Verify the seeder's attributes are defined; create the consent one.

    Returns False when a required attribute is missing, because every rule that
    references it would 422 -- better to stop with one clear message than to
    emit six opaque failures.
    """
    defs = api.get("/custom_attribute_definitions?attribute_model=contact_attribute")
    keys = {d.get("attribute_key") for d in defs}

    missing = [k for k in REQUIRED_CONTACT_ATTRIBUTES if k not in keys]
    if missing:
        report.append(
            f"  BLOCKED  contact attributes not defined: {', '.join(missing)}\n"
            "           Define them in Settings -> Custom Attributes (or re-run "
            "the seeder) before provisioning rules."
        )
        return False
    report.append(
        f"  ok       {len(REQUIRED_CONTACT_ATTRIBUTES)} required contact attributes defined"
    )

    key = CONSENT_ATTRIBUTE["attribute_key"]
    if key in keys:
        report.append(f"  unchanged attribute {key}")
    else:
        api.create("/custom_attribute_definitions", CONSENT_ATTRIBUTE, f"attribute {key}")
        report.append(f"  CREATE    attribute {key}")
    return True


def ensure_labels(api: Api, report: list[str]) -> None:
    existing = {lbl.get("title") for lbl in api.get("/labels")}
    for title, description, color in LABELS:
        if title in existing:
            report.append(f"  unchanged label {title}")
            continue
        api.create(
            "/labels",
            {
                "title": title,
                "description": description,
                "color": color,
                "show_on_sidebar": True,
            },
            f"label {title}",
        )
        report.append(f"  CREATE    label {title}")


def ensure_team(api: Api, report: list[str]) -> int | None:
    for team in api.get("/teams"):
        if team.get("name") == TEAM_NAME:
            report.append(f"  unchanged team {TEAM_NAME} (id {team.get('id')})")
            return int(team["id"])
    created = api.create(
        "/teams",
        {
            "name": TEAM_NAME,
            "description": TEAM_DESCRIPTION,
            "allow_auto_assign": False,
        },
        f"team {TEAM_NAME}",
    )
    if created and created.get("id"):
        report.append(f"  CREATE    team {TEAM_NAME} (id {created['id']})")
        return int(created["id"])
    report.append(
        f"  CREATE    team {TEAM_NAME}"
        + ("" if api.apply else " (dry run: id unknown, team-assigning rules deferred)")
    )
    return None


def ensure_rules(api: Api, team_id: int | None, report: list[str]) -> None:
    existing = {r.get("name") for r in api.get("/automation_rules")}
    for rule in _rules(team_id):
        state = "active" if rule["active"] else "INACTIVE"
        if rule["name"] in existing:
            report.append(f"  unchanged rule [{state}] {rule['name']}")
            continue
        api.create("/automation_rules", rule, f"rule {rule['name']}")
        report.append(f"  CREATE    rule [{state}] {rule['name']}")


def remove_rules(api: Api, report: list[str]) -> None:
    wanted = {r["name"] for r in _rules(0)}
    for rule in api.get("/automation_rules"):
        if rule.get("name") in wanted:
            api.delete(f"/automation_rules/{rule['id']}", f"rule {rule['name']}")
            report.append(f"  DELETE    rule {rule['name']}")
    report.append(
        "  note      labels, team and the consent attribute are left in place "
        "on purpose (see module docstring)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="actually write (default: dry run)"
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="delete the automation rules this script creates",
    )
    args = parser.parse_args()

    base = (os.environ.get("CHATWOOT_URL") or "").rstrip("/")
    account = os.environ.get("CHATWOOT_ACCOUNT_ID") or ""
    token = os.environ.get("CHATWOOT_API_TOKEN") or ""
    if not (base and account and token):
        print("CHATWOOT_URL, CHATWOOT_ACCOUNT_ID and CHATWOOT_API_TOKEN must be set")
        return 2

    api = Api(base, account, token, apply=args.apply)
    report: list[str] = []
    mode = "APPLY" if args.apply else "DRY RUN (nothing is written)"
    print(f"{base} account {account} -- {mode}\n")

    try:
        if args.remove:
            remove_rules(api, report)
        else:
            if not ensure_attribute(api, report):
                print("\n".join(report))
                return 1
            ensure_labels(api, report)
            team_id = ensure_team(api, report)
            ensure_rules(api, team_id, report)
            if team_id is None and not args.apply:
                report.append(
                    "  note      the two team-assigning rules are not listed above; "
                    "the team does not exist yet, so its id is unknown until --apply"
                )
    except (RuntimeError, urllib.error.URLError) as exc:
        print(f"aborted: {exc}")
        return 1

    print("\n".join(report))

    if api.failures:
        print("\nFAILURES")
        for line in api.failures:
            print(f"  {line}")
        return 1

    if not args.apply:
        print("\nRe-run with --apply to write. Nothing was changed.")
    elif not args.remove:
        print(
            "\nVerify: Settings -> Automation (8 rules, 2 inactive), "
            "Settings -> Labels (6 labels), then send a message from the demo "
            "handset and confirm the AI still replies.\n"
            "See docs/bahana-automation-personalization.md §7."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
