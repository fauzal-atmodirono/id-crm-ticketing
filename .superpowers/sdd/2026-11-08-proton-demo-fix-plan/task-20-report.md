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
