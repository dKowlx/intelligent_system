/* Optional real-browser check. Run against a LOCAL, OFFLINE server only.
   NODE_PATH=/path/to/node_modules node tools/check_project_ui.cjs
   Uses Playwright + installed Chrome; no paid model calls or personal profile. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const base = process.env.UI_BASE_URL || 'http://127.0.0.1:8011';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'Only loopback hosts are allowed');
const output = path.resolve('var/workspace-redesign-qa');
fs.mkdirSync(path.join(output, 'screenshots'), { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROME_PATH
    ? { executablePath: process.env.CHROME_PATH } : { channel: 'chrome' }) });
  const report = { mode: 'offline', checks: [], viewports: [], errors: [], consoleErrors: [] };
  const check = (name, condition) => { assert.ok(condition, name); report.checks.push(name); };
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      const local = url.origin === new URL(base).origin;
      const allowed = local && (/^\/(?:$|static\/|api\/)/.test(url.pathname) || url.pathname === '/favicon.ico');
      return allowed ? route.continue() : route.abort();
    });
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    page.on('pageerror', e => report.errors.push(e.message));
    page.on('console', message => { if (message.type() === 'error') report.consoleErrors.push(message.text()); });
    check('Server is explicitly offline', (await (await context.request.get(`${base}/api/status`)).json()).mode === 'offline');
    const sections = { overview: '#overview-panel', card: '#card-panel', planning: '#planning', chat: '#chat-panel' };
    const navigate = async view => {
      await page.locator(`[data-workflow="${view}"]`).click();
      await page.locator(sections[view]).waitFor({ state: 'visible' });
      check(`${view}: selected tab semantics`, await page.locator(`[data-workflow="${view}"]`).getAttribute('aria-selected') === 'true');
      check(`${view}: contextual materials visibility`, await page.locator('#materials-panel').isVisible() === (view === 'card'));
      check(`${view}: assistant sources visibility`, await page.locator('#context-column').isVisible() === (view === 'chat'));
      for (const [name, selector] of Object.entries(sections)) {
        check(`${view}: ${name} view visibility`, await page.locator(selector).isVisible() === (name === view));
      }
    };
    const evaluateBounds = async () => page.evaluate(() => ({
      width: innerWidth, documentWidth: document.documentElement.scrollWidth,
      mainWidth: document.querySelector('main').getBoundingClientRect().width,
      styles: { font: getComputedStyle(document.querySelector('main')).fontFamily, headingSize: getComputedStyle(document.querySelector('#assignment-title')).fontSize, tabsDisplay: getComputedStyle(document.querySelector('#workflow-nav')).display },
      fieldsOutsideParent: [...document.querySelectorAll('main input, main textarea, main button')].filter(e => {
        const r = e.getBoundingClientRect(); const p = e.parentElement.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && (r.right > p.right + 2 || r.left < p.left - 2);
      }).map(e => e.id || e.getAttribute('aria-label') || e.className),
    }));
    await page.goto(base);
    await page.waitForFunction(() => !document.querySelector('#project-example').disabled && !document.querySelector('#mode-notice').textContent.includes('Подключаем'));
    await page.locator('.add-assignment-link').click();
    await page.locator('#upload-panel').waitFor({ state: 'visible' });
    for (const width of [1440, 1280, 1024, 768, 390, 360, 320]) {
      await page.setViewportSize({ width, height: width > 800 ? 1000 : 800 });
      const bounds = await evaluateBounds();
      assert.ok(bounds.documentWidth <= width, JSON.stringify({ view: 'create', ...bounds }));
      assert.deepEqual(bounds.fieldsOutsideParent, [], JSON.stringify({ view: 'create', ...bounds }));
      report.viewports.push({ view: 'create', ...bounds });
      await page.screenshot({ path: path.join(output, 'screenshots', `create-${width}.png`) });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator('#project-example').click(); // Auto-waits for initialization.
    await page.locator('#text-create').click();
    await page.waitForFunction(() => document.querySelector('#card-deliverable').value === 'Исследовательский отчёт с доказательствами');
    await page.locator('#card-confirm').click();
    await page.waitForFunction(() => !document.querySelector('#plan-button').disabled);
    const save = async () => {
      const response = page.waitForResponse(r => r.url().endsWith('/plan') && r.request().method() === 'PUT');
      await page.locator('#plan-button').click();
      const result = await response;
      assert.equal(result.status(), 200, await result.text());
      await page.waitForFunction(() => !document.querySelector('#plan-result').hidden);
      return (await result.json()).plan;
    };
    await page.locator('#planning').waitFor({ state: 'visible' });
    let plan = await save();
    check('Reference is 470 required / 450 available / 20 deficit', plan.result.required_minutes === 470 && plan.result.available_minutes === 450 && plan.result.deficit_minutes === 20);
    check('Busy day stays empty', plan.result.schedule[1].tasks.length === 0);
    check('Missing work is visible', (await page.locator('#plan-result').innerText()).includes('Не помещается в план'));
    check('All four requirements have checkable actions', plan.result.coverage.complete_check_count === 4);
    check('Initial estimate basis survives confirmation', plan.input.tasks[1].estimate_basis.includes('5 ×'));

    await navigate('overview');
    assert.deepEqual(await page.locator('#overview-metrics .metric-value').allTextContents(), ['0 из 5', '7 ч 50 мин', '7 ч 30 мин', '20 мин']);
    check('Overview metrics reflect saved reference plan', true);
    check('Overview contains real project and next action', (await page.locator('#overview-panel').innerText()).includes('Подготовить сценарий'));
    await navigate('planning');
    await page.locator('#plan-tasks input[type=checkbox]').first().check();
    await page.getByLabel('Фактические минуты: Подготовить сценарий и согласия участников', { exact: true }).fill('70');
    await page.getByLabel('Минут на задачу 2', { exact: true }).fill('180');
    plan = await save();
    check('Progress and remaining estimate change required work to 405', plan.result.required_minutes === 405 && plan.input.tasks[0].actual_minutes === 70);
    await page.reload();
    await page.waitForFunction(() => document.querySelector('#plan-save-status').textContent.includes('восстановлены'));
    await navigate('overview');
    assert.deepEqual(await page.locator('#overview-metrics .metric-value').allTextContents(), ['1 из 5', '6 ч 45 мин', '7 ч 30 мин', '0 мин']);
    check('Overview restores saved progress and metrics after reload', true);
    check('Completed action is no longer the next action', !(await page.locator('#overview-focus').innerText()).includes('Подготовить сценарий'));
    await navigate('planning');
    check('Progress survives reload', await page.locator('#plan-tasks input[type=checkbox]').first().isChecked());
    check('Actual time survives reload', (await page.getByLabel('Фактические минуты: Подготовить сценарий и согласия участников', { exact: true }).inputValue()) === '70');
    check('Remaining estimate survives reload', (await page.getByLabel('Минут на задачу 2', { exact: true }).inputValue()) === '180');

    await navigate('card');
    if (!await page.locator('#card-title').isVisible()) await page.locator('#card-toggle').click();
    await page.locator('#card-title').fill('Исследование пользовательского пути');
    check('Dirty brief disables saving plan and sending chat', await page.locator('#plan-button').isDisabled() && await page.locator('#send-button').isDisabled());
    await navigate('overview');
    await navigate('card');
    check('Card draft survives view navigation', await page.locator('#card-title').inputValue() === 'Исследование пользовательского пути');
    await page.locator('#card-confirm').click();
    await page.locator('#planning').waitFor({ state: 'visible' });
    await page.waitForFunction(() => !document.querySelector('#plan-button').disabled);
    await page.waitForFunction(() => document.querySelector('#plan-stale').hidden === false);
    check('Unchanged actions retain completion after card update', await page.locator('#plan-tasks input[type=checkbox]').first().isChecked());
    plan = await save();
    check('Updated card saves without losing actual progress', plan.input.tasks[0].actual_minutes === 70 && plan.input.tasks[0].completed === true);

    await page.locator('#plan-start-date').fill('2026-11-09');
    await page.locator('#plan-reserve').fill('10');
    await navigate('overview');
    await navigate('planning');
    check('Plan draft survives view navigation', await page.locator('#plan-reserve').inputValue() === '10' && await page.locator('#plan-start-date').inputValue() === '2026-11-09');
    plan = await save();
    check('Reserve and start date are deterministic', plan.result.available_minutes === 405 && plan.result.schedule[0].date === '2026-11-09');

    for (const width of [1440, 1280, 1024, 768, 390, 360, 320]) {
      await page.setViewportSize({ width, height: width > 800 ? 1000 : 800 });
      for (const view of Object.keys(sections)) {
        await navigate(view);
        if (width <= 390 && ['card', 'planning'].includes(view)) await page.locator(`${sections[view]} details`).evaluateAll(nodes => nodes.forEach(node => { node.open = true; }));
        await page.evaluate(() => window.scrollTo(0, 0));
        const bounds = await evaluateBounds();
        assert.ok(bounds.documentWidth <= width, JSON.stringify({ view, ...bounds }));
        assert.deepEqual(bounds.fieldsOutsideParent, [], JSON.stringify({ view, ...bounds }));
        const headings = await page.locator(`#assignment-title, ${sections[view]} h1, ${sections[view]} h2, ${sections[view]} h3`).evaluateAll(nodes => nodes.filter(e => e.getBoundingClientRect().width > 2 && !e.classList.contains('visually-hidden')).map(e => ({ text: e.textContent, clipped: e.scrollWidth > e.clientWidth + 1 })));
        assert.ok(headings.every(h => !h.clipped), JSON.stringify({ view, width, headings }));
        report.viewports.push({ view, ...bounds });
        await page.screenshot({ path: path.join(output, 'screenshots', `${view}-${width}.png`) });
        if (width === 390 && ['overview', 'card', 'planning'].includes(view)) await page.screenshot({ fullPage: true, path: path.join(output, 'screenshots', `${view}-390-full.png`) });
      }
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await navigate('chat');
    await page.locator('#question').fill('Что должно быть в отчёте?');
    await page.locator('#send-button').click();
    await page.locator('.message-assistant:not(.is-pending)').last().waitFor();
    check('Offline chat completes without browser errors', report.errors.length === 0);
    await page.screenshot({ path: path.join(output, 'screenshots', 'chat-offline-response.png') });
    check('Reduced motion disables smooth scrolling', await page.evaluate(() => getComputedStyle(document.documentElement).scrollBehavior !== 'smooth'));
    await navigate('planning');
    await page.locator('#plan-start-date').focus();
    await page.keyboard.press('Tab');
    check('Keyboard focus stays on a visible control', await page.evaluate(() => document.activeElement.getBoundingClientRect().height > 0 && document.activeElement !== document.body));

    await navigate('overview');
    await page.locator('[data-workflow="overview"]').focus();
    await page.keyboard.press('ArrowRight');
    check('Arrow moves focus without changing the active view', await page.locator('[data-workflow="planning"]').evaluate(node => document.activeElement === node) && await page.locator('#overview-panel').isVisible());
    await page.keyboard.press('Enter');
    await page.locator('#planning').waitFor({ state: 'visible' });
    check('Enter activates the focused project tab', await page.locator('[data-workflow="planning"]').getAttribute('aria-selected') === 'true');
    check('No browser console errors', report.consoleErrors.length === 0);
    check('No runtime errors', report.errors.length === 0);
  } finally {
    fs.writeFileSync(path.join(output, 'browser-report.json'), JSON.stringify(report, null, 2));
    await browser.close();
  }
  console.log(JSON.stringify({ checks: report.checks.length, viewports: report.viewports.length, errors: report.errors }));
})().catch(e => { console.error(e); process.exitCode = 1; });
