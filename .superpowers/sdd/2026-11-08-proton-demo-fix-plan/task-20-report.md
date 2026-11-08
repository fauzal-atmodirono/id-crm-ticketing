# Task 20 report: Idle timeout (FIX-18)

## Design decisions
- Brief said "token lifespan from SESSION_IDLE_TIMEOUT_MINUTES, default 30". Rejected: devise_token_auth expiry is fixed at issue (Chatwoot does not rotate tokens per request), so a 30 min lifespan would be an ABSOLUTE cap and log active agents out every 30 min. Split into:
  - Server: `SESSION_TOKEN_LIFESPAN_HOURS` (unset = upstream 2 months, unchanged) via new initializer `config/initializers/zz_proton_session.rb` (zz_ loads after upstream's devise_token_auth initializer). Applies to logins after the change only.
  - Client: `SESSION_IDLE_TIMEOUT_MINUTES` (default 0 = off, so nothing changes until the user sets it). SPA signs out (DELETE /auth/sign_out best effort, remove `cw_d_session_info` cookie, go to /app/login). Activity is shared across tabs via localStorage.
- Config reaches the SPA via a new additions-only `<script>` hunk in vueapp.html.erb (context from 0001/0058 output) setting `window.__PROTON_IDLE_TIMEOUT_MINUTES__`.
- Hook: appended import+call to useProtonConfig.js (fork-created, only touched by 0001, widely imported) instead of editing an upstream SPA file whose context I cannot see.
- Dockerfile stage 2 only copies public/vite and the layout, so a Rails initializer would NOT reach the image; added a COPY line for it.
- Server-side true idle timeout (Devise timeoutable) not done: needs patching upstream models; no upstream tree.

## Built
patches/0079-session-idle-timeout.patch (new files idleMonitor.js, useProtonIdleTimeout.js, zz_proton_session.rb; additive hunks in useProtonConfig.js and vueapp.html.erb); Dockerfile COPY; docker-compose.tenant.yml x-chatwoot-env (2 vars); tenants/example.env.

## Tests
- tests/test_session_idle_timeout.py: 6 passed (applies patch to fixture, content asserts, env plumbing, node-run idle monitor logic: off/fires once/touch resets/cross-tab/blocked storage). ruby -c only if ruby installed.
- check_patch_edit_context.py 0fe46fc9: 0 hits. simulate_patch_chain.sh for useProtonConfig.js, idleMonitor.js, zz_proton_session.rb: ok.
- Brief's "Rails request spec expired_token_returns_401" NOT written: no upstream Rails tree/spec harness.

## Concerns
- Image not built; vite/Rails build unverified. Layout hunk verified only against synthetic post-0058 context (real build will confirm).
- Assumes upstream names: cookie `cw_d_session_info`, `dashboard/api/auth` default export with hasAuthCookie/getAuthData (the latter two used by patch 0030), `js-cookie` dependency, `/app/login` route.
- Enabling: set SESSION_IDLE_TIMEOUT_MINUTES in tenants/proton.env (user runs the script; stale compose trap: VM compose file must have the new x-chatwoot-env lines) and rebuild image rc17+.

## Fix report (review findings)
Scope statement for RFP 7.5 sign-off: this is a presentation-level, client-enforced idle timeout (SPA signs the user out); SESSION_TOKEN_LIFESPAN_HOURS is the server-side absolute backstop, not an idle timer. Do not describe it as server-enforced idle expiry.

Changes:
1. Reload bypass: createIdleMonitor reads the stored stamp before touching; stale (>= timeout) fires onIdle immediately. The stamp is removed on fire so the login page/next session does not re-fire.
2. Calls: new `hold` option (polled; throwing = not held). While held, check() touches, so the full window restarts after release. useProtonIdleTimeout passes `window.__PROTON_IDLE_HOLD__`; patch 0069 (useProtonSoftphone.js, additive lines, hunk count 165 -> 175) publishes it true during 'in-call'/'ringing'.
3. signOut: AbortController 3 s abort; clearTimeout, cookie removal and redirect in finally.
Minor: appended import moved to top of useProtonConfig.js (only 0001 creates it, no later patch touches it; simulate chain ok).

Covering tests (node cases inside test_idle_monitor_logout_behaviour, plus test_signout_has_timeout_and_finally, test_softphone_publishes_idle_hold): stale stamp fires at install; fresh does not; held -> no logout, released -> restart; throwing hold.
Commands/output:
- `rtk proxy python3 -m pytest test_session_idle_timeout.py` (in deploy/chatwoot-fork/tests): 8 passed
- `python3 tests/check_patch_edit_context.py 0fe46fc9`: 0 hit(s)
- simulate_patch_chain.sh useProtonSoftphone.js / ProtonSoftphonePanel / useProtonConfig.js: ok
