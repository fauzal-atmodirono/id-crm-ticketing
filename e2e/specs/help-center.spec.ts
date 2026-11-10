// Tracker tab: Help Center. One [TEST] portal per run (slug e2e-<run id>),
// deleted at the end of HELP-003 and again by the final sweep.
import { expect } from '../lib/fixtures';
import { Api } from '../lib/api';
import { BASE_URL, RUN_ID, TEST_PREFIX } from '../lib/env';
import { trackerModule } from '../lib/tracker';
import { bodyText } from '../lib/ui';

const SLUG = `e2e-${RUN_ID}`;
const ARTICLE = `${TEST_PREFIX} How to book a service`;

async function ensurePortal(api: Api): Promise<void> {
  if ((await api.cw(`portals/${SLUG}`)).ok) return;
  Api.must(
    await api.cw('portals', { method: 'POST', body: { portal: { name: `${TEST_PREFIX} Portal`, slug: SLUG, config: { allowed_locales: ['en'], default_locale: 'en' } } } }),
    'create portal',
  );
}

async function ensureArticle(api: Api): Promise<void> {
  const list = await api.cw(`portals/${SLUG}/articles?locale=en`);
  if ((list.data?.payload ?? []).some((a: any) => a.title === ARTICLE)) return;
  const cat = Api.must(
    await api.cw(`portals/${SLUG}/categories`, { method: 'POST', body: { category: { name: `${TEST_PREFIX} Service`, slug: `e2e-service-${RUN_ID}`, locale: 'en' } } }),
    'create category',
  );
  const me = await api.me();
  Api.must(
    await api.cw(`portals/${SLUG}/articles`, {
      method: 'POST',
      body: {
        article: {
          title: ARTICLE,
          content: `Book a service online or call your dealer. Test article ${RUN_ID}.`,
          category_id: (cat.payload ?? cat).id,
          author_id: me.id,
          locale: 'en',
          status: 'published',
        },
      },
    }),
    'create article',
  );
}

trackerModule('Help Center', {
  'HELP-001': async ({ page, api, ev }) => {
    await ensurePortal(api);
    const res = await page.goto(`${BASE_URL}/hc/${SLUG}/en`);
    expect(res?.status(), 'public portal URL').toBe(200);
    ev.add(`[TEST] portal saved; public URL /hc/${SLUG}/en loads (200)`);
    await ev.shot(page);
  },

  'HELP-002': async ({ page, api, ev }) => {
    await ensurePortal(api);
    await ensureArticle(api);
    await page.goto(`${BASE_URL}/hc/${SLUG}/en`);
    await expect.poll(async () => (await bodyText(page)).includes(ARTICLE) || (await page.reload(), false), { timeout: 30_000 }).toBeTruthy();
    ev.add('category and published article show on the public portal');
    await ev.shot(page);
  },

  'HELP-003': async ({ page, api, ev }) => {
    await ensurePortal(api);
    await ensureArticle(api);
    try {
      const r = await page.request.get(`${BASE_URL}/hc/${SLUG}/en/articles.json?query=${encodeURIComponent('How to book')}`);
      expect(r.status()).toBe(200);
      const json = await r.json();
      const hits = (json.payload ?? json) as any[];
      expect(hits.some(a => a.title === ARTICLE), 'search finds the [TEST] article').toBeTruthy();
      ev.add(`public search "How to book": ${hits.length} hit(s) incl. the [TEST] article`);
      await page.goto(`${BASE_URL}/hc/${SLUG}/en`);
      await ev.shot(page);
    } finally {
      await api.cw(`portals/${SLUG}`, { method: 'DELETE' });
      ev.add('test portal deleted');
    }
  },
});
