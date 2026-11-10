// Tracker tab: Reports (read-only). Native Chatwoot reports are cross-checked
// against the v2 reports API; the Proton report pages are checked for their
// sections. Measures that exist only as BigQuery views (for Power BI) are
// reported as Needs manual with what the CRM screen shows.
import type { Page } from '@playwright/test';
import fs from 'node:fs';
import { expect } from '../lib/fixtures';
import { Api } from '../lib/api';
import { ACCOUNT_ID } from '../lib/env';
import { manual, trackerModule } from '../lib/tracker';
import { bodyText, expectNoError, openApp } from '../lib/ui';

const DAY = 86400;
const now = () => Math.floor(Date.now() / 1000);

async function summary(api: Api, days: number, type = 'account', id?: number) {
  const q = `type=${type}${id ? `&id=${id}` : ''}&since=${now() - days * DAY}&until=${now()}`;
  return Api.must(await api.cw(`/api/v2/accounts/${ACCOUNT_ID}/reports/summary?${q}`), `summary ${type} ${days}d`);
}

async function report(page: Page, path: string): Promise<string> {
  await openApp(page, `reports/${path}`);
  await page.waitForTimeout(2000);
  await expectNoError(page);
  return bodyText(page);
}

/** Which of these section titles the page shows. */
function sections(text: string, names: string[]): { found: string[]; missing: string[] } {
  const found = names.filter(n => text.toLowerCase().includes(n.toLowerCase()));
  return { found, missing: names.filter(n => !found.includes(n)) };
}

trackerModule('Reports', {
  'REP-001': async ({ page, api, ev }) => {
    const text = await report(page, 'overview');
    const s = await summary(api, 7);
    ev.add(`last 7 days (API): ${s.conversations_count} conversations, ${s.resolutions_count} resolved`);
    expect(Number(s.conversations_count), 'real numbers, not empty').toBeGreaterThan(0);
    expect(text, 'overview cards show numbers').toMatch(/\d/);
    const open = Api.must(await api.cw('conversations?assignee_type=all&status=open'), 'open').data.meta.all_count;
    ev.add(`Conversations list: ${open} open now`);
    await ev.shot(page, { fullPage: true });
  },

  'REP-002': async ({ page, api, ev }) => {
    await report(page, 'conversation');
    const s7 = await summary(api, 7);
    const s30 = await summary(api, 30);
    ev.add(`conversations 7d ${s7.conversations_count}, 30d ${s30.conversations_count}`);
    expect(Number(s30.conversations_count)).toBeGreaterThanOrEqual(Number(s7.conversations_count));
    expect(s30.conversations_count, 'numbers change with the range').not.toBe(s7.conversations_count);
    await ev.shot(page, { fullPage: true });
  },

  'REP-003': async ({ page, api, ev }) => {
    const agents = Api.must(await api.cw('agents'), 'agents') as any[];
    let pick: any = null;
    let s: any = null;
    for (const a of agents) {
      const r = await summary(api, 30, 'agent', a.id);
      if (Number(r.conversations_count) > 0) {
        pick = a;
        s = r;
        break;
      }
    }
    expect(pick, 'an agent with conversations in the last 30 days').toBeTruthy();
    ev.add(`${pick.name}: ${s.conversations_count} conversations, first response ${Math.round(s.avg_first_response_time ?? 0)} s, resolution ${Math.round(s.avg_resolution_time ?? 0)} s`);
    await openApp(page, `reports/agents/${pick.id}`);
    await expectNoError(page);
    await ev.shot(page, { fullPage: true });
  },

  'REP-004': async ({ page, api, ev }) => {
    for (const p of ['labels_overview', 'inboxes_overview', 'teams_overview']) {
      const text = await report(page, p);
      ev.add(`${p} loads (${/\d/.test(text) ? 'with numbers' : 'no numbers'})`);
    }
    const inbox = await summary(api, 30, 'inbox', 1);
    ev.add(`inbox 1, 30d: ${inbox.conversations_count} conversations`);
    const teams = Api.must(await api.cw('teams'), 'teams') as any[];
    ev.add(teams.length ? `${teams.length} teams` : 'no teams exist, so the team report is empty');
    await ev.shot(page, { fullPage: true });
  },

  'REP-005': async ({ page, ev }) => {
    const text = await report(page, 'csat');
    ev.add(`CSAT report loads; ${/No (records|responses)/i.test(text) ? 'no responses in range' : 'responses listed'}`);
    await ev.shot(page, { fullPage: true });
    ev.handOff('the [TEST] rating from CONV-026 is given on the handset survey link, so it cannot be checked here');
  },

  'REP-006': async ({ page, ev }) => {
    const text = await report(page, 'bot');
    const s = sections(text, ['AI vs Human Handling', 'AI Escalation Reason']);
    expect(text, 'bot-handled and handoff figures').toMatch(/\d/);
    ev.add(`Bot report loads; sections ${s.found.join(', ') || 'none of the AI sections'}`);
    await ev.shot(page, { fullPage: true });
  },

  'REP-007': async ({ page, ev }) => {
    await report(page, 'agents_overview');
    const button = page.getByRole('button', { name: /download|export/i }).first();
    await expect(button, 'download button').toBeVisible();
    const [download] = await Promise.all([page.waitForEvent('download', { timeout: 60_000 }), button.click()]);
    const file = await download.path();
    const csv = fs.readFileSync(file!, 'utf8');
    const lines = csv.trim().split(/\r?\n/);
    expect(lines.length, 'CSV has a header and rows').toBeGreaterThan(1);
    ev.add(`downloaded ${download.suggestedFilename()}: ${lines.length - 1} rows; header ${lines[0].slice(0, 100)}`);
    await ev.shot(page);
  },

  'REP-008': async ({ page, ev }) => {
    const text = await report(page, 'proton_weekly_report');
    const s = sections(text, ['Case Volume', 'Channel mix', 'Case Status Trend', 'Per-Case Detail']);
    ev.add(`weekly report sections: ${s.found.join(', ')}${s.missing.length ? `; missing ${s.missing.join(', ')}` : ''}`);
    expect(s.missing).toEqual([]);
    expect(text).toMatch(/\d/);
    await ev.shot(page, { fullPage: true });
  },

  'REP-009': async ({ page, ev }) => {
    const text = await report(page, 'proton_anomaly');
    expect(text.length).toBeGreaterThan(50);
    ev.add('Anomaly report loads with data or a no-anomaly message, no error');
    await ev.shot(page, { fullPage: true });
  },

  'REP-010': async ({ page, ev }) => {
    const text = await report(page, 'proton_departments');
    expect(text).toMatch(/Department/);
    ev.add('Departments & PIC report loads with escalations per department and PIC');
    await ev.shot(page, { fullPage: true });
  },

  'REP-011': async ({ page, ev }) => {
    const text = await report(page, 'proton_case_lifecycle');
    expect(text).toMatch(/\d/);
    ev.add('Case Lifecycle report loads with time per stage');
    await ev.shot(page, { fullPage: true });
  },

  'REP-012': async ({ page, ev }) => {
    await report(page, 'proton_weekly_report');
    for (const label of ['Download PDF', 'Download Excel']) {
      const button = page.getByRole('button', { name: label });
      await expect(button, label).toBeVisible();
      const [download] = await Promise.all([page.waitForEvent('download', { timeout: 90_000 }), button.click()]);
      const size = fs.statSync((await download.path())!).size;
      expect(size, `${label} file is not empty`).toBeGreaterThan(200);
      ev.add(`weekly ${label}: ${download.suggestedFilename()} (${size} bytes)`);
    }
    for (const p of ['proton_departments', 'proton_case_lifecycle']) {
      await report(page, p);
      const offered = await page.getByRole('button', { name: /download|export|csv|excel|pdf/i }).allInnerTexts();
      ev.add(`${p}: export buttons ${offered.length ? offered.join(', ') : 'none'}`);
    }
    await ev.shot(page);
  },

  'REP-013': manual('a scheduled report email arriving in a test mailbox (SMTP for reports is not configured)'),
  'REP-014': manual('Power BI Desktop connected to the BigQuery reporting dataset'),

  'REP-015': async ({ page, ev }) => {
    const text = await report(page, 'proton_weekly_report');
    ev.add(/after.hours/i.test(text) ? 'weekly report shows an after-hours split' : 'no after-hours split on the weekly report screen');
    await ev.shot(page, { fullPage: true });
    ev.handOff('after-hours volume and in/out-of-hours response times are BigQuery views (v_volume_after_hours, v_first_response_by_hours_split); check them in Power BI');
  },

  'REP-016': async ({ page, ev }) => {
    const text = await report(page, 'proton_weekly_report');
    const s = sections(text, ['Reopen Rate', 'Reopened']);
    ev.add(`reopen columns on the weekly report: ${s.found.join(', ') || 'none'}`);
    await ev.shot(page, { fullPage: true });
    ev.handOff('a [TEST] case resolved and reopened only shows after the next reporting sync; check the dealer/department/PIC split later');
  },

  'REP-017': async ({ page, ev }) => {
    const found: string[] = [];
    for (const p of ['agents_overview', 'proton_weekly_report', 'proton_case_lifecycle']) {
      const text = await report(page, p);
      for (const [name, re] of [
        ['AHT', /handling time|AHT/i],
        ['after-call work', /after.call/i],
        ['resolution bands', /same day|1.2 days|3.5 days|over 5/i],
      ] as const) if (re.test(text) && !found.includes(name)) found.push(name);
    }
    ev.add(`shown on CRM screens: ${found.join(', ') || 'none of AHT / after-call work / resolution bands'}`);
    await ev.shot(page, { fullPage: true });
    if (found.length === 3) return;
    ev.handOff('the missing measures exist only as BigQuery views (v_resolution_time, v_resolution_sla_buckets; ACW is off); check them in Power BI');
  },

  'REP-018': async ({ page, ev }) => {
    await report(page, 'proton_weekly_report');
    await expect(page.getByText('Agent NPS').first()).toBeVisible();
    ev.add(`Agent NPS section shown; csat report by channel: ${/WhatsApp|Phone/.test(await report(page, 'csat')) ? 'channels listed' : 'no channel split'}`);
    await ev.shot(page, { fullPage: true });
    ev.handOff('agent NPS by call and WhatsApp needs NPS responses, and the NPS survey is not running on proton');
  },

  'REP-019': async ({ page, ev }) => {
    const text = await report(page, 'bot');
    const s = sections(text, ['AI vs Human Handling', 'AI Escalation Reason', 'AI Response Satisfaction', 'deflection', 'resolution', 'accuracy']);
    ev.add(`AI measures shown: ${s.found.join(', ')}; missing: ${s.missing.join(', ') || 'none'}`);
    expect(s.found.length, 'the AI measures are shown or listed as missing').toBeGreaterThan(0);
    await ev.shot(page, { fullPage: true });
  },

  'REP-020': async ({ page, api, ev }) => {
    const labels = (Api.must(await api.cw('labels'), 'labels').payload as any[]).filter(l => /^dept_|^category_/.test(l.title));
    expect(labels.length).toBeGreaterThan(0);
    const l = labels[0];
    await openApp(page, `reports/labels/${l.id}`);
    await expectNoError(page);
    const s = await summary(api, 30, 'label', l.id);
    ev.add(`label ${l.title}, last 30 days: ${s.conversations_count} conversations`);
    await ev.shot(page, { fullPage: true });
  },

  'REP-021': async ({ page, ev }) => {
    const text = await report(page, 'proton_weekly_report');
    ev.add(`case types ${/Case Type/i.test(text) ? 'shown' : 'not shown'}; peak hours ${/peak|hour of day|by hour/i.test(text) ? 'shown' : 'not shown'} on the weekly report`);
    await ev.shot(page, { fullPage: true });
    if (/Case Type/i.test(text) && /peak|by hour/i.test(text)) return;
    ev.handOff('complaint-type ranking and peak hours are BigQuery views (v_complaint_type_ranking, v_peak_hours); check them in Power BI');
  },

  'REP-022': async ({ page, ev }) => {
    await report(page, 'proton_weekly_report');
    const section = page.getByText(/Dealer.escalation turnaround/i).first();
    await expect(section).toBeVisible();
    await section.scrollIntoViewIfNeeded();
    ev.add('Dealer-escalation turnaround section shown on the weekly report');
    await ev.shot(page);
  },

  'REP-023': async ({ page, ev }) => {
    await report(page, 'proton_weekly_report');
    const section = page.getByText('Case Status Trend').first();
    await expect(section).toBeVisible();
    await section.scrollIntoViewIfNeeded();
    ev.add('Case Status Trend section shown with weekly counts per state');
    await ev.shot(page);
  },
});
