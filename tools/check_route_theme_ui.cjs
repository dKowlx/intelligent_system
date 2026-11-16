/* Real Chrome, loopback offline server only. No provider or external requests. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const base = process.env.UI_BASE_URL || 'http://127.0.0.1:8013';
assert.ok(['localhost','127.0.0.1'].includes(new URL(base).hostname));
const output = path.resolve('var/route-theme-qa');
fs.mkdirSync(path.join(output,'screenshots'),{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 const report={mode:'offline',checks:[],viewports:[],contrast:[],errors:[],consoleErrors:[]};
 const check=(label,value)=>{assert.ok(value,label);report.checks.push(label)};
 try {
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  await context.route('**/*',route=>new URL(route.request().url()).origin===new URL(base).origin?route.continue():route.abort());
  check('server offline',(await(await context.request.get(base+'/api/status')).json()).mode==='offline');
  const page=await context.newPage(); page.setDefaultTimeout(15000);
  page.on('pageerror',e=>report.errors.push(e.message));
  page.on('console',e=>{if(e.type()==='error')report.consoleErrors.push(e.text())});
  const nav=async view=>{await page.locator(`[data-workflow="${view}"]`).click();};
  const theme=async value=>{await page.locator('#theme-select').selectOption(value);};
  await page.goto(base);
  await page.waitForFunction(()=>!document.querySelector('#project-example').disabled);
  await page.locator('.add-assignment-link').click();
  await page.locator('#project-example').click(); await page.locator('#text-create').click();
  await page.waitForFunction(()=>document.querySelector('#card-deliverable').value==='Исследовательский отчёт с доказательствами');
  await page.locator('#card-confirm').click();
  await page.waitForFunction(()=>!document.querySelector('#plan-button').disabled);
  const saved=page.waitForResponse(r=>r.url().endsWith('/plan')&&r.request().method()==='PUT');
  await page.locator('#plan-button').click(); const planResponse=await saved; check('plan saved (HTTP '+planResponse.status()+')',planResponse.status()===200);
  for(const value of ['dark','light']){
   await theme(value); await page.reload();
   await page.waitForFunction(()=>document.querySelector('#plan-save-status').textContent.includes('восстановлены'));
   check(value+' survives reload',await page.locator('html').getAttribute('data-theme')===value);
   await nav('overview');
   for(const width of [320,390,768,1440]){
    await page.setViewportSize({width,height:width>800?1000:850});
    for(const view of ['overview','planning','card','chat']){
     await nav(view);
     await page.evaluate(()=>window.scrollTo(0,0));
     const bounds=await page.evaluate(()=>({width:innerWidth,documentWidth:document.documentElement.scrollWidth}));
     check(`${value}/${width}/${view} no page overflow`,bounds.documentWidth<=width);
     report.viewports.push({theme:value,view,...bounds});
     if(width===390||width===1440)await page.screenshot({path:path.join(output,'screenshots',`${value}-${view}-${width}.png`),fullPage:width===390});
    }
   }
   await nav('overview');
   const contrasts=await page.evaluate(()=>{
    const rgb=s=>(s.match(/[\d.]+/g)||[]).slice(0,3).map(Number);
    const lum=c=>c.map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
    return ['body','.metric-label','.metric-value','#overview-focus h2','.route-node-title','.route-node-status','.route-node-time','.route-node-day'].flatMap(selector=>{
     const node=document.querySelector(selector);if(!node)return [];
     let ancestor=node,bg;
     while(ancestor){const s=getComputedStyle(ancestor);if(s.backgroundColor!=='rgba(0, 0, 0, 0)'){bg=s.backgroundColor;break}ancestor=ancestor.parentElement;}
     if(!bg)return [];
     const fg=getComputedStyle(node).color;const a=lum(rgb(fg)),b=lum(rgb(bg));
     return [{selector,fg,bg,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)}];
    });
   });
   report.contrast.push({theme:value,pairs:contrasts});
   check(value+' key text contrast >=4.5',contrasts.length>=4&&contrasts.every(p=>p.ratio>=4.5));
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.emulateMedia({colorScheme:'dark'});await theme('system');
  check('system uses OS dark',await page.locator('html').getAttribute('data-theme')==='dark');
  await page.emulateMedia({colorScheme:'light'});
  await page.waitForFunction(()=>document.documentElement.dataset.theme==='light');
  check('system follows OS light',await page.locator('html').getAttribute('data-theme')==='light');
  await page.reload();check('system selection survives reload',await page.locator('#theme-select').inputValue()==='system');
  check('reduced motion removes smooth scrolling',await page.evaluate(()=>getComputedStyle(document.documentElement).scrollBehavior!=='smooth'));
  await nav('chat');await page.locator('#question').fill('Что должно быть в отчёте?');await page.locator('#send-button').click();
  await page.locator('.message-assistant:not(.is-pending)').last().waitFor();
  for(const value of ['dark','light']){await theme(value);await page.screenshot({path:path.join(output,'screenshots',value+'-chat-response.png')});}
  await nav('card');const source=page.locator('#material-results .source-open').first();
  if(await source.count()){
   const url=await source.getAttribute('href');const reader=await context.newPage();await reader.goto(new URL(url,base).href);
   for(const value of ['dark','light']){
    await theme(value);await reader.reload();
    check(value+' source reader theme',await reader.locator('html').getAttribute('data-theme')===value);
   }await reader.close();
  }
  await nav('overview');
  await page.locator('.route-map-node').first().click();
  check('real node opens detail',await page.locator('.route-map-details').isVisible());
  check('real details include result and readiness',(await page.locator('.route-map-details').innerText()).includes('Готово, когда'));
  for(const value of ['dark','light']){await theme(value);await page.screenshot({path:path.join(output,'screenshots',value+'-node-details.png')});}
  await page.locator('.route-map-node').first().focus();await page.keyboard.press('Enter');
  check('keyboard node activates details',await page.locator('.route-map-details h3').evaluate(n=>document.activeElement===n));
  await page.getByRole('button',{name:'Уменьшить',exact:true}).click();
  check('zoom out updates scale',await page.locator('.route-zoom-value').innerText()==='85%');
  await page.getByRole('button',{name:'Масштаб 100%',exact:true}).click();
  check('zoom reset updates scale',await page.locator('.route-zoom-value').innerText()==='100%');
  await page.locator('[data-route-view="list"]').click();
  check('list alternate available',await page.locator('#overview-route').isVisible()&&!await page.locator('#overview-route-diagram').isVisible());
  await page.locator('[data-route-view="diagram"]').click();
  await page.locator('.route-map-details .route-open').click();
  check('detail CTA opens planning',await page.locator('#planning').isVisible());
  await nav('overview');
  const fixture = await page.evaluate(()=>{
   const target=document.querySelector('#overview-route-diagram');
   const tasks=[
    {id:'a',title:'Подготовка',minutes:30,completed:true,actual_minutes:35,depends_on:[]},
    {id:'b',title:'Параллельное исследование',minutes:40,depends_on:['a']},
    {id:'c',title:'Параллельный прототип',minutes:50,depends_on:['a']},
    {id:'d',title:'Объединить результаты',minutes:60,depends_on:['b','c'],expected_result:'Отчёт',done_when:'Проверены оба результата',requirement_ids:['r1'],estimate_basis:'Два результата по 30 минут'}
   ];
   const result={schedule:[{day:1,date:'2026-11-09',tasks:[{id:'b'}]},{day:2,date:'2026-11-10',tasks:[{id:'b'},{id:'c'}]}],remaining_tasks:[{id:'d'}]};
   const layout=NavigatorRouteMap.layoutRoute(tasks);
   NavigatorRouteMap.render(target,{tasks,requirements:[{id:'r1',text:'Проверяемый результат'}],result});
   window.routeQaFixture={tasks,result};
   return {nodes:layout.nodes.map(n=>({id:n.task.id,column:n.column,row:n.row})),edges:layout.edges};
  });
  check('branch has two parallel nodes',fixture.nodes[1].column===fixture.nodes[2].column&&fixture.nodes[1].row!==fixture.nodes[2].row);
  check('merge has two incoming edges',fixture.edges.filter(e=>e.to==='d').length===2);
  check('multiple day allocations stay visible',(await page.locator('.route-map-node').nth(1).innerText()).includes('2026-11-09')&&(await page.locator('.route-map-node').nth(1).innerText()).includes('2026-11-10'));
  for(const value of ['dark','light']){await theme(value);await page.locator('#overview-route-diagram').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,'screenshots',value+'-branch-merge.png')});}
  await page.evaluate(()=>NavigatorRouteMap.render(document.querySelector('#overview-route-diagram'),{...window.routeQaFixture,stale:true}));
  check('stale graph hides obsolete dates',!(await page.locator('#overview-route-diagram').innerText()).includes('2026-11-09')&&(await page.locator('#overview-route-diagram').innerText()).includes('Нужен расчёт'));
  await page.evaluate(()=>{const {tasks,result}=window.routeQaFixture;NavigatorRouteMap.render(document.querySelector('#overview-route-diagram'),{tasks,result:{...result,schedule:result.schedule.map(d=>({...d,date:null}))}})});
  check('unknown dates remain day labels',!(await page.locator('#overview-route-diagram').innerText()).includes('2026-')&&(await page.locator('#overview-route-diagram').innerText()).includes('День 1'));
  await page.setViewportSize({width:390,height:850});
  await page.evaluate(()=>{const tasks=Array.from({length:20},(_,i)=>({id:'fit'+i,title:'Действие '+(i+1),minutes:30,depends_on:i?['fit'+(i-1)]:[]}));NavigatorRouteMap.render(document.querySelector('#overview-route-diagram'),{tasks});});
  await page.getByRole('button',{name:'Вместить схему',exact:true}).click();
  const fit=await page.evaluate(()=>{const v=document.querySelector('.route-viewport'),c=document.querySelector('.route-canvas');return {viewport:v.clientWidth,canvas:c.getBoundingClientRect().width,document:document.documentElement.scrollWidth,width:innerWidth}});
  report.fit=fit;
  check('20 task Fit stays within mobile viewport',fit.canvas<=fit.viewport+1&&fit.document<=fit.width);
  await page.reload();await page.waitForFunction(()=>document.querySelector('#plan-save-status').textContent.includes('восстановлены'));await nav('overview');
  await theme('dark');await page.locator('#overview-route-diagram').screenshot({path:path.join(output,'screenshots','dark-graph-390-region.png')});
  await page.locator('[data-route-view="list"]').click();await nav('chat');await nav('overview');
  check('list choice survives workflow navigation',await page.locator('#overview-route').isVisible());
  await page.reload();await page.waitForFunction(()=>document.querySelector('#plan-save-status').textContent.includes('восстановлены'));await nav('overview');
  check('list choice survives reload',await page.locator('#overview-route').isVisible());
  await page.locator('[data-route-view="diagram"]').click();await page.setViewportSize({width:1440,height:1000});await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:path.join(output,'screenshots','dark-overview-final-1440.png')});
  check('no runtime errors',report.errors.length===0);check('no console errors',report.consoleErrors.length===0);
 }finally{fs.writeFileSync(path.join(output,'browser-report.json'),JSON.stringify(report,null,2));await browser.close()}
 console.log(JSON.stringify({checks:report.checks.length,viewports:report.viewports.length,errors:report.errors}));
})().catch(e=>{console.error(e);process.exitCode=1});
