# Proton test console (VM)

A Devoteam-only test page for the chatbot, voicebot and browser phone that
talks to the **deployed** proton backend on the VM (the old Cloud Run
`proton-frontend` talks to the old Cloud Run `proton-backend`, so it does not
test what Proton sees).

- URL: `https://proton.crm.34-50-103-151.nip.io/apps/test-console/`
- Login: HTTP basic auth, user `devoteam`. The password is not in the repo;
  ask the person who set it up (it was generated on 9 Nov 2026).
- App: `backend/apps/frontend` built with
  `VITE_API_BASE_URL=/apps/test-console/api npx vite build --base=/apps/test-console/`.
  The router uses `import.meta.env.BASE_URL` (commit `b3f64275`) so deep links
  work under the sub-path.
- Serving: files in `/opt/platform/deploy/caddy/tenants/apps/test-console/` on
  the VM. `proton.caddy` (both `proton.crm` vhosts) has two `handle_path`
  blocks, both behind the same `basic_auth`:
  - `/apps/test-console/api/*` → `reverse_proxy proton-backend:8080` (path
    stripped), so `/chat/turn`, `/voice/turn`, `/voice/tts`,
    `/voice/phone/token` are reachable only with the login. `/chat/*` is still
    not routed publicly.
  - `/apps/test-console/*` → static files with `try_files {path} /index.html`.
  - Backup of the vhost before the change:
    `/backups/proton.caddy.pre-test-console-20261109`.
- Backend settings it relies on (proton.env):
  - `FRONTEND_ORIGINS` must include `https://proton.crm.34-50-103-151.nip.io`,
    or `/voice/phone/token` answers 403 (the Phone tab).
  - `VOICE_PROVIDER=gcp` for real Gemini text-to-speech; with the default
    `mock` the Voice tab plays no speech.
- Not working by design: the Dashboard tab calls `/metrics/dashboard`, which
  needs a Chatwoot session while `DATA_SCOPED_RBAC_ENABLED` is on. Use the CRM
  reports instead.
- Every chat/voice turn uses Gemini (and the Phone tab uses Twilio minutes):
  keep the login private, remove the two Caddy blocks after the engagement.

Rebuild/redeploy: build as above, `tar -C dist -czf test-console-dist.tgz .`,
copy to the VM, replace the folder contents. No Caddy reload is needed for a
content-only update.
