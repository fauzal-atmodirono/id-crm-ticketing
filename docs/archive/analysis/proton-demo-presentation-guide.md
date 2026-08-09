# Proton CRM — Demo Presentation Guide (mapped to the Process Flow workbook)

**Purpose.** Present the CRM to Proton by walking their own `CRM Process Flow` SOP
(WhatsApp / Social / Email / IVR / SSI) and showing the live system performing each
step. This guide gives the framing, a setup checklist, a click-by-click demo script,
and an honest live/partial/roadmap status per SOP row.

**Live tenant:** `http://proton.crm.34-50-103-151.nip.io` (Chrome, HTTP).

---

## 1. How to frame it (2 min opening)

- "You gave us a 5-channel process-flow SOP. We built it on **Chatwoot Community
  (free, self-hosted)** + a Gemini AI layer — no per-seat enterprise licences — and made
  it **multi-tenant**, so this same platform serves Proton and any future customer."
- "I'll walk your SOP top to bottom. Green = live today, amber = built and one switch
  away, roadmap = needs an external provider (telephony) or your app team (SSI)."
- Lead with **WhatsApp** and **Email** — they are the most complete and match your
  workbook text exactly.

---

## 2. Pre-demo setup checklist (do 15 min before)

1. **Log in** to `proton.crm...nip.io` and keep the Conversations view open.
2. **Business hours** — Settings → Inboxes → *Proton API* → Business Hours: set your
   Mon–Fri 8:30–17:30 / Sat–Sun 9:00–17:00 and an out-of-office reply (so the
   out-of-hours branch is demoable). Optional but recommended.
3. **"Demo mode" timings** (so idle→close happens in ~1 min instead of 15) — ask me to
   set, in `proton.env`: `LIFECYCLE_IDLE_WARN_MINUTES=1`, `LIFECYCLE_IDLE_CLOSE_GRACE_MINUTES=1`,
   `LIFECYCLE_CONFIRM_GRACE_MINUTES=1`, then recreate the agent. Revert to 10/5/10 after.
4. **A chat inbox to type into** — either the existing *Proton API* inbox, or connect the
   **Website widget** (Settings → Inboxes → Add → Website) for a realistic "customer" window.
5. **Knowledge is populated** — Knowledge → Documents should list the 70 KB docs (proves
   "AI answers based on FAQ").
6. Have the **workbook open** on a second screen to point at each row as you demo it.

---

## 3. The golden-path live demo (WhatsApp sheet, ~8 min)

Do this as one continuous story; it covers ~70% of the workbook.

| # | You do | Customer sees / SOP row it proves |
|---|---|---|
| 1 | Start a new conversation (widget or API inbox) as "customer" | **AI disclaimer** posts automatically — read it against the workbook DISCLAIMER row (identical text). |
| 2 | Type a product question in **Malay**, then another in **English** | Bot replies **in the same language** (SOP: "respond in the same language"). |
| 3 | Point at the reply grounded in KB | "AI answers based on **FAQ** (Vertex AI Search over your 70 KB docs)" — open Knowledge → Documents to show the source. |
| 4 | Stop replying (demo timings make this ~1 min) | **Idle warning**: "Your chat will close in 5 minutes…" then **auto-close**: "Close due to inactive" (SOP idle-10min / warn / autoclose rows). |
| 5 | Bot asks **"Does your case resolve? YES/NO"** | The resolution gate. Reply **NO** → conversation reopens. Reply **YES** → proceeds. |
| 6 | Bot posts **"Rate our AI 1–5"** | The **AI-performance rating survey** row. Send a rating. |
| 7 | Show the closed conversation's **labels** | A `category_*` label was auto-applied — SOP: "bot must assign the appropriate case category/division." |
| 8 | (If routing enabled) type "I want a live agent" | Hand-off to a human + the **agent-performance survey** on resolution. |

Then switch to **Email**: show the once-per-thread **auto-acknowledgement** (workbook
text is identical), and note the reply-thread does *not* re-trigger it.

---

## 4. SOP → CRM status map (use as your speaker notes)

Legend: 🟢 **Live now** · 🟡 **Built, flip a switch** · 🔭 **Roadmap / external dependency**

### WhatsApp Process
| SOP row | Status | How it's done / what to say |
|---|---|---|
| AI triggered → **Disclaimer** | 🟢 | Posted on conversation-created; verified live, exact workbook text. |
| Respond in same language | 🟢 | Gemini prompt enforces same-language replies. |
| Outside business hours auto-reply | 🟢 | Native Chatwoot business hours + out-of-office (configure the inbox). |
| AI answers from FAQ | 🟢 | Vertex AI Search over the Knowledge base (70 docs). "CRM furnishes the FAQ" = Knowledge → FAQs authoring. |
| Idle 10 min → warning | 🟢 | Lifecycle scanner. |
| Auto-close (15 min in-hours / 10 out) | 🟢 | Lifecycle; thresholds are per-tenant config. |
| "Case resolved? YES/NO" | 🟢 | Resolution gate; NO reopens, YES → survey. |
| AI-performance rating survey | 🟢 | Lifecycle survey → recorded for reporting. |
| Bot assigns case category/division | 🟢 | Auto-categorization via Gemini (Vertex/ADC) → `category_*` label. |
| Live-agent priority routing (WA>Call>Email>Social) | 🟡 | Agent Routing & Presence (Phase 5) built; enable `ROUTING_ENABLED` + channel priorities. |
| Agent acknowledge within 2 min | 🟡 | Per-channel SLA (`whatsapp:2`) set; needs the backend SLA engine on. |
| Reassign by Team Leader | 🟢 | Native Chatwoot assignment. |
| Agent-performance survey | 🟢 | Agent-variant survey on human-resolved. |
| Escalation email flow | 🟡 | Escalation/PIC engine (Phase 2) built; enable + set the PIC map. |

### Social Media (FB & IG)
| SOP row | Status | Notes |
|---|---|---|
| Log ticket on FB/IG post | 🟡 | Chatwoot has native FB/IG inboxes — **connect the Meta account** to demo live; the lifecycle then applies as per WhatsApp. |
| Out-of-hours auto-reply, assign next business hour | 🟢 | Same lifecycle/business-hours engine. |
| Priority routing, 2-working-hour ack, survey, escalation | 🟡 | Same routing/SLA/escalation switches as WhatsApp. |

### Email
| SOP row | Status | Notes |
|---|---|---|
| Once-per-thread auto-acknowledgement (new email→ack; reply→none; agent reply→none; new subject→ack again) | 🟢 | Implemented exactly to spec; workbook text is the default template. Needs an **email inbox** connected. |
| Assign to agent, attend next business hour | 🟢 / 🟡 | Assignment native; auto-routing 🟡. |
| Agent updates status within **4 working hours** | 🟡 | Per-channel SLA (`email:240` min) set; SLA engine flip. |
| Rating survey / escalation | 🟢 / 🟡 | Survey live; escalation 🟡. |

### IVR Call
| SOP row | Status | Notes |
|---|---|---|
| Call 1300-888-877, office-hour check, female AI voice, queue prompt, answer ≤20s | 🔭 | Backend has **Gemini Live STT + call-transcript→ticket** built, but a **PSTN/telephony provider (CTI) is not procured** — this is the one channel that needs an external phone vendor. Present as roadmap; show the transcript-to-conversation capability if asked. |

### SSI Process (e.MAS app survey)
| SOP row | Status | Notes |
|---|---|---|
| Customer installs e.MAS app → in-app survey (Profile → Customer Survey), dealer verifies phone | 🔭 | This flow lives in the **e.MAS mobile app + dealer process**, not the CRM. The CRM can **ingest and report** SSI survey results (reporting layer) — position as an integration, not a CRM screen. |

---

## 5. Also worth showing (differentiators beyond the flow)

- **Knowledge / Assistants** (left nav) — the self-built Captain replacement: FAQs,
  Documents, Assistants, Playground, Tools, Scenarios, Inboxes.
- **Reports** — native Chatwoot reports **plus** Proton analytics tabs (Anomaly,
  Departments & PIC, Case Lifecycle) and merged SLA/CSAT/Bot/Agent sections.
- **Multi-tenant** — mention the same stack runs isolated per customer on one VM.

---

## 6. Positioning / talking points

- **Cost:** built on Chatwoot **Community** (free) + Gemini — no enterprise per-seat fees;
  the "Captain AI / Upgrade" enterprise wall is replaced by our own Knowledge + Copilot.
- **AI is Google Gemini on Vertex AI**, grounded on Proton's own KB (not a black box).
- **Everything maps to your SOP** — you're not adapting to our product; we implemented
  your process flow.

---

## 7. Be ready to answer (known gaps)

- **Telephony/IVR** — needs a phone provider (Twilio/CTI); STT + transcript logging are
  already built, only the carrier link is pending.
- **Social** — one-time Meta (FB/IG) connection to go live.
- **Routing / SLA / Escalation-email** — built and tested; currently gated off on this
  tenant; can be switched on per your rollout.
- **SSI** — app/dealer-side process; CRM's role is reporting/ingestion.

---

## 8. Ask me to prep the environment

Before the meeting I can, on request: set **demo-mode timings**, **enable routing +
escalation + the SLA engine** for a fuller live run, connect a **website widget** as the
customer window, and add a **Gemini API key / confirm Vertex** so categorization is
visibly labelling. Tell me how "live" you want the amber items and I'll flip them.
