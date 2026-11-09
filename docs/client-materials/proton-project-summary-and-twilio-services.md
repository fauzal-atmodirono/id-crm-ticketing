# Proton (PRO-NET) — Project Summary & Twilio Services

*Covers our engagement with Proton's e.MAS support centre (PRO-NET), including
what we proposed in our response to RFP 2026_028 (Contact Centre / Case
Management System) and what has actually been built and deployed so far.*

## What we built for Proton

A single support platform that brings every way a Proton customer can reach
e.MAS Centre — **WhatsApp, email, phone calls, Facebook Messenger and
Instagram** — into one agent workspace, against one customer record, instead
of five separate tools.

On top of that, an **AI layer** (Google Gemini) that:
- Drafts or sends replies automatically, and can hold a live conversation
  with a customer on WhatsApp or by voice
- Understands casual/colloquial Malay, code-switching between Malay and
  English, and typos — not just exact FAQ wording
- Detects urgency and sentiment on every incoming message, so angry or urgent
  cases can be prioritised automatically
- Converses in **English, Bahasa Malaysia, Chinese and Tamil**
- Hands off to a human agent whenever it isn't confident

A configurable **rules engine** (no code changes needed) drives:
- Routing complaints to the right department / Person-In-Charge / dealer
- The escalation ladder we committed to in the RFP: **2-hour first-response
  trigger → 8-hour escalation to the next-level manager → 48-hour unresolved-case
  alert**, measured against Proton's business hours
- Roadside Assistance (RSA) calls, which get a dedicated priority route that
  stays available 24/7, even during maintenance windows

**Commercial shape:** there is no per-seat software licence — Proton can add
unlimited agents, supervisors and read-only report viewers at no extra
licence cost, because the CRM core is open-source and self-hosted on
Proton's own Google Cloud account. The only ongoing pass-through costs are
Google Cloud/AI usage, **Twilio** (WhatsApp + phone, billed per-message and
per-minute), Meta messaging, and Power BI seats.

### Where things stand today

| Channel | Status |
|---|---|
| Live chat (web) | Live |
| Email | Live |
| WhatsApp | Built and internally tested — not yet used on a real Proton customer conversation |
| Phone / IVR / AI voice agent | Built and internally tested — not yet used on a real Proton customer call |
| Facebook Messenger / Instagram | Per RFP scope |

---

## What Twilio does for Proton specifically

Twilio is the underlying carrier we use to deliver Proton's **WhatsApp** and
**phone/IVR** channels — everything below was committed to in our RFP 2026_028
response and is what's implemented in the platform today.

1. **WhatsApp Business API** — sends and receives WhatsApp messages
   (including customer voice notes and photo/video attachments) between
   e.MAS customers and Proton's agents or AI assistant. Also sends an
   automatic WhatsApp alert to the responsible Person-In-Charge when a case
   is escalated.

2. **Inbound call routing (IVR)** — answers Proton's support line and routes
   the call based on what the caller *says* (not just key presses): to a
   live agent, or straight to the dedicated 24/7 Roadside Assistance (RSA)
   route. Includes weekday vs. weekend/public-holiday business-hours logic,
   an after-hours message, and voicemail-to-case capture.

3. **Real-time AI voice agent** — streams the caller's voice live to Gemini
   so the AI can hold an actual spoken conversation (in all four languages),
   not just play static menu recordings.

4. **Live call transcription** — converts the call to text in real time, in
   whichever language is actually spoken, shown live on the agent's screen
   and saved with the recording — this satisfies RFP requirement 4.5.1.

5. **In-call agent controls** — transfer to another agent (carrying the
   case, transcript-so-far and Customer 360 context with it), mute, and hold
   with Proton-supplied pre-recorded hold music — RFP requirement 4.6.

6. **Call recording & retrieval** — every call is recorded (with a spoken
   PDPA consent notice first), attached to the case, and searchable/playable
   by authorised staff with a full audit trail. Recordings are then copied
   out of Twilio into Proton's own encrypted Google Cloud storage for the
   required 7-year retention — RFP requirement 4.7.

7. **Post-call & post-chat satisfaction surveys** — the agent can transfer a
   caller to a ratings IVR before hanging up (feeds agent NPS), and a
   WhatsApp CSAT survey fires automatically at the end of a chat, rating the
   AI and the human agent separately — RFP requirements 4.8/4.9.

8. **Browser softphone for agents** — agents make and take calls from their
   workspace, no desk phone required.

9. **Bilingual/multilingual text-to-speech** — Malay IVR prompts use a
   Google voice (Twilio's default engine has no Malay voice), so menus and
   messages sound natural in both English and Bahasa Malaysia.

**Billing note:** WhatsApp and phone/voice minutes through Twilio are
pass-through costs, billed to Proton per-message and per-minute — not part
of our fixed platform fee (see RFP §7.1 commercial response).

**Status note:** the WhatsApp and voice/IVR channels above are fully built
and pass internal testing, but as of now have **not yet carried a real,
live Proton customer conversation or call** — that's the next milestone
before go-live on those two channels.
