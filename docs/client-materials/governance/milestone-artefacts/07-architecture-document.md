# 07 — Architecture document

**Requirement:** §2.3.4, §6.3.2 · **Status: NOT READY TO SIGN**

> The document is complete and current. **The review its own definition of done
> requires — by someone who has not worked on this repository — has not
> happened.** Sign it after that review, not before.

## Traceability

| Evidence | Path |
|---|---|
| **The document** | `docs/client-materials/handover/architecture.md` |
| API surface | `docs/client-materials/handover/api-schema.md` |
| Configuration surface | `docs/client-materials/handover/configuration.md` |
| Engineer-facing description | `CLAUDE.md` |
| Multi-tenancy design | `docs/superpowers/specs/2026-07-16-per-tenant-isolation-design.md` |
| Shared infrastructure | `deploy/docker-compose.infra.yml` |
| Per-tenant stack | `deploy/docker-compose.tenant.yml` |
| Fork patches | `deploy/chatwoot-fork/patches/` (59 files) |

## What it covers

One diagram plus commentary on Caddy, the forked Chatwoot SPA, Rails, Sidekiq,
the `agent` and `backend` services, Postgres, Firestore, BigQuery, Vertex, Twilio
and Gemini — and, the part a handover reader actually wants, **the trust
boundaries and the data that crosses them.** §3.3 traces one customer phone number
end to end in seven steps and ends where it has to: **nothing strips it.**

§4 states that multi-tenant isolation here is **logical, not physical** —
containers, databases, datasets and hostnames are per tenant; compute, memory,
disk and availability are not. §5 states the single-VM reality in its own section,
with a table of six events that each take every tenant down.

## Why it cannot yet be signed

The definition of done requires review by someone outside the project, **with
their questions becoming revisions** — precisely because the author of an
architecture document cannot tell which parts are comprehensible only to the
author. Marking that done would have been the easiest false claim in this package.

**The most useful reviewer is PROTON's own security or infrastructure reviewer**,
and §3 (trust boundaries) and §5 (the single VM) are the sections to put in front
of them first. Expect §3.4 to generate questions: the summary PII protection is a
prompt to a language model, not a control, and anyone with persona-edit access can
weaken it without touching code (`../risk-register.md` R15).

**Also missing and recorded in the document's own §6**, so the omissions do not
read as simplifications: no capacity or sizing model, and no load test has ever
been run.
