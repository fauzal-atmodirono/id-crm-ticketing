#!/usr/bin/env node
// `npm run e2e [-- <playwright args>] [--allow-demo-reset] [--allow-taxonomy-write]`
//
// Thin wrapper over `playwright test`: our own opt-in flags become env vars
// (Playwright rejects unknown CLI flags); everything else is passed through,
// e.g. `npm run e2e -- --grep CONV` runs one module.
//
// Playwright's --grep is case-insensitive, so a bare "CONV" would also match
// every title containing "conversation". A bare module prefix (letters and
// digits only, like CONV, VOICE, C360) is therefore narrowed to row IDs:
// "CONV" -> "CONV-(\d|X\d)". Anything else is passed through untouched.
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const own = { '--allow-demo-reset': 'E2E_ALLOW_DEMO_RESET', '--allow-taxonomy-write': 'E2E_ALLOW_TAXONOMY_WRITE' };
const env = { ...process.env };
const pass = [];
const args = process.argv.slice(2);
for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (own[a]) env[own[a]] = '1';
  else if ((a === '--grep' || a === '-g') && /^[A-Za-z0-9]+$/.test(args[i + 1] ?? '')) {
    pass.push(a, `\\b${args[i + 1]}-(\\d|X\\d)`);
    i += 1;
  } else pass.push(a);
}
const bin = path.join(root, 'node_modules', '.bin', 'playwright');
const r = spawnSync(bin, ['test', ...pass], { cwd: root, env, stdio: 'inherit' });
process.exit(r.status ?? 1);
