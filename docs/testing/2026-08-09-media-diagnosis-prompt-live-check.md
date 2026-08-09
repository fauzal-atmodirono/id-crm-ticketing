# Media diagnosis prompt — live WhatsApp check

> **TEMPLATE — NOT YET RUN.** Every field below is a placeholder. This file
> records no result until someone fills it in against a real tenant, a real
> WhatsApp number, and real Gemini credentials. See
> `docs/analysis/2026-08-09-blocked-work-register.md` §2.4 for why this
> could not be run in the sandbox that built the feature (`GOOGLE_API_KEY=test-key`,
> no WhatsApp number, no live Gemini access).

**Feature under test:** `build_agent_instruction` in
`backend/apps/backend/src/chatbot/features/chat/prompts.py` (P7 task 8) — a
bounded media-diagnosis instruction (confidence statement + at most one
follow-up question) appended to the agent's system instruction only when
`MEDIA_DIAGNOSIS_PROMPT_ENABLED=true` and the turn carries an image or video.

**Unit coverage that already passed** (does not substitute for this check):
`backend/apps/backend/src/chatbot/features/chat/test_media_prompt.py`, all
seven named tests, asserting the composed instruction string only — no real
model was ever involved.

---

## Preconditions (fill in before running)

- [ ] Tenant: `___________`
- [ ] `MEDIA_DIAGNOSIS_PROMPT_ENABLED=true` on that tenant's backend
- [ ] `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=true` on the same tenant (§2.3 of
      the blocked-work register — this is also its first real test)
- [ ] Real Gemini credentials configured (not `GOOGLE_API_KEY=test-key`)
- [ ] A real WhatsApp number connected to the tenant's inbox
- [ ] A test photo ready: a visible, unambiguous vehicle fault (e.g. a dented
      door panel, a dashboard warning light, a cracked bumper)

## Steps

1. From the test WhatsApp number, send the photo with a short caption (e.g.
   "what's wrong with this?").
2. Record the bot's reply verbatim.
3. Send one plain-text follow-up with no attachment (e.g. "any update?").
4. Record that reply verbatim too — it must show no trace of the
   media-diagnosis instruction now that no media is attached.

## What would count as passing

- [ ] The reply to the photo names something **specific** it observed (the
      part, the damage, the light), not a generic "please describe the
      issue further."
- [ ] The reply states an **explicit confidence level** about its diagnosis.
- [ ] The reply asks **at most one** follow-up question — never a checklist
      of several.
- [ ] The plain-text follow-up's reply shows no diagnostic-instruction
      artifacts (no confidence statement, no forced follow-up question) —
      confirming the instruction is scoped to turns that actually carry media.

## Result

_(leave blank until run — do not fill this in speculatively)_

- Date run:
- Run by:
- Tenant / WhatsApp number used:
- Photo sent (attach or describe):
- Bot's reply to the photo (verbatim):
- Bot's reply to the plain-text follow-up (verbatim):
- Pass / fail against each checkbox above:
