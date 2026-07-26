# Proton e.MAS IVR — Twilio Studio Flow

Voice IVR for **1300-888-877**, generated from the `IVR Call` sheet of
`docs/CRM Process Flow (1).xlsx`.

Files:

| File | What it is |
|------|-----------|
| `ivr-studio-flow.json` | Importable Twilio **Studio Flow** definition — **fully self-contained** (office-hours logic is inline, no external Function) |

## Flow at a glance

```
Incoming call
  └─ split_holiday ──public holiday?──► weekend-hours window
  └─ split_dow     ──Sat/Sun?────────► weekend-hours window
                   └─ else ──────────► weekday-hours window
       ├─ outside window ─► after-hours message (EN + BM) ─► voicemail ─► goodbye
       └─ inside window  ─► language_gather  (1=English, 2=Bahasa Melayu)
                             └─ main menu (per language)
                                  1 = Roadside Assistance (RSA)  ─► dial RSA number
                                  2 = Inquiry            ┐
                                  3 = Complaint          ┘─► dial Non-RSA number
                                  0 = repeat menu
                                        └─ dial (ring 20s)
                                             ├─ answered  ─► goodbye
                                             └─ no answer ─► "agents busy" prompt ─► voicemail
```

Business hours (Malaysia time, UTC+8) — computed **inline** by the Split widgets:
- **Mon–Fri** 08:30–17:30
- **Sat/Sun/Public Holidays** 09:00–17:00

### How the inline hours check works (no Function)

The Split widgets test Liquid expressions on the current time. Because Twilio
renders `'now'` in US Pacific time, timezone conversion is done on the **absolute
epoch** (`{{ 'now' | date: '%s' }}`, which is timezone-independent) using
integer math only — so it is DST-safe:

| Widget | `input` expression | Meaning |
|--------|--------------------|---------|
| `split_holiday` | `{{ 'now' \| date: '%s' \| plus: 28800 \| date: '%Y-%m-%d' }}` | today's MYT date → `matches_any_of` the holiday list |
| `split_dow` | `{{ 'now' \| date: '%s' \| plus: 28800 \| divided_by: 86400 \| plus: 4 \| modulo: 7 }}` | MYT day of week, 0=Sun…6=Sat → `matches_any_of` `0,6` |
| `hours_weekday_*` / `hours_weekend_*` | `{{ 'now' \| date: '%s' \| plus: 28800 \| modulo: 86400 \| divided_by: 60 }}` | MYT minute-of-day (0–1439), range-checked (weekday 510–1050, weekend 540–1020) |

To change office hours, edit the numeric thresholds in the `hours_*` widgets
(minutes since midnight: 08:30=510, 17:30=1050, 09:00=540, 17:00=1020).

## Placeholders you MUST replace

### In `ivr-studio-flow.json`
| Placeholder | Where | Replace with |
|-------------|-------|--------------|
| `+60300000001` | `dial_rsa_en`, `dial_rsa_ms` → `to` | Real **RSA** agent/hunt-group number (E.164) |
| `+60300000002` | `dial_nonrsa_en`, `dial_nonrsa_ms` → `to` | Real **Non-RSA** agent/hunt-group number (E.164) |
| `{{trigger.call.To}}` | all `dial_*` → `caller_id` | Keep as-is to show the Proton DID as caller ID, or set a Twilio-verified number. Most carriers reject a non-Twilio caller ID. |

### Public holidays
Edit the `value` (comma-separated `YYYY-MM-DD` list) in the **`split_holiday`**
widget to keep the Malaysian public-holiday list current (national dates; add
state holidays if serviced from one state). No code file to touch.

## Deploy steps

1. **Import the Flow** (Twilio Console → Studio → Create Flow → *Import from JSON* → paste `ivr-studio-flow.json`).
2. Set the real numbers in the four `dial_*` widgets.
3. (Optional) update the holiday list in `split_holiday`.
4. **Publish** the flow.
5. **Wire the phone number**: Phone Numbers → your 1300 DID → *A Call Comes In* → **Studio Flow** → this flow.

## Voice / TTS notes

- Malay prompts use `Google.ms-MY-Standard-A` (female); English prompts use
  `Google.en-US-Standard-C` (female). Amazon Polly has **no Malay voice**, which
  is why Google TTS is used. Enable Google voices under Voice → Settings →
  Text-to-Speech if prompts fall back to the default voice.
- The **one** pre-selection prompt (`language_gather`) is bilingual and can only
  use a single voice — it uses the English female voice. Every prompt *after*
  language selection is fully in the caller's language/voice.

## Known mapping trade-off (Dial vs. Queue)

The spreadsheet describes a **queue** experience for Non-RSA — *"queue busy
prompt (>10s rings)"*, *"please stay on the line"*, *"agent must attend within
20 seconds"*. With the **Dial-a-number** delivery chosen for this flow there is
no hold queue, so it is mapped as: ring the agent number for **20s**, and if
unanswered play the busy prompt then take a **voicemail**. For a true
"stay on the line" hold with wait-music, switch the `dial_*` widgets to an
**Enqueue** into a Twilio Queue/TaskRouter workflow — ask and this can be
regenerated that way.

## Source of truth

Prompt wording (EN + BM), the 1/2/3/0 menu, RSA vs Non-RSA routing, office
hours, and the after-hours message are taken verbatim from
`docs/CRM Process Flow (1).xlsx` → `IVR Call`.
