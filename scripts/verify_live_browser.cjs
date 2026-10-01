/* Synthetic harness only. Requires Playwright via NODE_PATH or local install. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const out = process.env.PR12_BROWSER_OUTPUT || path.join(process.env.TEMP || '/tmp', 'footy-pr12-browser');
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const base = process.env.PR12_REVIEW_URL || 'http://127.0.0.1:58192';
  const checks = [];
  for (const width of [1440, 390, 360]) {
    await page.setViewportSize({ width, height: 1000 });
    for (let scenario = 1; scenario <= 10; scenario++) {
      assert.equal((await context.request.post(`${base}/__review/scenario/${scenario}`)).status(), 200);
      await page.goto(`${base}/tab/current?season=pr12-local&force_week=1`);
      await page.locator('#matchweek-view').waitFor();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
      assert.equal(overflow, false, `${width}px scenario ${scenario}: horizontal overflow`);
      const text = await page.locator('#matchweek-view').innerText();
      assert.match(text, scenario === 1 ? /Matchup set/ : scenario === 10 ? /Final result official/ : /If Scores Hold/);
      assert.equal(await page.locator('.live-timeline').count(), 0);
      assert.doesNotMatch(text, /Matchweek Timeline/);
      if (scenario > 1 && scenario < 10) {
        assert.match(text, /10 Fixtures Remaining/);
        for (const totals of await page.locator('.live-hero .live-player-totals').allTextContents()) {
          assert.match(totals, /For [+-]\d+ · Against [+-]\d+ · Net [+-]\d+/);
        }
        assert.match(await page.locator('.live-owned').first().innerText(), /Robin Defender/);
      }
      if (scenario === 9) assert.match(text, /Live data delayed/);
      if (scenario === 7) assert.match(text, /HT/);
      if (scenario === 8) assert.match(text, /90\+4/);
      if (scenario > 2 && scenario < 10) assert.match(text, /Alex Forward/);
      if ([3, 8, 9, 10].includes(scenario)) await page.screenshot({ path: path.join(out, `${width}-${scenario}.png`), fullPage: true });
      checks.push({ width, scenario, overflow });
    }
  }
  await context.request.post(`${base}/__review/scenario/3`);
  await page.goto(`${base}/tab/current?season=pr12-local&force_week=1`);
  await page.waitForFunction(() => !!window.htmx);
  const before = await page.locator('.live-team-events li').allTextContents();
  assert.ok(before.length > 0);
  await page.locator('.live-rail > .card > details > summary').click();
  await page.evaluate(() => scrollTo(0, 200));
  const scrollBefore = await page.evaluate(() => scrollY);
  // Trigger the same GET without scrolling to the button.
  await page.locator('#matchweek-refresh').evaluate(button => button.click());
  await page.waitForTimeout(800);
  assert.equal(await page.locator('.live-rail > .card > details').getAttribute('open'), '');
  assert.ok(Math.abs((await page.evaluate(() => scrollY)) - scrollBefore) < 10);
  await page.locator('button', { hasText: 'Refresh matchweek' }).click();
  await page.waitForTimeout(800);
  assert.deepEqual(await page.locator('.live-team-events li').allTextContents(), before);
  await page.reload();
  assert.deepEqual(await page.locator('.live-team-events li').allTextContents(), before);
  await page.locator('#navigation-tabs a[data-tab="open"]').click();
  await page.waitForURL('**/tab/open*');
  await page.goBack();
  await page.locator('.live-layout').waitFor();
  assert.deepEqual(await page.locator('.live-team-events li').allTextContents(), before);
  // Prove real scheduled HTMX refresh, including stopping on official Final.
  await context.request.post(`${base}/__review/scenario/10`);
  await page.waitForFunction(() => document.querySelector('#matchweek-view').textContent.includes('Final result official'), null, { timeout: 22000 });
  assert.equal(await page.locator('#matchweek-view').getAttribute('hx-trigger'), null);
  await context.request.post(`${base}/__review/scenario/1`);
  await page.goto(`${base}/tab/current?season=pr12-local&force_week=1`);
  assert.equal(await page.locator('#matchweek-view').getAttribute('hx-trigger'), 'every 60s');
  await page.clock.install();
  // Reprocess after installing the clock so HTMX's timer is controlled.
  await page.reload();
  await context.request.post(`${base}/__review/scenario/2`);
  await page.clock.runFor(61000);
  await page.locator('.live-layout').waitFor();
  assert.deepEqual(errors, []);
  fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify({ checks, errors, refresh: true, history: true, scroll: true, preMatchTransition: true, finalTransition: true }, null, 2));
  console.log(`PASS: ${checks.length} viewport/scenario checks; HTMX refresh, reload, back navigation, scroll, automatic Pre-Match/Live/Final transitions; ${out}`);
  await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
