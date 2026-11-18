/* Optional real-browser companion to test_codex_integration.py.
 * Credentials enter over private stdin and never appear in logs or arguments. */
'use strict';
const readline = require('node:readline');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.NAVIGATOR_PLAYWRIGHT_MODULE || 'playwright');
let browser, context, page;
const lines = readline.createInterface({input:process.stdin});
async function command(input) {
  if (input.op === 'open') {
    browser = await chromium.launch({headless:true,...(process.env.NAVIGATOR_CHROMIUM_PATH ? {executablePath:process.env.NAVIGATOR_CHROMIUM_PATH} : {})});
    context = await browser.newContext();
    await context.addCookies(input.cookies.map(cookie => ({name:cookie.name,value:cookie.value,url:input.url})));
    page = await context.newPage();
    page.on('pageerror', error => { throw error; });
    await page.goto(input.url);
    await page.getByRole('button',{name:'Team workshop',exact:false}).click();
    await page.getByRole('tab',{name:'Действия и план',exact:true}).click();
    await page.locator('.plan-task').first().waitFor();
    assert.equal(await page.locator('#codex-panel').getAttribute('open'),null);
    return {ready:true};
  }
  if (input.op === 'grant') {
    await page.locator('#codex-panel > summary').click();
    for (const scope of ['reports','progress','plan']) {
      assert.equal(await page.locator('#codex-scope-'+scope).isChecked(),false);
      if (input.scopes.includes(scope)) await page.locator('#codex-scope-'+scope).check();
    }
    assert.equal(await page.locator('#codex-completion-mode').inputValue(),'review');
    await page.locator('#codex-completion-mode').selectOption(input.mode);
    await page.locator('#codex-create').click();
    await page.locator('#codex-token-box').waitFor({state:'visible'});
    const token = await page.locator('#codex-token').inputValue();
    await page.locator('#codex-hide-token').click();
    await page.locator('#codex-panel > summary').click();
    return {token};
  }
  if (input.op === 'expect') {
    await page.waitForFunction(({minutes,completed}) => {
      const row=document.querySelector('.plan-task');
      return row.querySelector('[aria-label^="Фактические минуты:"]').value===String(minutes)
        && row.querySelector('input[type=checkbox]').checked===completed;
    },input,{timeout:20000});
    return {matched:true};
  }
  if (input.op === 'correct') {
    const row=page.locator('.plan-task').first();
    await row.locator('.task-detail > summary').click();
    const correction=row.locator('.progress-correction').last();
    await correction.locator('summary').click();
    await correction.locator('select').selectOption('reopen');
    await correction.locator('input[type=number]').fill(String(input.minutes));
    await correction.locator('input[placeholder]').fill('Owner verified the agenda and corrected the total');
    await correction.getByRole('button').click();
    await command({op:'expect',minutes:input.minutes,completed:false});
    return {corrected:true};
  }
  if (input.op === 'draft') {
    await page.locator('[aria-label^="Фактические минуты:"]').first().fill(String(input.minutes));
    return {draft:true};
  }
  if (input.op === 'expect-draft') {
    await page.waitForFunction(()=>document.querySelector('#plan-save-status').textContent.includes('Ваши правки сохранены'),{},{timeout:20000});
    assert.equal(await page.locator('[aria-label^="Фактические минуты:"]').first().inputValue(),String(input.minutes));
    return {preserved:true};
  }
  if (input.op === 'close') { await browser.close(); return {closed:true}; }
  throw Error('Unknown browser operation');
}
(async()=>{try { for await (const line of lines) {
  try { const result=await command(JSON.parse(line)); process.stdout.write(JSON.stringify({ok:true,...result})+'\n'); if (result.closed) { lines.close(); break; } }
  catch (error) { process.stdout.write(JSON.stringify({ok:false,error:error.message})+'\n'); if(browser)await browser.close();process.exitCode=1;break; }
} } finally { if(browser) await browser.close(); } })();
