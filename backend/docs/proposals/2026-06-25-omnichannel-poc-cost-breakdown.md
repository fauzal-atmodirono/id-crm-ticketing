# Omnichannel POC — Cost Breakdown (Twilio + Meta WhatsApp + Google)

**Cost reference for the WhatsApp / Email / Phone → AI → Zendesk integration**

- **Date:** 2026-06-25
- **Region:** Malaysia (+60). Meta added **MYR billing** effective 2026-04-01;
  rates below are quoted in **USD**.
- **Status:** For review / quoting
- **Related:** [POC proposal](2026-06-25-poc-omnichannel-ai-zendesk-integration.md)
  · [engineering spec](../superpowers/specs/2026-06-24-whatsapp-twilio-zendesk-integration-design.md)

> **Rate disclaimer.** Figures are taken from current public rate cards (sources
> at the bottom) and are accurate to the listed dates, but vendor pricing changes
> periodically. **Confirm against the official pages before contractually quoting
> the customer.** Items marked *(confirm)* were not on the primary rate page.

---

## 1. Twilio rates

Source: Twilio current rates (USD), retrieved 2026-06-25.

| Service | Billed per | Rate (USD) | Used in POC? |
|---|---|---|---|
| **WhatsApp Business API** | message (send *or* receive) | **$0.005** | ✅ Phase A — Twilio's fee (Meta's fee is separate — see §2) |
| **Programmable Voice — inbound** | minute | **$0.0085** to receive | ✅ Phase C |
| **Programmable Voice — outbound** | minute | $0.014 to make | Only if callbacks are added |
| **ConversationRelay** | minute | **$0.07** | ✅ **Phase C — chosen** (Twilio-managed STT+TTS; reuses `handle_turn`; picked for the 2026-07-07 demo) |
| **Voice Media Streams** | — | *no separate line on rate page; bundled with voice minutes* | ⏸️ deferred — cheaper-per-minute optimization for higher volume (see §6) |
| **Phone number rental (MY)** | number / month | **$4 local** · **$25 toll-free** *(Twilio Console, +60)* | ✅ one shared number (WhatsApp + voice); local assumed |
| SMS | message | $0.0083 | ❌ not used |
| Conversations API | active user / month | $0.05 | ❌ not used (we orchestrate ourselves) |
| Conversation Intelligence / Orchestrator / Memory | per 1k chars | various | ❌ not used (Gemini does this) |
| Verify | verification | $0.05 | ❌ not used |

## 2. Meta WhatsApp rates (Malaysia)

Source: WhatsApp Business Platform pricing, **per-delivered-message** model
(replaced per-conversation pricing in 2025); Malaysia rates as of 2026-01-01.

| Category | Rate (USD / msg) | When it applies to us |
|---|---|---|
| **Service** | **FREE** | **This is virtually all of our usage** — the AI only ever *replies* to a customer who messaged first, inside the 24-hour customer-service window |
| Utility | $0.0140 | Only if we send a utility *template* outside the free window (not in POC scope) |
| Authentication | $0.0140 | Not used |
| Authentication-International (MY special rate) | $0.0418 | Not used |
| Marketing | $0.0860 | Not used (no outbound marketing in scope) |

**Free windows that benefit us:**
- Service messages within the **24-hour customer-service window** → free.
- Utility messages sent *in response* to a user → free.
- **72-hour free entry-point window** when a customer arrives via a
  Click-to-WhatsApp ad or Facebook Page CTA → all categories free.

**Net effect for this POC:** because our AI is purely *reactive* (it replies to
inbound messages), our WhatsApp conversations are **Meta "service" category = $0
from Meta**. We pay only **Twilio's $0.005/message**.

## 3. Google Cloud rates (phone only — for total cost of ownership)

The phone channel does its own speech processing on Google Cloud. These are **GCP
costs, not Twilio**, and are *(indicative — confirm)*.

| Service | Billed per | Indicative rate |
|---|---|---|
| Speech-to-Text (streaming) | minute | ~$0.016–0.024 |
| Text-to-Speech (Neural/Gemini voices) | per 1M characters | ~$4 (standard) – $16 (neural) |
| Gemini inference (the AI itself) | per token | small per turn; applies to **all** channels |

Blended speech cost is roughly **~$0.02–0.03 / call-minute**. Gemini inference per
text/voice turn is small (Gemini 2.5 Flash tier) but applies to every channel.

## 4. Cost by channel (summary)

| Channel | Twilio | Meta | Google | Net running cost |
|---|---|---|---|---|
| **WhatsApp** | $0.005 / message | **$0** (service category) | — | **~$0.005 / message** |
| **Email** | — | — | Gemini inference only | **~$0** (Zendesk-native) |
| **Phone (ConversationRelay)** ✅ chosen | $0.0085 + $0.07 / min | — | — | **~$0.078 / min** |
| **Phone (Media Streams + Google)** ⏸️ later | $0.0085 / min | — | ~$0.02–0.03 / min | **~$0.03–0.05 / min** |
| **Shared number (MY)** | $4 local / $25 toll-free / month | — | — | fixed monthly |

## 5. Worked monthly scenarios

> Replace assumptions with the customer's expected volumes. Phone uses the
> **chosen POC option, ConversationRelay** (~$0.078/min). The "optimization"
> column shows the cost if phone later moves to Media Streams + Google (~$0.04/min).

**Assumptions per scenario:**
- WhatsApp message = one inbound or one outbound (both billed by Twilio).
- A "conversation" ≈ 8 messages (4 customer + 4 AI), all service-category.
- A "call" ≈ 4 minutes.

### Scenario A — Low volume (200 WA conversations, 50 calls = 200 min)
| Item | Calc | Monthly |
|---|---|---|
| Shared number (local) | 1 × $4 | $4 |
| WhatsApp (Twilio; Meta = $0) | 200 × 8 × $0.005 | $8 |
| Phone — voice + ConversationRelay | 200 × ($0.0085 + $0.07) | ~$15.70 |
| Email | — | $0 |
| **Total (ConversationRelay)** | | **≈ $28 / mo** |
| *Total if Media Streams later* | *200 min × ~$0.04 + $12* | *≈ $20 / mo* |

### Scenario B — Medium volume (500 WA conversations, 200 calls = 800 min)
| Item | Calc | Monthly |
|---|---|---|
| Shared number (local) | 1 × $4 | $4 |
| WhatsApp (Twilio; Meta = $0) | 500 × 8 × $0.005 | $20 |
| Phone — voice + ConversationRelay | 800 × ($0.0085 + $0.07) | ~$62.80 |
| Email | — | $0 |
| **Total (ConversationRelay)** | | **≈ $87 / mo** |
| *Total if Media Streams later* | *800 min × ~$0.04 + $24* | *≈ $56 / mo* |

**Formula (for a spreadsheet):**
```
Monthly =  number_rental
        +  WA_messages   × 0.005                    (Twilio; Meta service = 0)
        +  call_minutes  × (0.0085 + 0.07)          [chosen: voice + ConversationRelay]
   OR   +  call_minutes  × (0.0085 + ~0.025)        [later optimization: voice + Google STT/TTS]
        +  email         × 0
        +  gemini_inference (small, all channels)
```

## 6. Phone: build-vs-buy (the one real cost decision)

| | Media Streams + Google (Option A) | ConversationRelay (Option B) |
|---|---|---|
| Per-minute cost | **~$0.03–0.05** | **~$0.078** |
| Engineering effort | Higher — we build the realtime audio loop | Lower — Twilio handles STT+TTS |
| Voice quality control | Full (our Gemini/Google stack) | Twilio-managed |
| Vendor lock-in | Lower | Higher |
| Best when | Volume is high / cost-sensitive | POC speed matters more than per-minute cost |

**Decision (2026-06-25): ConversationRelay for the POC** — it ships fastest for
the **2026-07-07 demo** (Twilio does STT+TTS; our backend just exchanges text and
reuses `handle_turn`). Revisit Media Streams + Google as a cost optimization only
if call volume grows enough that the ~$0.03–0.04/min difference matters.

## 7. Key cost insights

1. **WhatsApp is cheap here.** Reactive AI replies are Meta **service category =
   $0 from Meta**; only Twilio's $0.005/msg applies. ~$8–$20/month at POC volumes.
2. **Email is essentially free** to run (Zendesk-native; only Gemini inference).
3. **Phone is the cost driver.** Voice minutes + speech dominate the bill. The
   build-vs-buy choice (§6) roughly doubles or halves the per-minute cost.
4. **Total POC running cost is low** — tens of dollars/month at the scenarios
   above. Costs scale with **phone minutes** first, WhatsApp messages second.

## 8. Still to confirm before quoting

- ~~Phone number rental for Malaysia~~ — **confirmed in Twilio Console: $4/mo
  local, $25/mo toll-free**. Still confirm any regulatory bundle / local-address
  requirement to actually provision the number.
- **Google STT/TTS/Gemini** exact rates for the chosen voices/models.
- **Any Twilio Media Streams** surcharge in the customer's account/region.
- The customer's **expected monthly volumes** (messages, calls, avg call length).
- One-time / setup: WhatsApp Business verification, number provisioning (usually
  no Meta platform fee, but confirm any Twilio onboarding costs).

---

## Sources

- [Twilio current rates](https://www.twilio.com/en-us/pricing/current-rates)
- [Twilio Programmable Voice pricing (US)](https://www.twilio.com/en-us/voice/pricing/us)
- [Twilio Voice pricing docs](https://www.twilio.com/docs/voice/pricing)
- [Meta — WhatsApp Business Platform pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)
- [WhatsApp Business price — Singapore & Malaysia (SleekFlow)](https://sleekflow.io/en-sg/blog/whatsapp-business-price)
- Google Cloud Speech-to-Text / Text-to-Speech pricing (confirm on Google Cloud pricing pages)
