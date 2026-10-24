/* Knowledge page UI regression: real server, read-only data, no writes. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const baseUrl = process.env.LN_UI_URL || 'http://127.0.0.1:8792';
const out = path.resolve(process.env.LN_UI_OUT || 'tmp/ui-qa-knowledge');
fs.mkdirSync(out, { recursive: true });

const results = [], errors = [];
async function check(name, fn) {
  await fn();
  results.push({ name, passed: true });
  console.log('PASS', name);
}

(async () => {
  const browser = await chromium.launch({
    channel: process.env.PLAYWRIGHT_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined),
    headless: true,
  });
  const context = await browser.newContext({ viewport: { width: 1180, height: 820 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  const writes = [];
  page.on('request', (r) => {
    if (r.method() !== 'GET' && !/\/api\/ask$/.test(new URL(r.url()).pathname)) writes.push(r.method() + ' ' + r.url());
  });

  const nav = async (id) => {
    if (await page.locator('#nav-toggle').isVisible() && await page.locator('#nav-toggle').getAttribute('aria-expanded') !== 'true') {
      await page.locator('#nav-toggle').click();
    }
    await page.locator('#ira-tab-' + id).click();
    await page.locator('#ira-panel-' + id).waitFor({ state: 'visible' });
  };

  try {
    await page.goto(baseUrl, { waitUntil: 'load' });
    await page.evaluate(() => document.fonts.ready);

    await check('knowledge tab exists in 进阶 group', async () => {
      await nav('knowledge');
      assert.match(await page.locator('#ira-tab-knowledge').innerText(), /知识库/);
      assert.equal(await page.locator('h1:visible').innerText(), '知识库');
    });

    await check('stats come from the API, not hardcoded', async () => {
      const api = await (await context.request.get(baseUrl + '/api/knowledge')).json();
      const nums = await page.locator('[data-kb-stats] .ira-num').allTextContents();
      assert.equal(nums.length, 6);
      assert.equal(nums[0], String(api.totals.intents));
      assert.equal(nums[1], api.totals.phrases.toLocaleString('zh-CN'));
      assert.equal(nums[3], String(api.totals.canonical));
      assert.match(await page.locator('#kb-stamp').innerText(), /^v\d+\.\d+\.\d+/);
    });

    await check('graph draws intents, fields and outputs', async () => {
      const intents = await page.locator('[data-kb-graph] .kb-node[aria-label*="条短语"]').count();
      const fields = await page.locator('[data-kb-graph] .kb-node[aria-label*="个字段"]').count();
      const outs = await page.locator('[data-kb-graph] .kb-node[aria-label*="播报方式"]').count();
      assert.equal(intents, 15);
      assert.equal(fields, 8);
      assert.equal(outs, 3);
      assert.ok(await page.locator('[data-kb-graph] .kb-edge').count() > 20);
    });

    await check('selecting an intent highlights its edges only', async () => {
      await page.locator('[data-kb-graph] .kb-node[aria-label*="轮胎"]').first().click();
      const on = await page.locator('[data-kb-graph] .kb-edge:not(.off)').count();
      const off = await page.locator('[data-kb-graph] .kb-edge.off').count();
      assert.ok(on >= 2 && off > on, `on=${on} off=${off}`);
      assert.match(await page.locator('#kb-detail h2').innerText(), /轮胎/);
      assert.match(await page.locator('#kb-detail').innerText(), /273 条短语/);
    });

    await check('matrix mode keeps the selected intent', async () => {
      await page.locator('#kb-mode-matrix').click();
      await page.locator('#kb-view-matrix').waitFor({ state: 'visible' });
      assert.equal(await page.locator('#kb-view-graph').isHidden(), true);
      assert.equal(await page.locator('[data-kb-matrix] tr').count(), 16); // header + 15
      assert.equal(await page.locator('.kb-matrix th.kb-rowhead button.on').count(), 1);
      assert.match(await page.locator('#kb-detail h2').innerText(), /轮胎/);
    });

    await check('matrix cell opens that bucket in the detail', async () => {
      const cell = page.locator('.kb-cell[data-kb-cell="query_tyre|对象/目标"]');
      await cell.click();
      assert.match(await page.locator('#kb-detail h2').innerText(), /对象\/目标/);
      assert.equal(await cell.evaluate((e) => e.classList.contains('sel')), true);
    });

    await check('filter narrows both views', async () => {
      await page.locator('#kb-filter').fill('胎');
      await page.waitForTimeout(120);
      const rows = await page.locator('.kb-matrix tbody tr').count();
      assert.equal(rows, 1);
      await page.locator('#kb-filter').fill('');
      await page.waitForTimeout(120);
      assert.equal(await page.locator('.kb-matrix tbody tr').count(), 15);
    });

    await check('Escape clears the selection', async () => {
      await page.locator('.kb-matrix th.kb-rowhead button').first().click();
      await page.locator('#ira-panel-knowledge').press('Escape');
      assert.match(await page.locator('#kb-detail h2').innerText(), /先选一个意图/);
    });

    await check('graph mode still works after switching back', async () => {
      await page.locator('#kb-mode-graph').click();
      await page.locator('#kb-view-graph').waitFor({ state: 'visible' });
      assert.equal(await page.locator('[data-kb-graph] .kb-node[aria-label*="条短语"]').count(), 15);
    });

    await check('conflicts are reported as rule problems, separate from data gaps', async () => {
      const api = await (await context.request.get(baseUrl + '/api/knowledge')).json();
      assert.ok(api.conflicts.length >= 1, 'expected at least one rule conflict');
      // 「现在几档」已在 2026-10-20 修好（词库补原句 + 拼音保留拉丁字母），
      // 这里断言它不再被误报；仍在的规则问题是「胎压多少」缺对象。
      assert.equal(api.conflicts.some((c) => c.q === '现在几档'), false,
        '现在几档 should no longer be flagged after the matcher fix');
      const tyre = api.conflicts.find((c) => c.q === '胎压多少');
      assert.ok(tyre, '胎压多少 should still be flagged as missing object');
      assert.equal(tyre.kind, '缺对象');
      assert.ok(api.data_gaps > 0, 'data gaps should be counted separately');
      const text = await page.locator('#kb-detail').innerText();
      assert.match(text, /可疑样本|先选一个意图/);
    });

    await check('read-only: no POST requests while browsing the page', async () => {
      assert.deepEqual(writes.filter((w) => !w.includes('/api/ask')), []);
    });

    await check('layout fits 1440 / 1180 / 900 with no horizontal overflow', async () => {
      for (const [w, h] of [[1440, 900], [1180, 820], [900, 650]]) {
        await page.setViewportSize({ width: w, height: h });
        await page.waitForTimeout(150);
        for (const mode of ['graph', 'matrix']) {
          await page.locator('#kb-mode-' + mode).click();
          await page.waitForTimeout(120);
          const m = await page.evaluate(() => ({
            doc: document.documentElement.scrollWidth,
            inner: innerWidth,
            pane: document.querySelector('.ira-main').scrollWidth,
            paneW: document.querySelector('.ira-main').clientWidth,
          }));
          assert.ok(m.doc <= m.inner + 1, `${mode} ${w}: document overflow`);
          assert.ok(m.pane <= m.paneW + 1, `${mode} ${w}: main pane overflow ${m.pane} > ${m.paneW}`);
          await page.screenshot({ path: path.join(out, `kb-${mode}-${w}.png`) });
        }
      }
    });

    if (errors.length) throw new Error('browser errors:\n' + errors.join('\n'));
    fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify({ passed: true, cases: results }, null, 2));
    console.log(`DONE ${results.length} checks passed`);
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error('FAIL', e.message);
  if (errors.length) console.error(errors.join('\n'));
  process.exit(1);
});
