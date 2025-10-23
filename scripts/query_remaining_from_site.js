// Query remaining Yen for a given DuckCoding token using the public check page.
// Usage: node scripts/query_remaining_from_site.js <token>
// Prints only the numeric remaining value (e.g., 0.48) to stdout.

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

  try {
    return await tryLaunch({ headless: true, args });
  } catch (_) {}
  try {
    return await tryLaunch({ headless: true, channel: 'msedge', args });
  } catch (e) { throw e; }
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

    // Fill token
    await page.getByRole('textbox', { name: '请输入您的令牌 (如: sk-xxx...)' }).fill(tokenArg);
    // Click 查询额度
    await page.getByRole('button', { name: '查询额度' }).click();
    // Wait for API call to finish and/or success banner
    try {
      await Promise.race([
        page.waitForResponse((r) => r.url().includes('/api/usage/token') && r.status() === 200, { timeout: 30000 }),
        page.getByText('查询成功').first().waitFor({ state: 'visible', timeout: 30000 })
      ]);
    } catch (_) {}

    // Wait for result card to appear and read the value next to label '剩余额度'
    // Use following-sibling to avoid walking into unrelated nodes
    let remainBlock = page.locator('xpath=(//*[normalize-space()="剩余额度"]/following-sibling::*[1])[1]');
    try {
      await remainBlock.waitFor({ state: 'visible', timeout: 20000 });
    } catch (_) {
      // Fallback: use relaxed following axis if sibling not matched
      remainBlock = page.locator('xpath=(//*[normalize-space()="剩余额度"]/following::*[1])[1]');
      await remainBlock.waitFor({ state: 'visible', timeout: 20000 });
    }
    const text = (await remainBlock.innerText()) || '';
    const m = text.replace(/[,\s]/g, '').match(/[-+]?\d+(?:\.\d+)?/);
    if (!m) throw new Error('Remaining value not found in UI');
    console.log(m[0]);
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
