/* Run against review_pick_insight.py; never production. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const out = process.env.PR15_BROWSER_OUTPUT;
  if (!out) throw new Error('Set PR15_BROWSER_OUTPUT to a local artifact directory');
  fs.mkdirSync(out, {recursive:true});
  const browser = await chromium.launch({channel:'msedge', headless:true});
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors = [];
  const providers = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (/api\.sportmonks|api\.football-data/.test(request.url())) providers.push(request.url()); });
  const base = 'http://127.0.0.1:5115';
  const view = `${base}/tab/current?season=year-2&force_week=2`;
  const dialog = page.locator('#pick-insight-dialog');
  const checks = [];
  async function setup(scenario) {
    assert.equal((await context.request.post(`${base}/__review/scenario/${scenario}`)).status(), 200);
    await page.goto(view);
    await page.waitForFunction(() => !!window.htmx);
  }
  async function geometry(label) {
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, label);
    assert.equal(await dialog.evaluate(el => el.scrollWidth > el.clientWidth), false, label);
    const rect = await dialog.boundingBox();
    assert.ok(rect.y >= 0 && rect.y + rect.height <= page.viewportSize().height + 1, label);
    assert.equal(await dialog.locator('.insight-band').count(), 6);
    const pitch = await dialog.locator('.insight-pitch').innerText();
    assert.ok(!pitch.includes('Leeds United'));
    const content = dialog.locator('.insight-content');
    await content.evaluate(el => { el.scrollTop = el.scrollHeight; });
    const lastBand = await dialog.locator('.insight-band').last().boundingBox();
    const contentBox = await content.boundingBox();
    assert.ok(lastBand.y + lastBand.height <= contentBox.y + contentBox.height + 1, `${label}: sixth band reachable`);
    await content.evaluate(el => { el.scrollTop = 0; });
    checks.push(label);
  }
  for (const [width, height] of [[1440,1000], [390,844], [360,640], [844,390]]) {
    await page.setViewportSize({width,height});
    await setup(1);
    await page.getByRole('button', {name:'Pick Arsenal against Leeds United', exact:true}).click();
    await page.getByRole('button', {name:'Confirm Pick', exact:true}).click();
    await dialog.waitFor({state:'visible'});
    assert.match(await dialog.innerText(), /WHAT TO LOOK FOR[\s\S]*Your Pick: Arsenal[\s\S]*Home vs Leeds United/);
    assert.match(await dialog.innerText(), /5W 1D 1L/);
    await geometry(`${width}x${height}: manual`);
    await page.screenshot({path:path.join(out, `manual-${width}.png`)});
    // Native dialog keeps keyboard focus contained and Escape returns to the next pick.
    for (let i=0;i<8;i++) await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => !!document.activeElement.closest('#pick-insight-dialog')), true);
    await page.keyboard.press('Escape');
    await dialog.waitFor({state:'hidden'});
    await page.waitForFunction(() => document.activeElement.matches('.mw-pick'));
    const next = page.locator('.mw-pick:not(:disabled)').first();
    await next.click();
    await page.getByRole('button', {name:'Confirm Pick', exact:true}).click();
    await dialog.waitFor({state:'visible'});
    await dialog.getByRole('button', {name:/Continue Drafting/}).click();
    assert.equal(await page.locator('.mw-pick:not(:disabled)').count(), 0);
    await page.reload();
    assert.equal(await dialog.isVisible(), false);

    await setup(2);
    await page.getByRole('button', {name:'Set Bulk Picks',exact:true}).click();
    const bulk = page.locator('.bulk-dialog[open]');
    for (let index=0;index<10;index++) {
      await bulk.locator('[data-bulk-fixture]').first().click();
      await bulk.locator(`[data-bulk-slot="${index}"]`).click();
      await bulk.locator(`[data-index="${index}"][data-bulk-team="0"]`).click();
    }
    await bulk.getByRole('button', {name:'Submit Bulk Picks',exact:true}).click();
    await bulk.getByRole('button', {name:'Confirm Bulk Picks',exact:true}).click();
    await page.getByRole('button', {name:'View Pick Recap',exact:true}).first().waitFor();
    assert.equal(await dialog.isVisible(), false);
    await page.getByRole('button', {name:'View Pick Recap',exact:true}).first().click();
    await dialog.waitFor({state:'visible'});
    assert.match(await dialog.innerText(), /1 of 10/);
    assert.equal(await dialog.getByRole('button', {name:'Previous',exact:true}).isDisabled(), true);
    await dialog.getByRole('button', {name:'Next',exact:true}).click();
    await page.waitForFunction(() => document.querySelector('.insight-navigation').textContent.includes('2 of 10'));
    await geometry(`${width}x${height}: bulk recap`);
    await page.screenshot({path:path.join(out, `bulk-${width}.png`)});
    await dialog.getByRole('button', {name:'Previous',exact:true}).click();
    await page.waitForFunction(() => document.querySelector('.insight-navigation').textContent.includes('1 of 10'));
    await dialog.getByRole('button', {name:'Close',exact:true}).click();

    await setup(3);
    assert.match(await page.locator('#matchweek-view').innerText(), /Matchup set/);
    await page.getByRole('button', {name:'Pick Recap',exact:true}).click();
    await dialog.waitFor({state:'visible'});
    assert.match(await dialog.innerText(), /1 of 5/);
    await geometry(`${width}x${height}: completed recap`);
    await page.screenshot({path:path.join(out, `completed-${width}.png`)});
    await page.keyboard.press('Escape');
  }
  // A failed insight GET cannot turn a successful pick into a second submission.
  await page.route('**/pick-insight/**', route => route.fulfill({status:503,body:'Unavailable'}));
  await page.getByRole('button', {name:'Pick Recap',exact:true}).click();
  await dialog.waitFor({state:'visible'});
  assert.match(await dialog.innerText(), /Your saved picks are unchanged/);
  await dialog.getByRole('button', {name:'Close',exact:true}).click();
  await page.unroute('**/pick-insight/**');
  await page.getByRole('button', {name:'Pick Recap',exact:true}).click();
  await page.waitForFunction(() => document.querySelector('.insight-navigation')?.textContent.includes('1 of 5'));
  await page.keyboard.press('Escape');
  assert.deepEqual(errors, []);
  assert.deepEqual(providers, []);
  fs.writeFileSync(path.join(out,'results.json'), JSON.stringify({checks,errors,providers},null,2));
  await browser.close();
  console.log(`PASS: ${checks.length} layout checks; manual persistence, consecutive turns, bulk confirmation, navigation, focus, Escape, no replay or browser provider calls`);
})().catch(error => {console.error(error);process.exit(1);});
