// Query full quota details for a given DuckCoding token using the public check page UI.
// Usage: node scripts/query_details_from_site.js <token>
// Prints JSON: { name, total_yen, used_yen, used_percent, remaining_yen }

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

function parseMoneyToNumber(text) {
  if (!text) return 0;
  const m = String(text).replace(/[\s,]/g, '').match(/[-+]?\d+(?:\.\d+)?/);
  return m ? parseFloat(m[0]) : 0;
}

function parsePercentToNumber(text) {
  if (!text) return 0;
  const m = String(text).replace(/[\s,]/g, '').match(/([-+]?\d+(?:\.\d+)?)%/);
  return m ? parseFloat(m[1]) : 0;
}

async function textFor(page, label) {
  // Prefer following-sibling then relaxed following
  let loc = page.locator(`xpath=(//*[normalize-space()="${label}"]/following-sibling::*[1])[1]`);
  try { await loc.waitFor({ state: 'visible', timeout: 6000 }); return (await loc.innerText()) || ''; } catch (_) {}
  loc = page.locator(`xpath=(//*[normalize-space()="${label}"]/following::*[1])[1]`);
  await loc.waitFor({ state: 'visible', timeout: 6000 });
  return (await loc.innerText()) || '';
}

async function main() {
  const tokenArg = process.argv[2] || process.env.DUCKCODING_TOKEN || '';
  if (!tokenArg || !/^sk-[A-Za-z0-9]+$/.test(tokenArg)) {
    throw new Error('Missing or invalid token. Pass as argv[2] or DUCKCODING_TOKEN env.');
  }
  const url = process.env.DUCKCODING_CHECK_URL || 'https://check.duckcoding.com/';

  let browser, ctx, page;
  try {
    ({ browser, ctx, page } = await openPageWithFallback(url));

    await page.getByRole('textbox', { name: '请输入您的令牌 (如: sk-xxx...)' }).fill(tokenArg);
    await page.getByRole('button', { name: '查询额度' }).click();

    // Wait for response or success banner
    try {
      await Promise.race([
        page.waitForResponse((r) => r.url().includes('/api/usage/token') && r.status() === 200, { timeout: 30000 }),
        page.getByText('查询成功').first().waitFor({ state: 'visible', timeout: 30000 })
      ]);
    } catch (_) {}

    const nameText = await textFor(page, '令牌名称').catch(() => '') || '';
    const totalText = await textFor(page, '总额度').catch(() => '') || '';
    const usedText = await textFor(page, '已使用').catch(() => '') || '';
    const remainText = await textFor(page, '剩余额度').catch(() => '') || '';

    const out = {
      name: nameText.trim(),
      total_yen: parseMoneyToNumber(totalText),
      used_yen: parseMoneyToNumber(usedText),
      used_percent: parsePercentToNumber(usedText) || parsePercentToNumber(await textFor(page, '使用进度').catch(() => '')),
      remaining_yen: parseMoneyToNumber(remainText),
    };

    console.log(JSON.stringify(out, null, 0));
  } finally {
    try { if (browser) await browser.close(); } catch (_) {}
  }
}

main().catch((err) => {
  console.error(String((err && err.message) || err));
  process.exit(1);
});
