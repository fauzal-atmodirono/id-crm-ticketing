// In-browser API access for assertions and [TEST] fixtures.
//
// Every call runs INSIDE a CRM tab (page.evaluate), using the session the
// person signed in with: the Chatwoot devise triplet from the
// `cw_d_session_info` cookie, and for the Proton backend the same headers the
// SPA's adminRequest sends (window.__PROTON_CONFIG__ + that triplet). The
// tokens never leave the browser: only {status, data, ms} come back to Node,
// so nothing secret can end up in a log, a report or the sheet.
import type { Page } from '@playwright/test';
import { ACCOUNT_ID, appUrl } from './env';

export interface ApiResult<T = any> {
  status: number;
  ok: boolean;
  ms: number;
  data: T;
}

interface CallArgs {
  target: 'cw' | 'be';
  path: string;
  method: string;
  body?: unknown;
  form?: { field: string; filename: string; content: string; type: string; extra?: Record<string, string> };
}

export class Api {
  private meCache: { id: number; name: string } | null = null;

  constructor(readonly page: Page) {}

  /** Park the API tab on a CRM page so cookies and __PROTON_CONFIG__ exist. */
  async ready(): Promise<void> {
    if (!this.page.url().startsWith(appUrl(''))) {
      await this.page.goto(appUrl('dashboard'), { waitUntil: 'domcontentloaded' });
    }
    await this.page.waitForFunction(() => !!(window as any).__PROTON_CONFIG__, null, { timeout: 30_000 });
  }

  /** Chatwoot REST. Relative paths are account-scoped (/api/v1/accounts/<id>/...). */
  cw<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<ApiResult<T>> {
    const full = path.startsWith('/') ? path : `/api/v1/accounts/${ACCOUNT_ID}/${path}`;
    return this.call<T>({ target: 'cw', path: full, method: opts.method ?? 'GET', body: opts.body });
  }

  /** Proton backend (PROTON_BACKEND_URL), authenticated like the SPA does it. */
  be<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<ApiResult<T>> {
    return this.call<T>({ target: 'be', path, method: opts.method ?? 'GET', body: opts.body });
  }

  /** Multipart upload of a text file (CSV) to Chatwoot or the backend. */
  upload<T = any>(target: 'cw' | 'be', path: string, form: CallArgs['form']): Promise<ApiResult<T>> {
    const full = target === 'cw' && !path.startsWith('/') ? `/api/v1/accounts/${ACCOUNT_ID}/${path}` : path;
    return this.call<T>({ target, path: full, method: 'POST', form });
  }

  async me(): Promise<{ id: number; name: string }> {
    if (!this.meCache) {
      const r = await this.cw('/api/v1/profile');
      if (!r.ok) throw new Error(`GET /api/v1/profile -> ${r.status}`);
      this.meCache = { id: r.data.id, name: r.data.name };
    }
    return this.meCache;
  }

  /** Throw with the status (never the body's secrets: bodies are API data) on failure. */
  static must<T>(r: ApiResult<T>, what: string): T {
    if (!r.ok) {
      const detail = typeof r.data === 'string' ? r.data : JSON.stringify(r.data);
      throw new Error(`${what} -> HTTP ${r.status}: ${String(detail).slice(0, 200)}`);
    }
    return r.data;
  }

  private async call<T>(args: CallArgs): Promise<ApiResult<T>> {
    await this.ready();
    return this.page.evaluate(async (a: CallArgs) => {
      const raw = document.cookie.split('; ').find(c => c.startsWith('cw_d_session_info='));
      let auth: Record<string, string> = {};
      try {
        auth = raw ? JSON.parse(decodeURIComponent(raw.slice('cw_d_session_info='.length))) : {};
      } catch {
        auth = {};
      }
      const cfg = (window as any).__PROTON_CONFIG__ || {};
      const headers: Record<string, string> = {};
      let url = a.path;
      if (a.target === 'cw') {
        if (auth['access-token']) {
          headers['access-token'] = auth['access-token'];
          headers.client = auth.client;
          headers.uid = auth.uid;
        }
      } else {
        url = `${cfg.backendUrl || ''}${a.path}`;
        if (cfg.backendKey) headers['x-api-key'] = cfg.backendKey;
        if (auth['access-token']) {
          headers['x-chatwoot-access-token'] = auth['access-token'];
          headers['x-chatwoot-client'] = auth.client;
          headers['x-chatwoot-uid'] = auth.uid;
        }
      }
      let body: BodyInit | undefined;
      if (a.form) {
        const fd = new FormData();
        fd.append(a.form.field, new Blob([a.form.content], { type: a.form.type }), a.form.filename);
        for (const [k, v] of Object.entries(a.form.extra || {})) fd.append(k, v);
        body = fd;
      } else if (a.body !== undefined) {
        headers['Content-Type'] = 'application/json';
        body = JSON.stringify(a.body);
      }
      const t0 = performance.now();
      const res = await fetch(url, { method: a.method, headers, body });
      const text = await res.text();
      const ms = Math.round(performance.now() - t0);
      let data: any = text;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = text.slice(0, 2000);
      }
      return { status: res.status, ok: res.ok, ms, data };
    }, args) as Promise<ApiResult<T>>;
  }
}
