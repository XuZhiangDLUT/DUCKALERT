// Fetch all benefit tokens (Claude Code, CodeX, Gemini CLI) from https://check.duckcoding.com/
// Prints a JSON object mapping card heading -> token, e.g.
// { "Claude Code 专用福利": "sk-...", "CodeX 专用福利": "sk-...", "Gemini CLI 专用福利": "sk-..." }

const { chromium } = require('playwright');

async function openPageWithFallback(url) {
  const proxy = process.env.HTTPS_PROXY || process.env.HTTP_PROXY || '';
  const args = [];
  if (proxy) args.push(`--proxy-server=${proxy}`);

  const ua = process.env.PLAYWRIGHT_UA || 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 Edg/124.0';

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

function canonicalLabel(title) {
  const t = String(title || '').replace(/\s+/g, '').toLowerCase();
  if (t.includes('codex')) return 'CodeX 专用福利';
  if (t.includes('claudecode') || t.includes('claude')) return 'Claude Code 专用福利';
  if (t.includes('gemini') || t.includes('geminicli')) return 'Gemini CLI 专用福利';
  return '';
}

async function extractTokenFromCard(card) {
  // Click 显示令牌 within the card, then read sk-... from the same card
  try { await card.getByRole('button', { name: '显示令牌' }).click({ timeout: 6000 }); } catch (_) {}
  const tokenNode = card.locator('text=/sk-[A-Za-z0-9]+/');
  try { await tokenNode.first().waitFor({ state: 'visible', timeout: 6000 }); } catch (_) {}
  const txt = (await tokenNode.first().innerText().catch(() => '')) || '';
  const m = txt.match(/sk-[A-Za-z0-9]+/);
  return m ? m[0] : '';
}

async function main() {
  const url = process.env.DUCKCODING_CHECK_URL || 'https://check.duckcoding.com/';
  let browser, ctx, page;
  try {
    ({ browser, ctx, page } = await openPageWithFallback(url));

    // Wait for benefit cards
    await page.locator('xpath=//h3[contains(normalize-space(), "专用福利")]').first().waitFor({ state: 'visible', timeout: 30000 });

    const labels = ['CodeX', 'Claude', 'Gemini'];
    const found = {};

    // Prefer extracting by card container per known label to avoid cross-heading mismatches
    for (const key of labels) {
      const card = page.locator(`xpath=//div[.//h3[contains(., "${key}") and contains(., "专用福利")]]`).first();
      try {
        await card.waitFor({ state: 'visible', timeout: 8000 });
        const title = ((await card.locator('xpath=.//h3').first().innerText().catch(() => '')) || '').trim();
        const canon = canonicalLabel(title) || canonicalLabel(key);
        const token = await extractTokenFromCard(card);
        if (canon && token) found[canon] = token;
      } catch (_) {}
    }

    // Fallback: click all buttons then map by nearest preceding h3 (legacy path)
    if (Object.keys(found).length < 3) {
      const buttons = page.getByRole('button', { name: '显示令牌' });
      const count = await buttons.count();
      for (let i = 0; i < count; i++) {
        try { await buttons.nth(i).click({ timeout: 8000 }); } catch (_) {}
      }
      const tokenNodes = page.locator('text=/sk-[A-Za-z0-9]+/');
      const tcount = await tokenNodes.count();
      for (let j = 0; j < tcount; j++) {
        const el = tokenNodes.nth(j);
        const titleNode = el.locator('xpath=(preceding::h3)[last()]');
        let title = '';
        try { title = (await titleNode.innerText({ timeout: 2000 }))?.trim() || ''; } catch (_) {}
        const txt = (await el.innerText().catch(() => '')) || '';
        const m = txt.match(/sk-[A-Za-z0-9]+/);
        const canon = canonicalLabel(title);
        if (m && canon) found[canon] = m[0];
      }
    }

    console.log(JSON.stringify(found, null, 0));
  } finally {
    try { if (browser) await browser.close(); } catch (_) {}
  }
}

main().catch((err) => {
  console.error(String((err && err.message) || err));
  process.exit(1);
});