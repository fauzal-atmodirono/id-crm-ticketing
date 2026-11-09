// Tracker tab: Case taxonomy. TAX-002/003 write a [TEST] node to the LIVE
// taxonomy agents pick from, so they only run with --allow-taxonomy-write
// (the node is retired again in the same test and by the final sweep).
import { expect, test } from '../lib/fixtures';
import { Api } from '../lib/api';
import { FLAGS, KEY_PREFIX, TEST_PREFIX } from '../lib/env';
import { trackerModule } from '../lib/tracker';
import { columnHeader, expectNoError, openApp, openConversation, revealInPanel } from '../lib/ui';

type Node = { key: string; label: string; level: number; parent?: string | null; children?: Node[]; department?: string | null };
const walk = (nodes: Node[]): Node[] => nodes.flatMap(n => [n, ...walk(n.children ?? [])]);

async function tree(api: Api): Promise<Node[]> {
  return Api.must(await api.be('/admin/taxonomy/tree'), 'taxonomy tree').tree as Node[];
}

function needWriteFlag(): void {
  if (!FLAGS.allowTaxonomyWrite) {
    test.skip(true, 'Needs manual: adds a node to the live taxonomy agents use (re-run with --allow-taxonomy-write to automate it)');
  }
}

/** A category that already has subcategory children: the new node joins them. */
async function parentForTest(api: Api): Promise<{ parent: Node; level: number }> {
  const all = walk(await tree(api));
  const parent = all.find(n => (n.children ?? []).length > 0 && (n.children ?? []).every(c => !(c.children ?? []).length));
  if (!parent) throw new Error('no category with leaf children');
  return { parent, level: parent.children![0].level };
}

trackerModule('Case taxonomy', {
  'TAX-001': async ({ page, api, ev }) => {
    await openApp(page, 'proton/taxonomy');
    await expect(page.getByRole('heading', { name: 'Case Taxonomy' })).toBeVisible();
    await expectNoError(page);
    // Plain <th> has role "cell" in Playwright, so headers are matched by element.
    for (const col of ['Level', 'Department', 'Key', 'Label']) await expect(columnHeader(page, col), `${col} column`).toBeVisible({ timeout: 30_000 });
    const n = walk(await tree(api)).length;
    ev.add(`tree loads with Level/Department/Key/Label; ${n} active nodes (346 when seeded)`);
    expect(n).toBeGreaterThanOrEqual(300);
    await ev.shot(page);
  },

  'TAX-002': async ({ page, api, ev }) => {
    needWriteFlag();
    const { parent, level } = await parentForTest(api);
    const key = `${KEY_PREFIX}_battery_fault`;
    const label = `${TEST_PREFIX} Battery fault`;
    try {
      Api.must(await api.be('/admin/taxonomy/node', { method: 'POST', body: { level, key, label, parent: parent.key } }), 'add node');
      await openApp(page, 'proton/taxonomy');
      await expect(page.getByText(label).first()).toBeVisible();
      ev.add(`node ${key} saved under ${parent.key}`);
      await ev.shot(page);
      const defs = Api.must(await api.cw('custom_attribute_definitions?attribute_model=0'), 'definitions') as any[];
      const inDropdown = defs.some(d => (d.attribute_values ?? []).includes(label) || (d.attribute_values ?? []).includes(key));
      ev.add(`offered in the conversation taxonomy dropdown: ${inDropdown}`);
      expect(inDropdown, 'new node appears in the conversation taxonomy dropdown').toBeTruthy();
    } finally {
      await api.be(`/admin/taxonomy/node/${key}/retire`, { method: 'POST' });
    }
  },

  'TAX-003': async ({ page, api, ev }) => {
    needWriteFlag();
    const { parent, level } = await parentForTest(api);
    const key = `${KEY_PREFIX}_rename`;
    try {
      Api.must(await api.be('/admin/taxonomy/node', { method: 'POST', body: { level, key, label: `${TEST_PREFIX} Rename me`, parent: parent.key } }), 'add');
      const renamed = `${TEST_PREFIX} Renamed`;
      Api.must(await api.be('/admin/taxonomy/node', { method: 'POST', body: { level, key, label: renamed, parent: parent.key } }), 'rename');
      expect(walk(await tree(api)).find(n => n.key === key)?.label).toBe(renamed);
      await openApp(page, 'proton/taxonomy');
      await expect(page.getByText(renamed).first()).toBeVisible();
      ev.add('rename shows in the tree');
      Api.must(await api.be(`/admin/taxonomy/node/${key}/retire`, { method: 'POST' }), 'retire');
      expect(walk(await tree(api)).some(n => n.key === key), 'retired node no longer offered').toBeFalsy();
      ev.add('retired node left the active tree');
      await ev.shot(page);
    } finally {
      await api.be(`/admin/taxonomy/node/${key}/retire`, { method: 'POST' });
    }
    ev.handOff('that a conversation already using the node keeps its value needs a real case set to it before retiring');
  },

  'TAX-004': async ({ page, api, data, ev }) => {
    const defs = Api.must(await api.cw('custom_attribute_definitions?attribute_model=0'), 'definitions') as any[];
    const counts = ['case_type', 'case_category', 'case_subcategory', 'case_detail'].map(k => {
      const d = defs.find(x => x.attribute_key === k);
      return [k, d?.attribute_values?.length ?? 0] as const;
    });
    ev.add(counts.map(([k, n]) => `${k} ${n} options`).join(', '));
    for (const [k, n] of counts) expect(n, `${k} has options`).toBeGreaterThan(0);
    const conv = await data.conversation([`${TEST_PREFIX} cascade check`]);
    await openConversation(page, conv.id);
    await revealInPanel(page, /case[_ ]?type/i); // taxonomy attributes in Conversation information
    await ev.shot(page);
  },
});

