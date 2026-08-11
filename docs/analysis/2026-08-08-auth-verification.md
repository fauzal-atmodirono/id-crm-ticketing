# Authentication verification: MFA, SSO and password policy (§7.3, §7.6)

**Date:** 2026-08-08 (record) / written 2026-08-11
**Requirements:** §7.3 (login control / IAM, MFA), §7.6 (password policy)
**Plan:** `docs/superpowers/plans/2026-08-08-rfp-p13-ops-hardening.md` task 8

---

## The headline, and its limit

> **What was verified:** from the code in this repository, **there is no
> first-party MFA implementation, no SAML SSO implementation, and no password
> expiry policy.** Additionally, this repository's own fork **removes the
> Chatwoot Security settings navigation item** (patch `0032`), and the platform
> runs Chatwoot **Community edition**, where SSO is an enterprise feature.
>
> **What was NOT verified, and this is the point of task 8:** whether the
> Chatwoot v4.15.1 image *itself* offers MFA/2FA to agents, and whether it is
> enabled on the live tenants. **That needs a login to a running instance, and
> no running instance or credentials were reachable from the environment this was
> written in.** §7.3's status therefore moves from "no evidence found" to
> "no first-party implementation, and one specific question left open" — which is
> better, but is not the documented yes-or-no the task asked for.
>
> The remaining question is narrow and cheap to answer: **log in to
> `crm.<ip>.nip.io` as an administrator and look at Profile Settings for a
> two-factor / authenticator option.** One person, five minutes. It is recorded
> as owed.

## 1. Method

Search of the whole repository on 2026-08-11 for `saml`, `sso`, `mfa`,
`two_factor`, `2fa`, `totp`, `otp_secret` and `password_expir`, case-insensitive,
across `.py`, `.vue`, `.js`, `.patch`, `.env` and `.yml` in `backend/`, `agent/`
and `deploy/`. Plus a read of the SSO design spec, the fork's patch series, and
the compose files.

**This is a code and configuration audit, not a live test.** Everything below is
sourced to a file. Nothing is sourced to a login.

## 2. MFA (§7.3)

| Finding | Evidence |
|---|---|
| No first-party MFA implementation anywhere in `backend/`, `agent/` or the 58 fork patches. | The only search hits for "2FA" are two comments in `deploy/tenants/*.env` about Gmail needing 2FA for an SMTP App Password — unrelated to CRM user login. |
| Authentication is Chatwoot's own, unmodified. | The `authz` layer in `backend/` (`features/authz/identity.py`) *validates* a Chatwoot session by calling Chatwoot, and layers roles and permissions on top. It does not authenticate users, so it neither adds nor could add a second factor. |
| **Open:** whether Chatwoot v4.15.1 ships agent 2FA, and whether it is on. | Chatwoot is consumed as an upstream Docker image (`chatwoot/chatwoot:v4.15.1`) and its source is **not in this checkout** — no `crm/` directory exists despite README §2's layout claiming one. So this cannot be settled by reading code here. |

**"MFA is available in the product and was never enabled" would be a
configuration finding and a better outcome than a build** — but it must not be
asserted without the login, because the opposite ("MFA is not available in
Community edition") is equally plausible and the difference is a licence.

## 3. SAML SSO (§7.3)

| Finding | Evidence |
|---|---|
| **Not implemented.** | A design spec exists (`docs/superpowers/specs/2026-08-02-native-saml-sso-security-design.md`) with a three-phase implementation plan. Zero of the three phases is built: no migration, no `saml_settings` model, no omniauth routes, no Security-page fork patch. Grep finds no `saml` in any first-party source file. |
| The design's own phase 2 says it "supersedes patch `0032`" and "un-hides the nav". Patch `0032` is still in the series, unmodified. | `deploy/chatwoot-fork/patches/0032-hide-security-settings-nav.patch`, present in the shipped patch set. |
| **This platform runs Chatwoot Community edition**, and SSO is a Chatwoot *enterprise* feature. | Patch `0008-strip-enterprise-cruft.patch` exists precisely because "self-hosted Community has no `/enterprise` limits endpoint", and it removes the call so it stops 404-ing. No enterprise licence key appears in any compose or env file. |

**So there are two independent reasons SSO is unavailable today**: nothing was
built, and the edition in use would not provide it out of the box either. The
design spec's approach — implement it natively in the Rails fork rather than use
the enterprise feature — is the right one given the edition, and is unstarted.

## 4. The Security settings page is hidden by our own fork

Worth calling out separately because it changes what an operator sees.

`0032-hide-security-settings-nav.patch` deletes the `Settings Security` item from
the Chatwoot sidebar:

```diff
-        {
-          name: 'Settings Security',
-          label: t('SIDEBAR.SECURITY'),
-          icon: 'i-lucide-shield',
-          to: accountScopedRoute('security_settings_index'),
-        },
```

It removes the **navigation item only** — the route is untouched, so the page
remains reachable by typing its URL. The patch exists because in Community
edition that page is empty or non-functional, so it was dead navigation. But the
consequence for §7.3 is concrete: **an administrator looking for
security/authentication settings in this CRM will not find a menu item for
them.** Anyone verifying MFA/SSO in the UI needs to know that before concluding
"there is no such setting".

## 5. Password policy (§7.6)

| Sub-requirement | Status | Evidence |
|---|---|---|
| Self-service password change | **Available**, via Chatwoot's own Profile Settings. Unmodified by any fork patch. | No patch touches the profile/password views. |
| "Save on the desktop machine" | Browser function, not an application feature. | Nothing to build. |
| **90-day forced change** | **Not implemented.** No expiry logic, no `PASSWORD_EXPIRY_DAYS` setting, no `password_changed_at` handling. | Grep for `password_expir` returns nothing outside the plan documents that ask for it. |

### `PASSWORD_EXPIRY_DAYS` was not implemented, and why

Task 8 says "implement 90-day expiry if absent (`PASSWORD_EXPIRY_DAYS`, default
0 = off)". It is absent, and it was **not** built here. The reason is where it
would have to live rather than reluctance:

Password expiry belongs in the **Rails authentication path** — a
`password_changed_at` column, a Devise/Warden check on sign-in, and a
forced-change screen. That is a fork patch against upstream-owned Rails files
(`app/models/user.rb`, the sessions controller, a migration), and:

1. This sandbox **cannot reach github.com**, so no Chatwoot checkout exists to
   write those hunks against. The house workaround — reconstructing a pre-image
   by replaying our own patches — only works for files *our* patches created.
   `user.rb` and the sessions controller are upstream-owned and out of reach.
2. A fork patch touching the **login path** is the highest-risk patch in the
   series by consequence: get it wrong and nobody can log in, on every tenant at
   once. It should not be hand-authored blind, and it must be verified on a
   non-prod VM before production — which does not exist yet (see
   `docs/runbooks/environments.md`).
3. The task's own verification step is "attempt a login with an expired
   password", which needs a running instance. Shipping the patch without it would
   be shipping the exact thing this programme keeps having to correct: an
   unverifiable claim.

**Recommendation:** do not hand-author this patch. Do it as part of the SAML SSO
work, which already has to touch the same authentication path and already has a
design; the two share the migration and the Security settings page. Until then
§7.6 is **partially met** — self-service change yes, 90-day expiry no — and
should be described that way.

## 6. What is owed, precisely

| Owed | Who can do it | Effort |
|---|---|---|
| Log in to a live tenant as administrator; record whether a two-factor option exists in Profile Settings, and whether it is enabled for any user | anyone with CRM admin access | 5 minutes |
| Confirm the Chatwoot edition in use has no enterprise licence key installed | same | 2 minutes |
| Decide whether §7.6's 90-day expiry is required for sign-off, given self-service change already works | commercial | — |
| If required: implement expiry inside the SAML SSO work, verified on non-prod | engineering | with SSO |

Recorded in `docs/analysis/2026-08-09-blocked-work-register.md`.

## 7. How to describe §7.3 and §7.6 to the client

Short, and true:

- **§7.3** — username/password login via Chatwoot, with role-based access control
  and an audit log added by this platform. **SAML SSO is designed and not built.**
  **MFA is not implemented by us**; whether the underlying Chatwoot version
  offers it has not yet been checked on a running instance. The Security settings
  navigation is currently hidden by our fork.
- **§7.6** — self-service password change works today. **There is no 90-day
  forced-change policy.**

Do not describe either as met.
