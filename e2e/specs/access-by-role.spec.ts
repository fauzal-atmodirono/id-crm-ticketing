// Tracker tab: Access by role. Only the admin view can be automated; the
// supervisor and agent views need their own logins.
import { expect, test } from '../lib/fixtures';
import { appUrl } from '../lib/env';
import { manual, trackerModule } from '../lib/tracker';
import { ERROR_TEXT, bodyText, openApp } from '../lib/ui';

trackerModule('Access by role', {
  'RBAC-001': async ({ page, ev }) => {
    test.setTimeout(12 * 60_000);
    await openApp(page, 'dashboard');
    const prefix = appUrl('');
    const hrefs = await page.locator('aside a[href]').evaluateAll((as: Element[]) => as.map(a => (a as HTMLAnchorElement).href));
    // One page per module: skip the per-inbox/label/team/folder list views.
    const perItem = /^(inbox|label|team|custom_view)\/|^contacts\/(labels|segments)\//;
    const pages = [...new Set(hrefs.filter(h => h.startsWith(prefix)).map(h => h.split('?')[0]))].filter(h => !perItem.test(h.slice(prefix.length)));
    expect(pages.length, 'sidebar items found').toBeGreaterThan(5);
    const bad: string[] = [];
    for (const url of pages) {
      await openApp(page, url.slice(prefix.length));
      const text = await bodyText(page);
      const err = text.match(ERROR_TEXT);
      if (err || !page.url().startsWith(prefix)) bad.push(`${url.slice(prefix.length)} (${err?.[0] ?? `went to ${page.url()}`})`);
    }
    ev.add(`${pages.length} sidebar pages opened as admin; with an error: ${bad.length ? bad.join(', ') : 'none'}`);
    expect(bad).toEqual([]);
    await ev.shot(page);
  },

  'RBAC-002': manual('a supervisor login'),
  'RBAC-003': manual('an agent login'),
  'RBAC-004': manual('an agent login pasting the admin-only URLs'),
});
