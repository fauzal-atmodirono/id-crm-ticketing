# Proton CRM end-to-end suite (Playwright)

A headed Playwright run against the live Proton CRM (Chatwoot fork plus the
FastAPI backend and the Vue test console). There is one spec per module tab of
the tracker sheet, and one test per sheet row, titled `<ID> <Test>`. The
results can be written back to the sheet's `Auto result / Auto evidence / Auto
run at` columns (K:M).

## Run it

```bash
cd e2e
npm install          # @playwright/test 1.56.1 uses Chromium build 1194, already in ~/Library/Caches/ms-playwright
npm run e2e          # every module, headed
npm run e2e -- --grep CONV        # one module: a bare prefix becomes the row-ID regex \bCONV-(\d|X\d)
npm run e2e -- --grep "CONV-01[0-9]"   # any other pattern is passed to Playwright as is
```

**You sign in. The suite never does.** It never types or stores a password,
a TOTP code or a basic-auth credential:

1. A Chromium window opens on `https://proton.crm.34-50-103-151.nip.io/app/login`
   and the terminal prints `Waiting for you to log in…`. Sign in, including
   2FA if asked. The suite waits up to 15 minutes.
2. It then opens the test console at `/apps/test-console/`. Get past the
   browser's sign-in prompt there (it waits up to 10 minutes).
3. Leave the window alone. All tests run in that same signed-in browser.

The browser profile lives at `~/.cache/proton-e2e-profile`, outside the repo,
so the CRM session usually carries over to the next run. Basic auth does not
carry over, because the browser only keeps it in memory. Chatwoot signs you out
after 15 idle minutes. Tests navigate all the time, and each test also
refreshes the shared idle stamp, so the session stays alive while the run is
going.

Optional flags, off by default:

| Flag | Effect |
| --- | --- |
| `--allow-demo-reset` | VOICE-X05 presses **Reset** and then **Seed** on the test console's Demo tab. Both rewrite the `[DEMO]` contacts. |
| `--allow-taxonomy-write` | Runs TAX-002 and TAX-003 instead of marking them manual. They add a `[TEST]` node to the live taxonomy and retire it again. |

Environment overrides: `E2E_BASE_URL`, `E2E_CONSOLE_URL`, `E2E_PROFILE_DIR`,
`E2E_CDP_PORT` (default 9339), `E2E_CALL_CONVERSATION` (default 214).

## Results

- `results/html/`: the HTML report (`npx playwright show-report results/html`).
- `results/results.json`: the JSON report. The publisher reads this file.
- `results/screenshots/<ID>.png`: the key screen for each row. Every row gets
  one, including rows that fail.
- Every test records an `evidence` annotation, a one-line summary of what it
  checked.

Writing to the tracker sheet (columns K:M):

```bash
npm run publish                    # dry run: prints every row it would write
npm run publish -- --write         # writes via `gws sheets spreadsheets values batchUpdate`
npm run publish -- --label rc21    # Auto run at = "DD Mon YYYY (Playwright, rc21)"
```

| Playwright outcome | Auto result |
| --- | --- |
| passed | Pass |
| skipped `Needs manual: …` | Needs manual |
| skipped `Blocked: …` | Blocked |
| failed / timed out | Fail (first line of the error) |
| interrupted / not run | (not written) |

The publisher finds each row by its ID in the live column A, never by row
position. Extra tests (`VOICE-X..`) have no sheet row, so they are only
printed.

## How rows are handled

- **Automated**: the test drives the UI against the row's Definition of Done.
  It uses in-browser API calls, made with your own session, for setup and
  cross-checks.
- **Partly automated**: the automatable part runs and must pass. The rest (a
  handset, a mailbox, a second login, an hours-long wait) is reported as
  `Needs manual: <why>. Automated part OK: <evidence>`. These rows are never
  reported as Pass.
- **Manual**: rows that genuinely need something the suite cannot do. They are
  declared with `manual('<why>')` and reported as Needs manual, not Fail.
- **Blocked**: a precondition outside the suite's control is missing. For
  example, there is no Messenger or Instagram inbox (CONV-027), the DMS is not
  connected (C360-006), or outbound Tamil is switched off (AI-006).

A row in the sheet that has no handler fails with "No automation or manual
reason written", so a new row is never skipped silently. After rows change in
the sheet, run `npm run sync-tracker` to refresh `data/tracker.json`.

New features without a row of their own run as extra tests:

| ID | What it checks |
| --- | --- |
| VOICE-005 | The in-note call player on conversation 214. Checks that "▶ Play recording" is shown, that no `gs://` text appears in the bubble or the list, that the audio gets a `src` and `currentTime` advances, and that the transcript opens. |
| VOICE-X01 | The sidebar call-recording player plays. |
| VOICE-X02 / X03 | Test console Chat tab: talking as Ahmad, the reply mentions "Ahmad". Talking as Anonymous, it does not. |
| VOICE-X04 | Test console Phone tab: the "Call as" persona list. It never presses Call. |
| VOICE-X05 | Test console Demo tab: the seed status of each persona. This is read-only unless `--allow-demo-reset` is passed. |

## Production safety (live tenant)

- The suite is read-mostly. Everything it creates is named
  `[TEST] E2E <run id>`, or has a key or slug containing the run id.
  Conversations are created only on the **API inbox (1)** and carry the
  `audit_test` label.
- Each test deletes what it created. A sweep then deletes anything still
  carrying the run id when each worker ends and again in global teardown. It
  can only find the run's own records.
- The suite never sends WhatsApp or email. It never changes escalation
  routing, SLA policies, roles, account or inbox settings, ladder `dry_run`,
  AI modes, lifecycle messages, account alert defaults or any feature flag.
  The only FAQs it uploads are `[TEST]` ones, and it deletes them.
- Two writes affect you personally:
  - WF-002 and WF-003 set your **own** status to Busy, then Break, so no
    conversation is routed to you. They then restore your previous status if
    it can be picked on the My status page. If you started Offline, set it
    back yourself.
  - ALRT-001 changes your **own** "new message" alert. The suite restores it
    afterwards.
- No secret is ever printed or written. API calls run inside the browser, and
  only status codes and data come back to the test.

## Layout

```
playwright.config.ts   workers=1, headed via global setup, JSON + HTML reporters
lib/global-setup.ts    launches the one persistent headed Chromium and waits for your logins
lib/fixtures.ts        connects each worker to that browser over CDP; page, api, data, evidence
lib/tracker.ts         one test per sheet row; manual(), extras
lib/api.ts             in-browser Chatwoot and Proton-backend calls with your session
lib/data.ts            [TEST] conversation and contact helpers and the run-id sweep
lib/ui.ts              selectors and helpers (CRM and test console)
lib/results.ts         report-to-sheet mapping (pure; unit-tested in unit/)
specs/*.spec.ts        one file per tracker tab
scripts/run.mjs        `npm run e2e` wrapper (own flags become env vars)
scripts/publish.ts     writes K:M (dry run by default)
scripts/sync-tracker.py  refreshes data/tracker.json from the sheet (read-only)
```

The browser is launched in global setup, not in a worker. Playwright discards a
worker after every failing test, and a browser owned by a worker would close
with it and take the basic-auth sign-in with it. Workers connect to the shared
browser over CDP (`127.0.0.1:9339`) instead, so a failure costs nothing. Do not
run two copies at the same time.

`npm run test:unit` runs the mapping tests. `npm run e2e:list` lists every test
without opening a browser.
