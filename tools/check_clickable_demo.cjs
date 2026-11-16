/* Real Chromium file:// QA. HTTP(S) requests are blocked and counted. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const {chromium}=require('playwright');
const target=path.resolve(process.argv[2]||'var/share/project-navigator-demo.html');
const output=path.resolve(process.env.DEMO_QA_OUTPUT||'var/clickable-demo-qa');
fs.mkdirSync(path.join(output,'screenshots'),{recursive:true});
(async()=>{
 const report={target,protocol:'file:',checks:[],bounds:[],networkAttempts:[],errors:[],consoleErrors:[]};
 const check=(name,value)=>{assert.ok(value,name);report.checks.push(name)};
 const html=fs.readFileSync(target,'utf8');
 check('single HTML exists',html.length>20000);
 check('no external script/style/image assets',!/<(?:script|img)\b[^>]*\bsrc\s*=|<link\b[^>]*\bhref\s*=/i.test(html));
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
 const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
 await context.route(/^https?:\/\//,route=>{report.networkAttempts.push(route.request().url());return route.abort()});
 const page=await context.newPage();page.setDefaultTimeout(12000);
 page.on('pageerror',e=>report.errors.push(e.message));
 page.on('console',e=>{if(e.type()==='error')report.consoleErrors.push(e.text())});
 await page.goto(pathToFileURL(target).href);
 await page.locator('#card-form').waitFor({state:'visible'});
 check('starts from file URL',page.url().startsWith('file:'));
 check('two authored projects',await page.locator('#assignments li').count()===2);
 await page.locator('#assignments button').nth(1).click();await page.locator('[data-workflow=overview]').click();
 check('reference project 470/450/20',JSON.stringify(await page.locator('#overview-metrics .metric-value').allTextContents())===JSON.stringify(['0 из 5','7 ч 50 мин','7 ч 30 мин','20 мин']));
 await page.locator('#assignments button').first().click();
 await page.locator('[data-workflow=planning]').click();
 check('launch seed has concrete step list',await page.locator('#plan-result .day-step').count()>=15);
 const initialDays=await page.evaluate(()=>savedPlan.result.schedule.map(d=>({minutes:d.tasks.reduce((n,t)=>n+t.minutes,0),steps:d.tasks.flatMap(t=>t.substeps||[]).length})));
 check('launch keeps225 remaining and two empty days',initialDays.reduce((n,d)=>n+d.minutes,0)===225&&initialDays[1].steps===0&&initialDays[4].steps===0);
 check('day list counts match allocated substeps',JSON.stringify(await page.locator('#plan-result .plan-day').evaluateAll(days=>days.map(d=>d.querySelectorAll('.day-step').length)))===JSON.stringify(initialDays.map(d=>d.steps)));
 const nav=async view=>{await page.locator(`[data-workflow="${view}"]`).click();await page.locator({overview:'#overview-panel',planning:'#planning',card:'#card-panel',chat:'#chat-panel',analytics:'#analytics-panel'}[view]).waitFor({state:'visible'})};
 const theme=async value=>page.locator('#theme-select').selectOption(value);
 check('three theme choices',await page.locator('#theme-select option').count()===3);
 for(const value of ['dark','light']){
  await theme(value);await page.reload();await page.locator('#card-form').waitFor({state:'visible'});
  check(value+' persists reload',await page.locator('html').getAttribute('data-theme')===value);
  for(const width of [390,1440]){
   await page.setViewportSize({width,height:width===390?850:1000});
   for(const view of ['card','chat','overview','planning','analytics']){
    await nav(view);await page.evaluate(()=>scrollTo(0,0));
    const bounds=await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth}));
    check(`${value}/${width}/${view}: bounds`,bounds.scrollWidth<=width);report.bounds.push({theme:value,view,...bounds});
    await page.screenshot({path:path.join(output,'screenshots',`${value}-${width}-${view}.png`),fullPage:true});
    if(view==='planning'){await page.locator('#plan-result .plan-day').first().scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,'screenshots',`${value}-${width}-daily-steps.png`)});}
   }
   await page.locator('.add-assignment-link').click();
   const b=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);check(`${value}/${width}/create: bounds`,b);
   await page.screenshot({path:path.join(output,'screenshots',`${value}-${width}-create.png`),fullPage:true});
   await page.locator('#assignments button').first().click();
  }
 }
 for(const value of ['light','dark']) {
  await theme(value);
  for(const width of [320,390]) {
   await page.setViewportSize({width,height:850});await nav('planning');
   if(!await page.locator('#decomposition').evaluate(n=>n.open))await page.locator('#decomposition > summary').click();
   for(const detail of await page.locator('#card-actions-editor details').all())await detail.evaluate(n=>n.open=false);
   for(const detail of (await page.locator('#card-actions-editor details').all()).slice(0,2))await detail.locator('summary').click();
   const bounds=await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth}));
   check(`${value}/${width}/expanded-decomposition: bounds`,bounds.scrollWidth<=width);report.bounds.push({theme:value,view:'expanded-decomposition',...bounds});
   await page.screenshot({path:path.join(output,'screenshots',`${value}-${width}-expanded-decomposition.png`),fullPage:true});
   await page.locator('.substep-editor-row').first().scrollIntoViewIfNeeded();
   await page.screenshot({path:path.join(output,'screenshots',`${value}-${width}-substep-editor.png`)});
   check(`${value}/${width}/substep editor visible`,await page.locator('.substep-editor-row').first().isVisible());
   const clipped=await page.locator('#planning button, #planning input, #planning select').evaluateAll(nodes=>nodes.filter(n=>n.getClientRects().length).filter(n=>{const r=n.getBoundingClientRect();return r.left < -1 || r.right > innerWidth+1}).map(n=>n.getAttribute('aria-label')||n.textContent));
   check(`${value}/${width}/editor controls in bounds`,clipped.length===0);
  }
 }
 await page.setViewportSize({width:1440,height:1000});await nav('overview');
 await page.locator('.route-map-node').first().click();
 check('graph node reveals result and readiness',(await page.locator('.route-map-details').innerText()).includes('Готово, когда'));
 await page.locator('.route-map-details .route-open').click();check('node CTA opens plan',await page.locator('#planning').isVisible());
 await page.getByLabel('Доступно минут в день 2',{exact:true}).fill('0');await page.locator('#plan-reserve').fill('20');
 await page.locator('#plan-tasks input[type=checkbox]').nth(1).check();await page.locator('#plan-button').click();
 await page.waitForFunction(()=>!document.querySelector('#plan-button').disabled&&!document.querySelector('#plan-result').hidden);
 await nav('overview');await nav('planning');
 check('busy day persists',await page.getByLabel('Доступно минут в день 2',{exact:true}).inputValue()==='0');
 check('reserve persists',await page.locator('#plan-reserve').inputValue()==='20');
 check('completion persists',await page.locator('#plan-tasks input[type=checkbox]').nth(1).isChecked());
 await nav('analytics');await page.locator('.analytics-chart svg').first().waitFor();check('analytics has two history graphs',await page.locator('.analytics-chart svg').count()===2);
 await nav('card');check('card evidence removed',await page.locator('#card-evidence').count()===0);
 check('article library removed',await page.locator('#research-library, .research-library').count()===0);
 await nav('planning');check('coverage and assumptions removed',await page.locator('#plan-result .coverage-summary, #plan-result .plan-assumptions').count()===0);
 const daily=await page.evaluate(()=>savedPlan.result.schedule.map(d=>({day:d.day,minutes:d.tasks.reduce((n,t)=>n+t.minutes,0),steps:d.tasks.flatMap(t=>t.substeps||[]).length,exact:d.tasks.every(t=>!t.substeps?.length||t.substeps.reduce((n,s)=>n+s.minutes,0)===t.minutes)})));
 check('daily child effort matches parents',daily.every(d=>d.exact));check('busy day has no fabricated steps',daily[1].minutes===0&&daily[1].steps===0);
 await nav('planning');if(!await page.locator('#decomposition').evaluate(n=>n.open))await page.locator('#decomposition > summary').click();await page.locator('#card-actions-editor details').nth(2).locator('summary').click();await page.getByLabel('Приоритет, действие 3',{exact:true}).selectOption('high');await page.locator('#save-actions').click();await page.waitForFunction(()=>!document.querySelector('#save-actions').disabled || !state.cardDirty);check('priority saved',(await page.evaluate(()=>state.detail.card.tasks[2].priority))==='high');
 await page.locator('#plan-button').click();await page.waitForFunction(()=>!document.querySelector('#plan-button').disabled&&!state.planDirty);
 await nav('chat');await page.locator('#question').fill('отметь Собрать кликабельный прототип выполненным');await page.locator('#send-button').click();await page.locator('.assistant-proposal').last().waitFor();
 await page.screenshot({path:path.join(output,'screenshots','assistant-proposal.png'),fullPage:true});check('proposal waits for apply',!await page.evaluate(()=>savedPlan.input.tasks.find(t=>t.id==='prototype').completed));await page.getByRole('button',{name:'Применить к плану',exact:true}).click();await page.waitForFunction(()=>savedPlan.input.tasks.find(t=>t.id==='prototype').completed);check('explicit apply updates saved plan',true);
 await nav('chat');await page.locator('#question').fill('Что должно получиться?');await page.locator('#send-button').click();
 await page.locator('.message-assistant:not(.is-pending)').last().waitFor();
 check('chat returns project context',(await page.locator('.message-assistant').last().innerText()).includes('Проект:'));
 // Common source dialog is shared with the local UI.
 const source=page.locator('.source-open:visible, .source-chip:visible').first();
 if(await source.count()){await source.click();await page.locator('dialog[open]').waitFor();check('source opens local dialog',true);await page.locator('dialog[open] button').first().click()}
 else throw Error('No source link exposed for local dialog check');
 await page.locator('.add-assignment-link').click();await page.locator('#project-name').fill('QA: ручной проект');await page.locator('#project-text').fill('Подготовить краткий проверяемый результат. Срок неизвестен.');await page.locator('#text-create').click();
 await page.locator('#card-form').waitFor({state:'visible'});await page.locator('#card-goal').fill('Проверить самостоятельный ручной путь');await page.locator('#card-deliverable').fill('Готовый текст');await page.locator('#card-requirements').fill('Текст содержит вывод');await page.locator('#card-criteria').fill('Вывод проверен');
 await page.locator('#card-confirm').click();await page.locator('#planning').waitFor({state:'visible'});check('manual text creation and card confirmation',true);
 await page.locator('#plan-button').click();await page.locator('#plan-result').waitFor({state:'visible'});check('manual project plan works',true);
 await page.reload();await page.waitForFunction(()=>document.querySelectorAll('#assignments li').length===2);check('reload restores two authored projects',true);
 await nav('planning');check('reload reset removes changed completion',!await page.locator('#plan-tasks input[type=checkbox]').nth(1).isChecked());check('reload reset removes reserve edit',await page.locator('#plan-reserve').inputValue()==='10');
 check('zero HTTP(S) requests',report.networkAttempts.length===0);check('zero runtime errors',report.errors.length===0);check('zero console errors',report.consoleErrors.length===0);
 }finally{fs.writeFileSync(path.join(output,'browser-report.json'),JSON.stringify(report,null,2));await browser.close()}
 console.log(JSON.stringify({checks:report.checks.length,bounds:report.bounds.length,networkAttempts:report.networkAttempts.length,errors:report.errors}));
})().catch(e=>{console.error(e);process.exitCode=1});
