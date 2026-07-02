# Multi-Tenant on One Shared Zendesk Instance (Proton + Wah Chan + Bank Jago)

**Context:** Only one Zendesk instance (`devoteam-95614`) is available — a new
trial can't be registered because Zendesk limits one email to one instance. So
**all customer bots share this single tenant**, kept apart by a per-customer
**tag lane**. This guide is the repeatable recipe: each customer (Proton, Wah
Chan, Bank Jago, and any future client) is one tag lane; adding a client = repeat
one column, no existing lane changes.

> **Decision:** Shared instance, tag-based separation. Chosen because a second
> Zendesk instance can't be registered on the same email. Two separate instances
> would be more isolated but isn't an option here.

> **Isolation caveat:** One instance = shared agents, billing, SLAs, and
> data-at-rest. Fine for **trial/POC**. If a client goes to production and needs
> true isolation, move that client to its own Zendesk account — the only code
> change is its `ZENDESK_SUBDOMAIN` + fresh creds in that fork's `.env`.

## Per-customer tag lanes

| Lane setting | Proton | Wah Chan | Bank Jago |
|---|---|---|---|
| `ZENDESK_TICKET_TAG` | `source_proton` | `source_wahchan` | `source_bankjago` |
| `ZENDESK_REQUESTER_DOMAIN` | `proton.devoteam.example` | `wahchan.devoteam.example` | `bankjago.devoteam.example` |
| `ZENDESK_CUSTOMER_NAME_PREFIX` | `Proton AI Customer` | `Wah Chan AI Customer` | `Bank Jago AI Customer` |
| Zendesk Webhook → | Proton backend | Wah Chan backend | Bank Jago backend |
| Zendesk Trigger (tag→webhook) | `source_proton` | `source_wahchan` | `source_bankjago` |
| Zendesk View (agent filter) | `source_proton` | `source_wahchan` | `source_bankjago` |

---

## The separation model

| Layer | Mechanism | What it does | Required? |
|---|---|---|---|
| **Tag** | `zendesk_ticket_tag` applied by the backend | Machine-routable label on every ticket | **Yes** — the backbone |
| **Trigger → Webhook** | Conditional trigger scoped by tag | Sends each ticket's events to the **correct** backend | **Yes** — the actual routing |
| **View** | Saved filter scoped by tag | Lets agents look at one customer's tickets only | Recommended |
| **Brand** | Zendesk *Brand* per customer | Branding / help-center / a visible "Brand" column | **Optional** — cosmetic only |

The **Trigger → Webhook** layer is the one that actually matters: without a
tag-scoped trigger, a shared Zendesk fires webhooks for **every** ticket, so Wah
Chan ticket updates would hit Proton's backend (which won't recognize the
session) and vice versa. **Brands route nothing** — skip them for the POC; add
them later only if a client wants a branded help-center or email sender.

> **Do tickets combine?** Physically yes — all customers' tickets live in one
> ticket list (it's one instance). The **tag** labels each one and the **View**
> filters the list so an agent sees only their customer. Same idea as one shared
> email inbox with per-sender labels + saved folders.

---

## Code support (already implemented)

The Zendesk adapter no longer hardcodes Proton identity. Three env vars drive
per-customer behavior (`platform/config.py`, applied in
`features/chat/adapters/zendesk.py` on all ticket-creation paths):

| Env var | Default | Purpose |
|---|---|---|
| `ZENDESK_REQUESTER_DOMAIN` | `proton.devoteam.example` | Domain for synthesized requester emails (`{session_id}@<domain>`) |
| `ZENDESK_CUSTOMER_NAME_PREFIX` | `Proton AI Customer` | Display name for auto-created end-users |
| `ZENDESK_TICKET_TAG` | *(empty)* | Tag stamped on every ticket; empty = don't tag (dedicated instance) |

When `ZENDESK_TICKET_TAG` is set, every ticket the backend creates
(escalation + per-conversation + rotated) carries that tag, which the
conditional trigger keys off of.

### Per-fork `.env`

Every customer's deployment points at the **same** subdomain and differs only in
its tag lane:

```bash
# Proton fork
ZENDESK_SUBDOMAIN=devoteam-95614        # same instance for everyone
ZENDESK_REQUESTER_DOMAIN=proton.devoteam.example
ZENDESK_CUSTOMER_NAME_PREFIX=Proton AI Customer
ZENDESK_TICKET_TAG=source_proton

# Wah Chan fork
ZENDESK_SUBDOMAIN=devoteam-95614
ZENDESK_REQUESTER_DOMAIN=wahchan.devoteam.example
ZENDESK_CUSTOMER_NAME_PREFIX=Wah Chan AI Customer
ZENDESK_TICKET_TAG=source_wahchan

# Bank Jago fork
ZENDESK_SUBDOMAIN=devoteam-95614
ZENDESK_REQUESTER_DOMAIN=bankjago.devoteam.example
ZENDESK_CUSTOMER_NAME_PREFIX=Bank Jago AI Customer
ZENDESK_TICKET_TAG=source_bankjago
```

> Zendesk tags cannot contain hyphens/spaces (they're normalized to
> underscores). Use `source_proton` / `source_wahchan` / `source_bankjago`.

---

## Zendesk admin setup — one tag lane per customer

All steps are in **Admin Center** (`https://devoteam-95614.zendesk.com/admin`).
Do steps 1–3 **once per customer**. (Brands/Groups are optional and skipped.)

### 1. Create the customer's View (so agents see only that customer)

`Admin Center → Workspaces → Views → Add view`

- View "Wah Chan tickets": conditions → *Tags · contains at least one of ·
  `source_wahchan`*

### 2. Create the routing Webhook

`Admin Center → Apps and integrations → Webhooks → Create webhook`

| Webhook | Endpoint URL | Auth |
|---|---|---|
| `wahchan-support-sync` | `https://<wahchan-backend>/webhooks/zendesk-support` | Bearer/secret = that fork's `ZENDESK_SUPPORT_WEBHOOK_SECRET` |

Repeat for whichever webhook routes the fork uses
(`/webhooks/zendesk-support`, `/webhooks/zendesk-handback`,
`/webhooks/zendesk-email`).

### 3. Create the tag-scoped Trigger (the critical step)

`Admin Center → Objects and rules → Business rules → Triggers → Add trigger`

**Trigger "Notify Wah Chan backend":**
- Conditions → *Meet ALL*:
  - *Ticket · Tags · Contains at least one of the following · `source_wahchan`*
  - (+ whatever event condition you sync on, e.g. *Comment is · Public* or
    *Ticket · is · Updated*)
- Actions → *Notify active webhook · `wahchan-support-sync`* → JSON body your
  backend expects.

Because each trigger is gated on the customer tag, that customer's events only
ever reach that customer's backend. **This is what makes one shared Zendesk safe
to route to many bots.**

---

## Adding a new customer (e.g. Bank Jago) — the repeatable recipe

1. Pick a tag: `source_bankjago`.
2. Deploy the Bank Jago fork with the four `ZENDESK_*` lane vars above.
3. In Zendesk: add **1 View** + **1 Webhook** + **1 Trigger**, all keyed on
   `source_bankjago`.

That's it. Existing Proton/Wah Chan lanes are untouched — no risk to live
customers when onboarding a new one.

---

## Verification checklist

1. Escalate a Wah Chan conversation → ticket appears tagged `source_wahchan`,
   requester email `...@wahchan.devoteam.example`.
2. Escalate a Proton conversation → tagged `source_proton`, requester
   `...@proton.devoteam.example`. The two do **not** share a tag.
3. Agent replies on the Wah Chan ticket → **only** Wah Chan's backend receives
   the webhook (check logs); other backends log nothing.
4. The "Wah Chan tickets" view shows no Proton/Bank Jago tickets, and vice versa.

---

## What you still cannot separate on one instance

- **Agents / billing / SLAs / data-at-rest** — shared tenant. Group + view
  restrictions soften visibility but are not hard isolation.
- **Sunshine Conversations app** — one `zendesk_app_id`/`key_id`/`secret`. If
  multiple forks use Sunshine messaging, they share one Sunshine app;
  per-customer routing there needs additional care (separate integrations if the
  plan allows).

→ **Now (POC, one email = one instance): all customers share `devoteam-95614`,
separated by tag lanes. Later, if a specific client needs true isolation for
production, move just that client to its own Zendesk account** — the only code
change is its `ZENDESK_SUBDOMAIN` + fresh creds.
