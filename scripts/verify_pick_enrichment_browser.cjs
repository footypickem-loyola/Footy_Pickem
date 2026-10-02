/* Synthetic cached enrichment only; run against review_pick_insight.py. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
(async () => {
  const out = process.env.PR16_BROWSER_OUTPUT;
  if (!out) throw new Error('Set PR16_BROWSER_OUTPUT');
  fs.mkdirSync(out, {recursive:true});
  const browser = await chromium.launch({channel:'msedge', headless:true});
  const context = await browser.newContext();
  const page = await context.newPage();
  const calls = [], errors = [];
  page.on('request', r => {if (/api\.sportmonks|api\.football-data/.test(r.url())) calls.push(r.url());});
  page.on('pageerror', e => errors.push(e.message));
  // Test image rendering and error fallback without fetching any provider asset.
  let broken = false;
  await page.route('https://cdn.sportmonks.com/**', r => r.fulfill(broken ? {status:404, body:''}
    : {status:200, contentType:'image/svg+xml', body:'<svg xmlns="http://www.w3.org/2000/svg" width="72" height="80"><path fill="#c22" d="M4 4h64v46L36 76 4 50z"/></svg>'}));
  const base = 'http://127.0.0.1:5115';
  let checks = 0;
  for (const [width,height] of [[1440,1000],[390,844],[360,640],[844,390]]) {
    await page.setViewportSize({width,height});
    for (const [scenario,brokenImage] of [[5,false],[5,true],[6,false]]) {
      broken = brokenImage;
      assert.equal((await context.request.post(`${base}/__review/scenario/${scenario}`)).status(),200);
      await page.goto(`${base}/tab/current?season=year-2&force_week=2`);
      await page.waitForFunction(() => !!window.htmx);
      await page.getByRole('button',{name:'Pick Arsenal against Leeds United',exact:true}).click();
      await page.getByRole('button',{name:'Confirm Pick',exact:true}).click();
      const dialog = page.locator('#pick-insight-dialog');
      await dialog.waitFor({state:'visible'});
      const band = dialog.locator('.insight-band').last();
      assert.equal(await band.innerText(), scenario === 5
        ? 'TOP SCORER\nFirst Synthetic Scorer / Second Synthetic Scorer · 5 goals each'
        : 'TOP SCORER\n—');
      assert.match(await dialog.innerText(), /5W 1D 1L/);
      if (scenario === 5 && broken) {
        await page.waitForFunction(() => document.querySelector('.insight-crest')?.hidden);
        assert.equal(await dialog.locator('.insight-crest').isVisible(), false);
      } else if (scenario === 5) {
        await page.waitForFunction(() => document.querySelector('.insight-crest')?.naturalWidth > 0);
        assert.equal(await dialog.locator('.insight-crest').isVisible(),true);
      } else assert.equal(await dialog.locator('.insight-crest').count(),0);
      assert.equal(await dialog.locator('.insight-hero h3').innerText(),'ARSENAL');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      assert.equal(await dialog.evaluate(el => el.scrollWidth > el.clientWidth), false);
      await band.scrollIntoViewIfNeeded();
      assert.equal(await band.isVisible(),true);
      await page.screenshot({path:path.join(out,`enrichment-${scenario}-${broken}-${width}.png`)});
      await page.keyboard.press('Escape');
      checks++;
    }
  }
  assert.deepEqual(calls,[]);
  assert.deepEqual(errors,[]);
  await browser.close();
  console.log(`PASS: ${checks} cached/stale layout checks; ties, broken-crest fallback, unchanged official record, no provider API requests`);
})().catch(e => {console.error(e);process.exit(1);});
