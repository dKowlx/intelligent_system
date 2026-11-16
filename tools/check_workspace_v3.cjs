/* Local offline backend + browser scenario, no external services. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
(async()=>{
 const output=path.resolve('var/workspace-v3-qa/browser');fs.mkdirSync(output,{recursive:true});
 const report={checks:[],errors:[],consoleErrors:[],network:[],bounds:[]};
 const browser=await chromium.launch({headless:true,channel:'chrome'}),context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
 await context.route('**/*',r=>{if(!r.request().url().startsWith('http://127.0.0.1:8014/')){report.network.push(r.request().url());return r.abort()}return r.continue()});
 const page=await context.newPage();page.setDefaultTimeout(15000);page.on('pageerror',e=>report.errors.push(e.message));page.on('console',e=>{if(e.type()==='error')report.consoleErrors.push(e.text())});
 const check=(name,value)=>{assert.ok(value,name);report.checks.push(name)};
 const nav=async view=>{await page.locator(`[data-workflow=${view}]`).click();await page.waitForFunction(v=>document.querySelector('#workspace').dataset.view===v,view)};
 const save=async()=>{const pending=page.waitForResponse(r=>r.url().endsWith('/plan')&&r.request().method()==='PUT');await page.locator('#plan-button').click();const response=await pending;check('plan save returns200',response.status()===200);await page.locator('#plan-result').waitFor({state:'visible'});return(await response.json()).plan};
 try{
 await page.goto('http://127.0.0.1:8014/');await page.locator('.add-assignment-link').click();await page.locator('#project-example').click();await page.locator('#text-create').click();await page.locator('#card-goal').waitFor({state:'visible'});
 check('new project opens card',await page.locator('#workspace').getAttribute('data-view')==='card');
 check('decomposition moved out of project card',await page.locator('#card-panel #card-actions-editor').count()===0);
 await page.locator('#card-confirm').click();await page.locator('#planning').waitFor({state:'visible'});let plan=await save();
 check('reference470/450/20',plan.result.required_minutes===470&&plan.result.deficit_minutes===20);
 await page.locator('#plan-tasks input[type=checkbox]').first().check();await page.getByLabel('Фактические минуты: Подготовить сценарий и согласия участников',{exact:true}).fill('50');plan=await save();
 await nav('analytics');await page.locator('.analytics-metric').first().waitFor();check('analytics uses recorded50min',(await page.locator('.analytics-metrics').innerText()).includes('50 мин'));
 await nav('card');await page.locator('#add-material > summary').click();await page.locator('#material-title').fill('Проверяемый текст');await page.locator('#material-text').fill('У проекта три сценария. Отчёт должен содержать ограничения и ссылки на наблюдения.');await page.locator('#material-url').fill('https://example.org/project-reference');
 const append=page.waitForResponse(r=>r.url().endsWith('/materials')&&r.request().method()==='POST');await page.locator('#material-add-submit').click();check('text material appended',(await append).status()===201 || (await append).status()===200);
 await page.getByText('Материал добавлен. Теперь он доступен в поиске и ассистенту.',{exact:true}).waitFor();
 await page.locator('.source-card').filter({hasText:'Проверяемый текст'}).locator('.source-open').first().click();await page.locator('#source-dialog[open]').waitFor();await page.locator('#source-dialog a[href="https://example.org/project-reference"]').waitFor();check('source dialog has originalarticle',await page.locator('#source-dialog a[href="https://example.org/project-reference"]').count()===1);await page.locator('#source-close').click();
 await page.locator('#material-kind').selectOption('pdf');await page.locator('#material-title').fill('QA PDF');await page.locator('#material-files').setInputFiles(path.resolve('var/workspace-v3-qa/qa-source.pdf'));
 const pdfResponse=page.waitForResponse(r=>r.url().endsWith('/materials')&&r.request().method()==='POST');await page.locator('#material-add-submit').click();let pdf=await pdfResponse;assert.ok(pdf.ok(),await pdf.text());check('PDF appended to text project',true);
 await page.locator('.source-card').filter({hasText:'qa-source.pdf'}).locator('.source-open').first().click();await page.locator('#source-dialog[open]').waitFor();await page.locator('#source-dialog a').first().waitFor();check('PDF original link isreal',/\/materials\/.*\.pdf/.test(await page.locator('#source-dialog a').first().getAttribute('href')));await page.locator('#source-close').click();
 await nav('chat');await page.locator('#question').fill('Отметь interviews выполненным');const chatResponse=page.waitForResponse(r=>r.url().endsWith('/api/chat'));await page.locator('#send-button').click();const chat=await chatResponse;assert.equal(chat.status(),200,await chat.text());const answer=await chat.json();check('assistant returnedreviewableproposal',!!answer.action_proposal?.proposal_id);
 await page.locator('.assistant-proposal .button-primary').click();await page.getByText('Изменения сохранены. Действия, расписание и аналитика обновлены.',{exact:true}).waitFor();
 await nav('planning');check('assistant completion visible',await page.locator('#plan-tasks input[type=checkbox]').nth(1).isChecked());
 await page.reload();await page.locator('#card-goal').waitFor({state:'visible'});await nav('planning');check('completion survives reload',await page.locator('#plan-tasks input[type=checkbox]').nth(1).isChecked());await nav('analytics');await page.locator('.analytics-metric').first().waitFor();check('analytics reflects2completed',(await page.locator('.analytics-metrics').innerText()).includes('2 / 5'));
 for(const theme of ['light','dark']){await page.locator('#theme-select').selectOption(theme);for(const width of [320,390,1024,1440]){await page.setViewportSize({width,height:width<700?850:1000});for(const view of ['card','chat','overview','planning','analytics']){await nav(view);if(view==='analytics')await page.locator('.analytics-metric').first().waitFor();const b=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));assert.ok(b.scroll<=width,`${theme}/${width}/${view} bounds ${b.scroll}`);report.bounds.push({theme,view,...b});}await page.screenshot({path:path.join(output,`${theme}-${width}-analytics.png`),fullPage:true});}}
 check('no externalnetwork',report.network.length===0);check('no runtimeerrors',report.errors.length===0);check('no consoleerrors',report.consoleErrors.length===0);
 }finally{fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));await browser.close()}
 console.log(JSON.stringify(report));
})().catch(e=>{console.error(e);process.exitCode=1});
