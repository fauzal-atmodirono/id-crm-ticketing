# Runbook: enable MFA on proton (FIX-25)

Turns on Chatwoot's built-in two-factor login (RFP 2026_028 7.3). MFA stays optional per user; enforcing it for admins is a separate Wave 3 policy decision.

## Read first: the keys are permanent

The script generates three ActiveRecord encryption keys. Existing plaintext credentials stay readable (`support_unencrypted_data`), and a credential is encrypted only when it is next saved. Once any save has happened, removing or changing the keys breaks email IMAP/SMTP, Telegram, Twilio, social channel tokens, integration hooks and MFA, not just MFA. The script refuses to overwrite keys that are already set.

`--rollback` (restores the pre-run env backup) is safe only in the window before any user enrols and before any inbox or integration credential is saved. After that, do not roll back.

## When

After the Wave 1 deploy is done and stable, and after the FIX-25 compose diff (three `ACTIVE_RECORD_ENCRYPTION_*` lines in the `x-chatwoot-env` block of `deploy/docker-compose.tenant.yml`) has been applied to `/opt/platform/deploy/docker-compose.tenant.yml` on the VM. Apply it as a diff with `patch --dry-run` first, never by copying the file. Without those lines the keys reach `tenants/proton.env` but not Rails.

## Steps (on the VM)

1. `cd /opt/platform/deploy`
2. Preview: `sudo bash ../scripts/ops/proton_mfa_keys.sh --dry-run`
3. Run: `sudo bash ../scripts/ops/proton_mfa_keys.sh`. It generates the keys inside `proton-chatwoot-rails`, backs up to `tenants/proton.env.bak-mfa-20261110`, and writes them without printing them.
4. MANDATORY: copy the three values (`ACTIVE_RECORD_ENCRYPTION_PRIMARY_KEY`, `..._DETERMINISTIC_KEY`, `..._KEY_DERIVATION_SALT`) to the team secret store: `<secret store / vault path: user to fill in>`, owner `<name: user to fill in>`. Copy without displaying them: open `tenants/proton.env` with `sudoedit tenants/proton.env`, copy each value straight into the vault, then quit the editor without changing anything. Never `cat`, `grep` or `echo` the file, and never paste the values in chat or tickets. Do not continue until this is done: losing them locks out enrolled users and breaks stored channel credentials.
5. Recreate: `sudo docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env up -d --force-recreate chatwoot-rails chatwoot-sidekiq`

## Smoke check

- `sudo docker exec proton-chatwoot-rails bundle exec rails runner 'puts Chatwoot.mfa_enabled?'` prints `true` (defined in upstream `config/application.rb`).
- Logged in, `GET /api/v1/profile/mfa` returns `feature_available: true` (upstream route `profile/mfa`), or the Profile settings page shows the Two-factor authentication card.

## Enrol a Devoteam test user

1. Log in as the test user, open Profile settings, then Two-factor authentication.
2. Scan the QR code in an authenticator app, enter the 6-digit code, save the backup codes.
3. Log out and log in again: the app asks for the code after the password. A wrong code is rejected; a backup code works once.
4. Check a user without MFA still logs in normally.

Locked-out user (upstream `lib/tasks/mfa.rake` defines `mfa:reset`): `sudo docker exec proton-chatwoot-rails bundle exec rails mfa:reset[user@example.com]`
