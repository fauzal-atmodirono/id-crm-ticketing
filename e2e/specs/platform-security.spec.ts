// Tracker tab: Platform & security. Credentials, GCP console checks and long
// idle waits are manual by design.
import { expect } from '../lib/fixtures';
import { manual, trackerModule } from '../lib/tracker';
import { bodyText, openApp } from '../lib/ui';

trackerModule('Platform & security', {
  'PLAT-001': manual('logging out and in with a test user’s password and receiving the reset email (the suite never types credentials)'),

  'PLAT-002': async ({ page, ev }) => {
    await openApp(page, 'profile/settings');
    const text = await bodyText(page);
    const shown = /two.factor|2FA|authenticator|multi.factor/i.test(text);
    ev.add(`two-factor option on Profile settings: ${shown ? 'visible' : 'not visible'}`);
    await ev.shot(page, { fullPage: true });
    expect(shown, 'two-factor option is visible').toBeTruthy();
    ev.handOff('turning it on needs an authenticator app and a fresh login with the code');
  },

  'PLAT-003': manual('changing a password is a credential action, and expiry needs 3 months'),

  'PLAT-004': async ({ page, ev }) => {
    await openApp(page, 'dashboard');
    const minutes = await page.evaluate(() => (window as any).__PROTON_IDLE_TIMEOUT_MINUTES__ ?? null);
    ev.add(`client idle timeout configured: ${minutes ?? 'not set'} min`);
    expect(Number(minutes), 'an idle timeout is configured').toBeGreaterThan(0);
    await ev.shot(page);
    ev.handOff(`staying idle ${minutes} min would sign this run out; check it by hand at the end`);
  },

  'PLAT-005': async ({ page, api, ev }) => {
    const pii = await api.be('/authz/me/pii');
    ev.add(`signed-in admin PII policy: ${JSON.stringify(pii.data).slice(0, 120)}`);
    await openApp(page, 'contacts');
    await ev.shot(page);
    ev.handOff('masking shows only for a role without pii.view, which needs that user’s login');
  },

  'PLAT-006': manual('a test role scoped to one team and that user’s login'),
  'PLAT-007': manual('retention settings are server config (env and purge jobs), checked on the VM'),
  'PLAT-008': manual('GCP snapshot schedule and a restore to a test instance'),
  'PLAT-009': manual('GCP uptime checks and alert policies in Cloud Monitoring'),
});
