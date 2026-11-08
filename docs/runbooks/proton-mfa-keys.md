# Runbook: enable MFA on proton (FIX-25)

Turns on Chatwoot's built-in two-factor login (RFP 2026_028 7.3). MFA stays optional per user; enforcing it for admins is a separate Wave 3 policy decision.

## Read first: the keys are permanent

The script generates three ActiveRecord encryption keys. Existing plaintext credentials stay readable (`support_unencrypted_data`), and a credential is encrypted only when it is next saved. Once any save has happened, removing or changing the keys breaks email IMAP/SMTP, Telegram, Twilio, social channel tokens, integration hooks and MFA, not just MFA. The script refuses to overwrite keys that are already set. Do not roll back after users enrol or credentials are re-saved.

## When

After the Wave 1 deploy is done and stable, and after this commit's change to `deploy/docker-compose.tenant.yml` (three `ACTIVE_RECORD_ENCRYPTION_*` lines in the `x-chatwoot-env` block) has been applied to `/opt/platform/deploy/docker-compose.tenant.yml` on the VM. Apply it as a diff with `patch --dry-run` first, never by copying the file. Without those lines the keys reach `tenants/proton.env` but not Rails.

## Steps (on the VM, in /opt/platform/deploy)

1. Preview: `bash ../scripts/ops/proton_mfa_keys.sh --dry-run`
2. Run: `bash ../scripts/ops/proton_mfa_keys.sh`. It generates the keys inside `proton-chatwoot-rails`, backs up to `tenants/proton.env.bak-mfa-20261110`, writes them without printing them.
3. MANDATORY: copy the three values (`ACTIVE_RECORD_ENCRYPTION_PRIMARY_KEY`, `..._DETERMINISTIC_KEY`, `..._KEY_DERIVATION_SALT`) from `tenants/proton.env` to the team secret store: `<secret store / vault path: user to fill in>`, owner `<name: user to fill in>`. Do not paste them in chat or tickets. Do not continue until this is done: losing them locks out enrolled users and breaks stored channel credentials.
4. Recreate: `docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env up -d --force-recreate chatwoot-rails chatwoot-sidekiq`

## Smoke check

- `docker exec proton-chatwoot-rails bundle exec rails runner 'puts Chatwoot.mfa_enabled?'` prints `true`.
- Logged in, `GET /api/v1/profile/mfa` returns `feature_available: true`, or the Profile settings page shows the Two-factor authentication card.

## Enrol a Devoteam test user

1. Log in as the test user, open Profile settings, then Two-factor authentication.
2. Scan the QR code in an authenticator app, enter the 6-digit code, save the backup codes.
3. Log out and log in again: the app asks for the code after the password. A wrong code is rejected; a backup code works once.
4. Check a user without MFA still logs in normally.

Locked-out user: `rails mfa:reset[email]` in the rails container.
