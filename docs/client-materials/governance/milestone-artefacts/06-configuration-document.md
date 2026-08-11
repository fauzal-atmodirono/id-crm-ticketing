# 06 — Functional configuration document

**Requirement:** §2.3.4, §6.3.2 · **Status:** ready to sign

## Traceability

| Evidence | Path |
|---|---|
| **The document** | `docs/client-materials/handover/configuration.md` |
| Generator | `scripts/generate-config-doc.py` |
| Generator tests | `scripts/test_generate_config_doc.py` (8 tests) |
| Source of truth — backend | `backend/apps/backend/src/chatbot/platform/config.py` |
| Source of truth — agent | `agent/app/config.py` |
| Tenant template | `deploy/tenants/example.env` |

## Why this one is the strongest artefact in the set

**It is generated, and it is the only artefact that cannot drift.** 256 settings
across two services, with roughly forty added by this programme; a hand-maintained
list of that size is wrong within a sprint and wrong in the worst way, because it
looks maintained.

Three properties make it trustworthy rather than merely automated:

- **Nothing reads the environment.** Defaults come from `Settings.model_fields`,
  class-level metadata. A generator that instantiated `Settings()` would emit
  whatever the host had exported — pydantic-settings reads `os.environ` even when
  handed `_env_file=None` — and would emit a *different* document under the
  all-flags-on test gate.
- **Descriptions are the source comments**, parsed with `ast`. The document
  cannot say something the code does not. A field with no comment renders
  "(no comment in source)" rather than a blank, because a blank is a statement
  about the source and should read as one.
- **`--check` fails the build while the committed copy is stale**, so a
  hand-edited copy cannot survive. A generator whose output was edited afterwards
  is worse than no generator.

**Blast radius and "who may change it" are derived by rule and the document says
so in those words.** They are a consistent first cut across 256 rows, not a human
risk assessment of each one.

## Regenerate after any settings change

```bash
cd backend/apps/backend
GOOGLE_API_KEY=test-key uv run python ../../../scripts/generate-config-doc.py
```

## The finding inside it

The drift table is the part with teeth: **90 of 256 settings are set in neither
`example.env` nor either compose file**, so the only way to learn they exist is to
read `config.py`. Among them are **all eight Twilio credentials** — no phone or
WhatsApp feature works without them and nothing tells an operator they exist — and
14 of the 16 `PHONE_*` settings.

The document also names eight settings that **do not gate what their name
suggests**, including `DATA_SCOPED_RBAC_ENABLED`, which gates nothing at all. Those
caveats live in the generator rather than in the markdown, so a regeneration
cannot drop them.
