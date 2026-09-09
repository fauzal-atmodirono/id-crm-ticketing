# Project Summary — CRM & Customer Support Platform

## What this is

A customer support platform (CRM + live chat + WhatsApp + phone) that our
company runs for its own use and can also offer to clients as a branded
service. Each client ("tenant") gets their own private, isolated copy of the
system — their data never mixes with another client's.

On top of the base support platform, we've added an **AI layer** (powered by
Google Gemini) that can:
- Draft replies to customers automatically, for a human agent to review and send
- Answer simple questions on its own, and hand off to a human when it's unsure
- Read a company's own knowledge base/FAQs so its answers stay accurate and on-brand
- Escalate urgent cases to the right person automatically, by email and WhatsApp

Support staff work inside one unified inbox that brings together **live chat,
WhatsApp, email, and phone calls**, instead of juggling separate tools for
each channel.

## Where it currently runs

- **Live** for our client **Proton**, and for **AEON360** (an insurance-adjacent
  client that recently replaced an earlier client, WahChan).
- A demo environment (**Bahana**) is used to showcase and test the AI features
  before rolling them out to real clients.
- We previously ran a second ticketing system (Zammad) alongside the main
  platform; that has been fully retired — everything now runs through the
  one system.

## The customer channels

| Channel | Status |
|---|---|
| Live chat (website widget) | Live and in use |
| Email | Live and in use |
| WhatsApp | Built and tested internally, not yet used on a real client conversation |
| Phone calls (with AI voice assistant) | Built and tested internally, not yet used on a real client call |

---

## What we use Twilio for

**Twilio** is the outside service that gives us WhatsApp and phone-call
capability — it's the "phone company" our platform plugs into. Without it,
customers could only reach us by chat and email.

What it does for us, in plain terms:

1. **WhatsApp messaging** — sends and receives WhatsApp messages between
   customers and our agents (or our AI assistant), including automatically
   alerting the right staff member by WhatsApp when a case is escalated.

2. **Inbound phone calls** — answers calls on our support phone line and
   routes them: business-hours menu, choice of English or Bahasa Melayu,
   and routing to the correct team (e.g. Roadside Assistance vs. general
   enquiries), including an after-hours message and voicemail when no one
   is available.

3. **AI voice assistant on live calls** — streams the caller's voice to our
   Gemini AI in real time so it can hold a spoken conversation with the
   caller, not just play recorded menus.

4. **Browser-based softphone for agents** — lets support agents make and
   receive calls directly from their browser, no desk phone required.

5. **Call recording** — records calls (with a spoken notice to the caller,
   as required by law), then copies the recording into our own secure
   storage so we control it long-term rather than leaving it sitting in
   Twilio indefinitely.

6. **Voicemail** — turns a missed-call voicemail into a support case
   automatically, so it doesn't get lost.

7. **Automated phone menu (IVR)** for our main support number, built and
   managed directly in Twilio — this is the "press 1 for English, 2 for
   Bahasa Melayu" system, with different working hours for weekdays vs.
   weekends/public holidays.

8. **Text-to-speech voices** — uses natural-sounding Google voices for
   English and Bahasa Melayu prompts (Twilio's default voice engine can't
   speak Malay).

**Bottom line:** Twilio is the vendor behind our WhatsApp and phone-call
channels. The WhatsApp and voice/AI-call features are fully built and pass
our internal tests, but as of now **neither has been used on a real,
live customer call or WhatsApp conversation yet** — that's the next step
before we can rely on them in production.
