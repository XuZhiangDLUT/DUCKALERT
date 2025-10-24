// Fetch DuckCoding status services and their 24h availability from
// https://status.duckcoding.com/status/duckcoding using Playwright.
// Prints JSON array: [{ name: string, percent_24h: number }]

const { chromium } = require('playwright');

async function openPageWithFallback(url) {
  const proxy = process.env.HTTPS_PROXY || process.env.HTTP_PROXY || '';
  const args = [];
  if (proxy) args.push(`--proxy-server=${proxy}`);
  const ua = process.env.PLAYWRIGHT_UA ||
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 Edg/124.0';

  async function tryLaunch(options) {
    const browser = await chromium.launch(options);
    const ctx = await browser.newContext({ ignoreHTTPSErrors: true, userAgent: ua, locale: 'zh-CN' });
    const page = await ctx.newPage();
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    return { browser, ctx, page };
  }

  try { return await tryLaunch({ headless: true, args }); } catch (_) {}
  try { return await tryLaunch({ headless: true, channel: 'msedge', args }); } catch (e) { throw e; }
}

function parsePercent(text) {
  if (!text) return null;
  const m = String(text).replace(/[\s,]/g, '').match(/(\d+(?:\.\d+)?)%/);
  return m ? parseFloat(m[1]) : null;
}

async function main() {
  const url = process.env.DC_STATUS_URL || 'https://status.duckcoding.com/status/duckcoding';
  let browser, page;
  try {
    ({ browser, page } = await openPageWithFallback(url));
    // Wait root heading
    try { await page.getByRole('heading', { name: /DuckCoding|服务|Status|服务状态/ }).first().waitFor({ timeout: 15000 }); } catch (_) {}

    // Fetch official monitor names from the status page API (same origin)
    const names = await page.evaluate(async () => {
      try {
        const res = await fetch('/api/status-page/duckcoding', { credentials: 'omit' });
        const j = await res.json();
        const groups = j && j.publicGroupList || [];
        const list = [];
        for (const g of groups) {
          const ms = (g && g.monitorList) || [];
          for (const m of ms) if (m && m.name) list.push(String(m.name));
        }
        return list;
      } catch (e) {
        return [];
      }
    });

    const results = [];
    for (const name of names) {
      let percent = null;
      // Try exact text match first
      try {
        const loc = page.getByText(name, { exact: true }).first();
        await loc.waitFor({ state: 'visible', timeout: 6000 });
        // Robust: start from the name element, prefer searching previous siblings first (same card header)
        const pct = await loc.evaluate((node) => {
          function getText(el){ return (el && el.textContent || '').replace(/[\s,]/g,''); }
          function findPctIn(el){ if(!el) return null; const t=getText(el); const m=t.match(/(\d+(?:\.\d+)?)%/); if(m) return parseFloat(m[1]); if(el.childNodes) for(const c of el.childNodes){ const r=findPctIn(c); if(r!=null) return r; } return null; }
          // Climb up: on each level, check previousElementSibling first, then the ancestor itself
          let cur = node;
          for (let i=0; i<8 && cur; i++) {
            const prevPct = findPctIn(cur.previousElementSibling);
            if (prevPct != null) return prevPct;
            cur = cur.parentElement;
          }
          // Fallback to scanning ancestors
          cur = node.parentElement;
          for (let i=0; i<6 && cur; i++, cur=cur.parentElement){
            const pct = findPctIn(cur);
            if (pct != null) return pct;
          }
          return null;
        });
        if (pct != null) percent = pct;
      } catch (_) {}
      // Fallback: contains() search when exact not found
      if (percent == null) {
        try {
          const loc2 = page.locator(`xpath=//*[contains(normalize-space(), ${JSON.stringify(name)})]`).first();
          await loc2.waitFor({ state: 'visible', timeout: 5000 });
          const pct2 = await loc2.evaluate((node) => {
            function getText(el){ return (el && el.textContent || '').replace(/[\s,]/g,''); }
            function findPctIn(el){ if(!el) return null; const t=getText(el); const m=t.match(/(\d+(?:\.\d+)?)%/); if(m) return parseFloat(m[1]); if(el.childNodes) for(const c of el.childNodes){ const r=findPctIn(c); if(r!=null) return r; } return null; }
            let cur = node;
            for (let i=0; i<8 && cur; i++) {
              const prevPct = findPctIn(cur.previousElementSibling);
              if (prevPct != null) return prevPct;
              cur = cur.parentElement;
            }
            cur = node.parentElement;
            for (let i=0; i<6 && cur; i++, cur=cur.parentElement){
              const pct = findPctIn(cur);
              if (pct != null) return pct;
            }
            return null;
          });
          if (pct2 != null) percent = pct2;
        } catch (_) {}
      }
      if (percent != null) results.push({ name, percent_24h: percent });
    }

    console.log(JSON.stringify(results, null, 0));
  } finally {
    try { if (browser) await browser.close(); } catch (_) {}
  }
}

main().catch(err => {
  console.error(String((err && err.message) || err));
  process.exit(1);
});