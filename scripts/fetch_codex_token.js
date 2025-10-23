// Fetch the CodeX token from https://check.duckcoding.com/
// Uses Playwright (Node). Prints token to stdout, nothing else.

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

  // 1) Default Chromium
  try {
    return await tryLaunch({ headless: true, args });
  } catch (_) {}
  // 2) Edge channel
  try {
    return await tryLaunch({ headless: true, channel: 'msedge', args });
  } catch (e) {
    throw e;
  }
}

async function main() {
  const url = process.env.DUCKCODING_CHECK_URL || 'https://check.duckcoding.com/';
  let browser, ctx, page;
  try {
    ({ browser, ctx, page } = await openPageWithFallback(url));

    // Wait for any benefit cards to render
    await page.locator('xpath=//h3[contains(normalize-space(), "专用福利")]').first().waitFor({ state: 'visible', timeout: 30000 });

    // Iterate all 显示令牌 buttons, click, then collect all visible tokens and map by nearest heading
    const buttons = page.getByRole('button', { name: '显示令牌' });
    const count = await buttons.count();
    for (let i = 0; i < count; i++) {
      const btn = buttons.nth(i);
      try {
        await btn.click({ timeout: 8000 });
      } catch (_) {}
    }
    // Now scan all token text nodes and attribute to nearest preceding heading
    const map = {};
    const tokenNodes = page.locator('text=/sk-[A-Za-z0-9]+/');
    const tcount = await tokenNodes.count();
    for (let j = 0; j < tcount; j++) {
      const el = tokenNodes.nth(j);
      const titleNode = el.locator('xpath=(preceding::h3)[last()]');
      let title = '';
      try { title = (await titleNode.innerText({ timeout: 2000 }))?.trim() || ''; } catch (_) {}
      const txt = (await el.innerText().catch(() => '')) || '';
      const m = txt.match(/sk-[A-Za-z0-9]+/);
      if (m && title) map[title] = m[0];
    }
    
    if (map['CodeX 专用福利']) {
      console.log(map['CodeX 专用福利']);
      await browser.close();
      return;
    }
    throw new Error('CodeX token not found');
  } finally {
    try {
      if (browser) await browser.close();
    } catch (_) {}
  }
}

main().catch((err) => {
  console.error(String((err && err.message) || err));
  process.exit(1);
});
