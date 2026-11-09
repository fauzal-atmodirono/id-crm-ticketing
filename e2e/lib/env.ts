// Run-wide constants for the Proton CRM end-to-end suite.
//
// Everything that names the live tenant lives here so a run against another
// tenant (or a staging copy) is one env var away. Nothing in this file is a
// secret: the suite never sees a password, TOTP code or basic-auth credential,
// because the person running it signs in by hand in the headed window.
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const E2E_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const RESULTS_DIR = path.join(E2E_ROOT, 'results');
export const SCREENSHOT_DIR = path.join(RESULTS_DIR, 'screenshots');

export const BASE_URL = (process.env.E2E_BASE_URL ?? 'https://proton.crm.34-50-103-151.nip.io').replace(/\/$/, '');
export const CONSOLE_URL = process.env.E2E_CONSOLE_URL ?? `${BASE_URL}/apps/test-console/`;
export const ACCOUNT_ID = Number(process.env.E2E_ACCOUNT_ID ?? 1);

// The persistent browser profile lives OUTSIDE the repo so the CRM session
// cookie survives between runs without ever being committed.
export const PROFILE_DIR = process.env.E2E_PROFILE_DIR ?? path.join(os.homedir(), '.cache', 'proton-e2e-profile');
export const CDP_PORT = Number(process.env.E2E_CDP_PORT ?? 9339);

export const LOGIN_WAIT_MS = 15 * 60_000;
export const CONSOLE_WAIT_MS = 10 * 60_000;

// Known live facts (9 Nov 2026).
export const INBOX = { api: 1, web: 2, whatsapp: 3, email: 5 } as const;
export const CALL_ARCHIVE_CONVERSATION = Number(process.env.E2E_CALL_CONVERSATION ?? 214);
export const DEMO_PERSONAS = [
  { id: 'ahmad', name: 'Ahmad', phone: '+60000000101' },
  { id: 'nurul', name: 'Nurul', phone: '+60000000102' },
  { id: 'lim', name: 'Lim', phone: '+60000000103' },
  { id: 'farid', name: 'Farid', phone: '+60000000104' },
  { id: 'new', name: 'New caller', phone: '+60000000105' },
] as const;

export const FLAGS = {
  // Off by default: Seed/Reset on the test console's Demo tab rewrites the
  // [DEMO] contacts. Opt in with `npm run e2e -- --allow-demo-reset`.
  allowDemoReset: process.env.E2E_ALLOW_DEMO_RESET === '1',
  // Off by default: TAX-002/003 add and retire a [TEST] node in the LIVE
  // taxonomy agents pick from. Opt in with `--allow-taxonomy-write`.
  allowTaxonomyWrite: process.env.E2E_ALLOW_TAXONOMY_WRITE === '1',
};

// Every record the run creates carries this tag in its name, key or slug so
// cleanup (per worker and again in global teardown) can find exactly the
// run's own records and nothing else.
export const RUN_ID = process.env.E2E_RUN_ID ?? `r${Date.now().toString(36).slice(-5)}`;
export const TEST_PREFIX = `[TEST] E2E ${RUN_ID}`;

export function appUrl(p: string): string {
  return `${BASE_URL}/app/accounts/${ACCOUNT_ID}/${p.replace(/^\//, '')}`;
}
