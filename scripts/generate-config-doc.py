#!/usr/bin/env python3
"""Generate the functional configuration document from the `Settings` classes.

P14 task 2. The configuration document is the highest-value handover artefact
for the client's operations team and the one most certain to rot, because this
platform now has **256 settings** across two services and this programme alone
added roughly forty of them. A hand-transcribed list of that size is wrong
within a sprint, and wrong in the worst way: it looks maintained.

So the document is generated, and the generator is the only thing that writes
it. Three consequences worth stating, because each is a decision:

1. **Nothing here reads the environment.** Defaults come from
   `Settings.model_fields[...].default`, which is class-level metadata, not from
   instantiating `Settings()`. That matters more than it looks: pydantic-settings
   reads `os.environ` even with `_env_file=None`, so a generator that
   instantiated the class would emit whatever the machine it ran on happened to
   have exported — and would emit a *different* document under the all-flags-on
   test gate. Six vacuous tests in this repo have already been found relying on
   that misunderstanding.

2. **Descriptions are the source comments, not a second prose corpus.** Neither
   `Settings` class uses `Field(description=...)`; every explanation in this
   codebase lives in a comment above its field. Re-typing those into a document
   would create a second copy to keep in step, so the generator parses them out
   of the source with `ast` plus the comment lines immediately above each
   annotated assignment. The document therefore cannot say something the code
   does not.

3. **Blast radius and "who may change" are DERIVED BY RULE, not assessed.**
   See `classify()`. They are a deterministic function of the field's name, type
   and default — useful as a first cut and honest about being one. They are not
   a human risk assessment of each of 256 settings, and the generated document
   says so in those words. Anyone who wants a real assessment of a particular
   setting should write it in the comment above the field, where it will be
   picked up here and cannot drift.

**There is no timestamp in the output, deliberately.** A generated document
whose bytes change on every run cannot be checked into git with a test that
asserts it is current — the test would fail on the second run and be deleted
within a week. Determinism is what makes `--check` possible.

Usage:
    python3 scripts/generate-config-doc.py            # write the document
    python3 scripts/generate-config-doc.py --check    # exit 1 if it is stale
    python3 scripts/generate-config-doc.py --stdout   # print, write nothing
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import re
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

OUTPUT_PATH = REPO_ROOT / "docs/client-materials/handover/configuration.md"
EXAMPLE_ENV = REPO_ROOT / "deploy/tenants/example.env"

# Compose is the third place a setting's value can come from, and leaving it out
# made the first draft of the drift table useless: 101 of 256 settings looked
# undocumented, when many are set by compose and an operator is not meant to
# touch them at all (`CHATWOOT_URL` is a required agent setting supplied as an
# internal docker hostname). Conflating "absent from example.env" with "set
# nowhere" buries the ~40 rows that are the actual finding under ~60 that are
# correct by design.
COMPOSE_FILES = [
    REPO_ROOT / "deploy/docker-compose.tenant.yml",
    REPO_ROOT / "deploy/docker-compose.infra.yml",
]

# The two Settings classes, in the order they appear in the document. Each is
# (heading, import name, source file, one-line scope note).
SOURCES: list[tuple[str, str, Path, str]] = [
    (
        "Backend service (`backend/`)",
        "chatbot.platform.config",
        REPO_ROOT / "backend/apps/backend/src/chatbot/platform/config.py",
        "The conversational AI backend: Gemini/Vertex, the knowledge base, "
        "metrics, RBAC, alerting and every admin surface.",
    ),
    (
        "Agent service (`agent/`)",
        "app.config",
        REPO_ROOT / "agent/app/config.py",
        "The Chatwoot webhook receiver and agent-bot orchestrator.",
    ),
]

# sys.path entries needed to import the two config modules. Both modules import
# nothing beyond the standard library and pydantic-settings, so importing them
# is cheap and has no side effects -- neither builds a client or reads a file.
IMPORT_PATHS = [
    REPO_ROOT / "backend/apps/backend/src",
    REPO_ROOT / "agent",
]

# Settings that this repository's own conventions say do not belong in
# `deploy/tenants/example.env`, so the drift report does not cry wolf about
# them. Keep this list short and justified -- it is an exemption from
# CLAUDE.md's "anything new must be added to both" rule, and every entry is a
# claim that the rule should not apply.
EXAMPLE_ENV_EXEMPT: dict[str, str] = {
    "host": "process-local bind address, set by the container, never per tenant",
    "port": "process-local listen port, set by the container, never per tenant",
    "debug": "process-local; the compose file sets it, not the tenant env",
}


# --------------------------------------------------------------------------
# Source parsing
# --------------------------------------------------------------------------

_SECTION_RE = re.compile(r"^#\s*-{2,}\s*(.*?)\s*-{2,}\s*$")


@dataclass
class FieldDoc:
    """One setting, as read out of the source."""

    name: str
    section: str
    description: str


def parse_source(path: Path, class_name: str = "Settings") -> list[FieldDoc]:
    """Read field order, section headings and comment descriptions from source.

    `ast` gives the fields and their line numbers; the comment block
    immediately above each field gives the description. A comment line shaped
    like `# --- Something ---` starts a new section instead, which is how the
    generated document gets its subheadings: the source is already organised,
    so the document inherits that organisation rather than inventing one.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    tree = ast.parse(text, filename=str(path))

    cls = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        ),
        None,
    )
    if cls is None:  # pragma: no cover - a missing class is a broken checkout
        raise SystemExit(f"{path}: no class {class_name}")

    fields: list[FieldDoc] = []
    section = ""
    for node in cls.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        name = node.target.id
        if name.startswith("_") or name == "model_config":
            continue

        # Walk upward from the field collecting its contiguous comment block.
        # A blank line or any code ends the block, so a comment attached to the
        # previous field cannot leak onto this one.
        block: list[str] = []
        index = node.lineno - 2  # 0-based, the line above the annotation
        while index >= 0:
            raw = lines[index].strip()
            if not raw.startswith("#"):
                break
            block.append(raw)
            index -= 1
        block.reverse()

        described: list[str] = []
        for raw in block:
            heading = _SECTION_RE.match(raw)
            if heading:
                # A section rule inside the block: everything above it belongs
                # to the section, not to this field.
                section = heading.group(1)
                described = []
                continue
            described.append(raw.lstrip("#").strip())

        fields.append(
            FieldDoc(
                name=name,
                section=section,
                description=" ".join(part for part in described if part),
            )
        )
    return fields


def load_settings_class(module_name: str) -> type:
    """Import a `Settings` class without instantiating it.

    Importing is enough: `model_fields` is populated at class-definition time,
    so no environment is read and no I/O happens.
    """
    for entry in IMPORT_PATHS:
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))
    module: types.ModuleType = importlib.import_module(module_name)
    return module.Settings  # type: ignore[no-any-return,attr-defined]


# --------------------------------------------------------------------------
# Classification (derived by rule -- see the module docstring)
# --------------------------------------------------------------------------

_SECRET_MARKERS = ("token", "secret", "password", "credential", "api_key")

OPERATOR = "Platform operator"
ADMIN = "Tenant administrator"


def is_secret(name: str) -> bool:
    """Whether a setting carries a credential, by name.

    Name-based and therefore fallible in one direction only: it can classify a
    harmless field as a secret (costing nothing but a redacted default in the
    document), and the marker list is broad for exactly that reason.
    """
    return any(marker in name for marker in _SECRET_MARKERS) or name.endswith("_key")


def classify(name: str, annotation: Any, default: Any) -> tuple[str, str]:
    """Return (blast radius, who may change it) for one setting.

    A deterministic function of the field's name and type. Ordered most
    specific first; the last branch is the fallback.
    """
    type_name = _annotation_name(annotation)

    if is_secret(name):
        return (
            "Authentication. A wrong value fails every call to that dependency; "
            "a leaked value is a security incident.",
            f"{OPERATOR} (secret store only)",
        )
    if "database_url" in name:
        return (
            "Data location. Pointing this at a different database splits the "
            "tenant's data silently rather than failing.",
            f"{OPERATOR} only",
        )
    if name.endswith("_url") or name.endswith("_host") or name in {"host", "port"}:
        return (
            "Deployment topology. A wrong value takes the dependency offline "
            "for the whole tenant.",
            OPERATOR,
        )
    if type_name == "bool" and name.endswith("_enabled"):
        return (
            "Feature on/off for this tenant. Default-off is the ship-dark "
            "guarantee: off reproduces pre-feature behaviour exactly.",
            f"{ADMIN}, after re-running the both-flag-states gate",
        )
    if type_name == "bool":
        return ("Behaviour switch for this tenant.", ADMIN)
    if type_name in {"int", "float"}:
        return (
            "Tuning. Moves a threshold, budget or volume; no schema effect, but "
            "a value outside its intended range degrades quietly rather than failing.",
            ADMIN,
        )
    if type_name.startswith("Literal"):
        return (
            "Provider selection. Switches which adapter is constructed at boot.",
            OPERATOR,
        )
    if type_name.startswith(("list", "dict")):
        return ("Routing or allow-list content.", ADMIN)
    return ("Content or identifier used in customer-visible output.", ADMIN)


def _annotation_name(annotation: Any) -> str:
    """A short, stable rendering of a type annotation."""
    if annotation is None:
        return "unknown"
    if isinstance(annotation, type):
        return annotation.__name__
    text = str(annotation)
    text = text.replace("typing.", "").replace("NoneType", "None")
    return text


def render_default(name: str, field: Any) -> str:
    """The default column.

    Secrets never print their default even though every default in the source
    today is a blank or an obvious placeholder. The rule is about the next
    person to edit `config.py`, not about today's values: a real default
    dropped into the code would otherwise be published into a client-facing
    document by a script nobody re-reads.
    """
    if field.is_required():
        return "**required**"
    default = field.default
    if is_secret(name):
        return "blank" if default in ("", None) else "_(not shown: credential)_"
    if default == "":
        return "`\"\"` (blank)"
    if default is None:
        return "`None`"
    return f"`{default!r}`"


# --------------------------------------------------------------------------
# example.env
# --------------------------------------------------------------------------

_ENV_LINE_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=")


def read_example_env(path: Path) -> list[str]:
    """Env var names assigned in `example.env`, in file order."""
    names: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ENV_LINE_RE.match(line.strip())
        if match:
            names.append(match.group(1))
    return names


_COMPOSE_KEY_RE = re.compile(r"^\s{2,}([A-Z][A-Z0-9_]{2,}):")
_COMPOSE_SUBST_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]{2,})")


def read_compose_env(paths: list[Path]) -> set[str]:
    """Env var names compose either assigns to a service or substitutes.

    Both forms count as "compose provides a path for this value": an assigned
    key reaches the container directly, and a `${VAR}` substitution means the
    tenant env file's value is forwarded even when `example.env` never mentions
    it. Deliberately a text scan rather than a YAML parse -- no yaml dependency
    is needed for a question this shallow, and a scan cannot fail on the
    `x-chatwoot-env` anchor syntax.
    """
    found: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                continue
            key = _COMPOSE_KEY_RE.match(line)
            if key:
                found.add(key.group(1))
            found.update(_COMPOSE_SUBST_RE.findall(line))
    return found


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def _cell(text: str) -> str:
    """Make a string safe inside a markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ").strip() or "—"


def where_set(env_name: str, example_set: set[str], compose_set: set[str]) -> str:
    """Which of the three places an operator can find this setting."""
    if env_name in example_set:
        return "`example.env`"
    if env_name in compose_set:
        return "compose"
    return "**nowhere**"


def build_document() -> str:
    example_names = read_example_env(EXAMPLE_ENV)
    example_set = set(example_names)
    compose_set = read_compose_env(COMPOSE_FILES)

    out: list[str] = []
    w = out.append

    w("# Functional configuration document")
    w("")
    w("**Generated. Do not edit by hand.**")
    w("")
    w("```")
    w("python3 scripts/generate-config-doc.py")
    w("```")
    w("")
    w(
        "Every row below is read out of the `Settings` classes named in each "
        "section — field name, type and default from the class itself, "
        "description from the comment above the field in the source. Editing "
        "this file directly is pointless: the next run overwrites it, and "
        "`scripts/test_generate_config_doc.py` fails the build while it is stale."
    )
    w("")

    # ---- How to read it -------------------------------------------------
    w("## How to read this document")
    w("")
    w(
        "**Env var name.** Field names map case-insensitively to environment "
        "variables, so the setting `sla_engine_enabled` is set as "
        "`SLA_ENGINE_ENABLED`. Names must match verbatim; there is no prefix "
        "and no alias table."
    )
    w("")
    w(
        "**Default.** Class-level defaults, not the values on any running "
        "tenant. `**required**` means the service refuses to boot without it. "
        "Credential-shaped settings never print a default value here even when "
        "the source has a harmless placeholder — see `render_default()` in the "
        "generator for why."
    )
    w("")
    w(
        "**Blast radius** and **Who may change it** are *derived by rule* from "
        "each setting's name, type and default by `classify()` in the "
        "generator. They are a consistent first cut across 256 settings, not a "
        "human risk assessment of each one, and they should not be read as "
        "though someone weighed every row. Where a specific setting deserves a "
        "real assessment, the place to write it is the comment above the field "
        "in `config.py`, which appears in the Description column and cannot "
        "drift from the code."
    )
    w("")
    w(
        "**Set in.** Where an operator finds this setting today, in one of three "
        "states. `` `example.env` `` means it is in the per-tenant template "
        "`add-tenant.sh` copies, so it is discoverable. `compose` means "
        "`docker-compose.tenant.yml` (or the infra file) either assigns it or "
        "forwards it, so it has a value and usually should not be touched — "
        "`CHATWOOT_URL` is a required agent setting supplied as an internal "
        "docker hostname, and no tenant should ever set it. **`nowhere`** means "
        "neither: the setting exists only in `config.py`, at its code default, "
        "and an operator has no way to discover that it exists. That third "
        "state is the finding, and the drift table at the end of this document "
        "lists every instance."
    )
    w("")

    # ---- Caveats --------------------------------------------------------
    w("## Settings that do not gate what their name suggests")
    w("")
    w(
        "These live in the generator rather than in a hand-edited section of "
        "the document, so a regeneration cannot drop them. Each one cost an "
        "operator a support conversation."
    )
    w("")
    for entry in FLAG_CAVEATS:
        w(f"- {entry}")
    w("")

    # ---- Per-service tables --------------------------------------------
    total = 0
    set_nowhere: list[tuple[str, str, str]] = []
    all_field_names: set[str] = set()

    for heading, module_name, source_path, scope in SOURCES:
        settings_cls = load_settings_class(module_name)
        model_fields = settings_cls.model_fields
        parsed = parse_source(source_path)
        parsed_by_name = {field.name: field for field in parsed}

        w(f"## {heading}")
        w("")
        w(scope)
        w("")
        w(
            f"Source: `{source_path.relative_to(REPO_ROOT)}` — "
            f"{len(model_fields)} settings."
        )
        w("")

        section = None
        for field_doc in parsed:
            name = field_doc.name
            field = model_fields.get(name)
            if field is None:
                # Annotated in the source but not a model field. Nothing in
                # either class does this today; skip rather than guess.
                continue
            total += 1
            all_field_names.add(name)

            if field_doc.section != section:
                section = field_doc.section
                w(f"### {section or 'General'}")
                w("")
                w("| Env var | Type | Default | Blast radius | Who may change it | Set in | Description |")
                w("|---|---|---|---|---|---|---|")

            blast, who = classify(name, field.annotation, field.default)
            env_name = name.upper()
            location = where_set(env_name, example_set, compose_set)
            if location == "**nowhere**" and name not in EXAMPLE_ENV_EXEMPT:
                set_nowhere.append(
                    (env_name, heading, "required" if field.is_required() else "default")
                )

            w(
                "| `{env}` | `{type}` | {default} | {blast} | {who} | {in_env} | {desc} |".format(
                    env=env_name,
                    type=_cell(_annotation_name(field.annotation)),
                    default=_cell(render_default(name, field)),
                    blast=_cell(blast),
                    who=_cell(who),
                    in_env=location,
                    # A blank description is a statement about the source, not a
                    # claim about the setting, and it should read as one: the
                    # field has no explanatory comment above it in `config.py`,
                    # and the fix is to write one there rather than here.
                    desc=_cell(field_doc.description) if field_doc.description
                    else "_(no comment in source)_",
                )
            )
        w("")

        # Fields present in model_fields but not found by the AST walk would be
        # a generator bug rather than a repo fact, so surface them loudly.
        unparsed = sorted(set(model_fields) - set(parsed_by_name))
        if unparsed:
            w(
                f"> **Generator warning:** {len(unparsed)} field(s) in "
                f"`{module_name}` were not matched to a source declaration and "
                f"are missing above: {', '.join(f'`{n}`' for n in unparsed)}."
            )
            w("")

    # ---- Drift ----------------------------------------------------------
    w("## Drift: settings an operator cannot discover")
    w("")
    w(
        "This is the section with teeth. CLAUDE.md requires that a new setting "
        "be added to both `config.py` and `deploy/tenants/example.env`; this "
        "table is what happens when it is not, and "
        "`test_a_setting_present_in_code_but_missing_from_example_env_is_flagged` "
        "keeps it honest."
    )
    w("")
    w(
        "Every row below is set in **neither** `example.env` nor either compose "
        "file. It exists at its code default, and the only way to learn it "
        "exists is to read `config.py`."
    )
    w("")
    w(
        "**A row here is not automatically a bug.** Many are internal tunables "
        "no tenant should override. A row *is* a bug in two cases: when it is "
        "marked `required` (the service will not boot and nothing tells the "
        "operator which variable is missing), and when it gates a feature the "
        "client has been told they can enable — which is precisely how a "
        "feature ends up correct, tested and unreachable, nine times in this "
        "programme so far."
    )
    w("")
    w(
        f"{len(set_nowhere)} of {total} settings are set nowhere but in code."
    )
    w("")
    w("| Env var | Service | Boot |")
    w("|---|---|---|")
    for env_name, heading, requiredness in sorted(set_nowhere):
        marker = "**required**" if requiredness == "required" else "has a default"
        w(f"| `{env_name}` | {_cell(heading)} | {marker} |")
    w("")

    if EXAMPLE_ENV_EXEMPT:
        w("Exempt from the check above, with the reason:")
        w("")
        w("| Env var | Why it is not in `example.env` |")
        w("|---|---|")
        for name, reason in sorted(EXAMPLE_ENV_EXEMPT.items()):
            w(f"| `{name.upper()}` | {_cell(reason)} |")
        w("")

    # ---- Deploy-only ----------------------------------------------------
    deploy_only = [
        name for name in example_names if name.lower() not in all_field_names
    ]
    w("## `example.env` entries that are not service settings")
    w("")
    w(
        "These are read by Docker Compose, Caddy, Chatwoot or the provisioning "
        "scripts rather than by either `Settings` class, so they have no row "
        "above. They are listed because an operator editing `example.env` "
        "cannot otherwise tell the two kinds apart, and because an entry here "
        "that nothing reads is dead configuration."
    )
    w("")
    w(f"{len(deploy_only)} of {len(example_names)} entries.")
    w("")
    for name in deploy_only:
        w(f"- `{name}`")
    w("")

    return "\n".join(out) + "\n"


# Curated caveats. In the generator, not in the document, so regeneration
# cannot silently drop them. Every entry names a setting whose name overpromises
# and states the current, verified position -- not the intended one.
FLAG_CAVEATS = [
    "**`FAQ_SUGGESTION_POPUP_ENABLED`** and **`INBOUND_ALERTS_ENABLED`** were "
    "each *two* independent switches until recently: the backend setting, and "
    "the Chatwoot fork's own `PROTON_FEATURES` list, which is what the SPA "
    "actually reads via `hasFeature(...)`. An operator who flipped the "
    "documented setting got nothing — twice, in two different packages. "
    "`deploy/chatwoot-fork/patches/0058-feature-flag-unification.patch` plus "
    "`deploy/docker-compose.tenant.yml` now derive the feature list from the "
    "same variable, and `test_p9_task7_feature_flag_unification.py` (8 tests) "
    "renders the shipped ERB through Ruby's own `erb` to prove it. **What is "
    "still unproven: patch 0058 has never been applied to a real Chatwoot "
    "checkout or built into an image**, so on any tenant running a pre-0058 "
    "image both settings are still two switches and `PROTON_FEATURES` must be "
    "set by hand. Check the deployed image before telling an operator one "
    "switch is enough.",
    "**`INBOUND_ALERTS_ENABLED` does not gate the backend alert-rule store or "
    "`/alerts/rules`.** That is `ALERT_RULES_ENABLED`, a genuinely separate "
    "feature with its own switch. The two names invite the assumption that one "
    "implies the other.",
    "**`NORMALISE_RETRIEVAL_QUERY_ENABLED` is not a setting at all** and does "
    "not appear in the tables below. It is a module constant in "
    "`nlu_normalise.py`, deliberately, because the Malay SMS query normaliser "
    "ships off pending a real-credential measurement of whether it improves the "
    "corpus pass rate. There is no env var to flip.",
    "**`RESOLVED_CASE_INDEX_ENABLED` builds a corpus that nothing queries.** "
    "Turning it on embeds resolved-case summaries into pgvector and no agent-"
    "facing surface reads them, so it buys storage and no visible behaviour. "
    "See the blocked-work register §3f.",
    "**`FOLLOW_UP_DATE_ENABLED` has no Chatwoot UI.** The field works end to "
    "end in both services; there is no patch that renders it, so on today's "
    "image the flag being off is the honest state (register §3d).",
    "**`PRESENCE_THRESHOLD_ALERTS_ENABLED` without "
    "`PRESENCE_CUSTOM_STATUSES_ENABLED` correctly produces no alerts at all** — "
    "there is then no way for an agent to record an absence in the first place. "
    "Neither of Chatwoot's native `busy` or `offline` ever counts as an absence, "
    "by design (register §3e).",
    "**`CALL_RECORDING_RETRIEVAL_ENABLED` gates an endpoint that can only "
    "answer the empty state.** `features/chat/phone/recording_router.py` was "
    "written, unit-tested against its own throwaway `FastAPI()` and included by "
    "nothing else at all, so `GET /calls/{conversation_id}/recording` returned "
    "404 at every value of this setting; the P11 wiring change to `main.py` "
    "mounts it. **Check `main.py` before quoting the mount to an operator.** "
    "Mounted or not, the handler reads an in-process registry "
    "(`_RECORDING_RETENTIONS`) that nothing in production writes to, so against "
    "a real conversation it answers \"no recording exists\" either way. The "
    "Chatwoot custom-attribute read that would populate it is owed.",
    "**`REPORTING_TIMEZONE` re-buckets every historical figure** on every "
    "dashboard the next time `ensure_views()` runs. Totals do not change; cases "
    "slide between adjacent days, weeks and months, which is why it reads as "
    "\"close but not quite\" rather than obviously broken. Run "
    "`scripts/compare-reporting-timezone.py` first and keep the output.",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed document differs from what would be generated",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="print the document instead of writing it",
    )
    args = parser.parse_args(argv)

    document = build_document()

    if args.stdout:
        sys.stdout.write(document)
        return 0

    if args.check:
        if not OUTPUT_PATH.exists():
            print(f"{OUTPUT_PATH} does not exist; run this script.", file=sys.stderr)
            return 1
        current = OUTPUT_PATH.read_text(encoding="utf-8")
        if current != document:
            print(
                f"{OUTPUT_PATH.relative_to(REPO_ROOT)} is stale. "
                "Run: python3 scripts/generate-config-doc.py",
                file=sys.stderr,
            )
            return 1
        print("configuration.md is current.")
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(document, encoding="utf-8")
    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)} ({len(document)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
