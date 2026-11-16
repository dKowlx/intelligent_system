const {test}=require('node:test');
const assert=require('node:assert/strict');
const {layoutRoute,taskTiming}=require('../../web/route-map.js');
const task=(id,deps=[])=>({id,title:id,depends_on:deps,minutes:30});
test('branches and merges retain their edges on a horizontal topological route',()=>{const g=layoutRoute([task('a'),task('b',['a']),task('c',['a']),task('d',['b','c'])]);assert.deepEqual(g.nodes.map(n=>n.column),[0,1,2,3]);assert.equal(g.nodes[1].y,g.nodes[2].y);assert.equal(g.edges.length,4);assert.deepEqual(g,layoutRoute([task('a'),task('b',['a']),task('c',['a']),task('d',['b','c'])]));});
test('dependencies resolve IDs regardless of input order',()=>{assert.deepEqual(layoutRoute([task('b',['a']),task('a')]).nodes.map(n=>n.column),[1,0]);});
test('invalid IDs, missing dependencies and cycles reject graph',()=>{for(const tasks of [[task('a'),task('a')],[task('')],[task('a',['missing'])],[task('a',['b']),task('b',['a'])],[task('a',['a'])]])assert.throws(()=>layoutRoute(tasks));});
test('date absent stays absent and split task retains all days',()=>{const result={schedule:[{day:1,date:null,tasks:[{id:'a'}]},{day:3,date:'2026-11-11',tasks:[{id:'a'}]}],remaining_tasks:[{id:'a'}]};assert.equal(taskTiming(task('a'),result,false),'День 1, День 3 · 2026-11-11 · частично');assert.equal(taskTiming(task('a'),result,true),'Нужен расчёт');assert.equal(taskTiming(task('b'),result,false),'Вне плана');assert.equal(taskTiming(task('a'),null,false),'Нужен расчёт');});
test('untrusted titles do not affect IDs or layout',()=>{const t={...task('safe'),title:'<img src=x onerror=alert(1)>'};assert.equal(layoutRoute([t]).nodes[0].task.title,t.title);const source=require('node:fs').readFileSync(require.resolve('../../web/route-map.js'),'utf8');assert.equal(source.includes('innerHTML'),false);});
test('long edges route below all cards and cross no card interior',()=>{
 const g=layoutRoute([task('a'),task('b',['a']),task('c',['b','a']),task('d')]);
 const edge=g.edges.find(e=>e.from==='a'&&e.to==='c');assert.ok(edge.points);
 for(let i=1;i<edge.points.length;i++){
   const [x1,y1]=edge.points[i-1],[x2,y2]=edge.points[i];
   for(const n of g.nodes){
     const intersects = x1===x2 ? x1>n.x&&x1<n.x+236&&Math.max(y1,y2)>n.y&&Math.min(y1,y2)<n.y+164 : y1>n.y&&y1<n.y+164&&Math.max(x1,x2)>n.x&&Math.min(x1,x2)<n.x+236;
     assert.equal(intersects,false,`edge crosses ${n.task.id}`);
   }
 }
 assert.ok(edge.points[2][1]>Math.max(...g.nodes.map(n=>n.y+164)));
});
test('ready/blocked status and invalid estimates are explicit',()=>{
 const {taskStatus,taskEstimate}=require('../../web/route-map.js');
 const a=task('a'),b=task('b',['a']);assert.equal(taskStatus(a,[a,b]),'○ Можно начать');assert.equal(taskStatus(b,[a,b]),'◷ Ждёт предыдущих действий');
 a.completed=true;assert.equal(taskStatus(b,[a,b]),'○ Можно начать');assert.equal(taskTiming(a,null,true),'Выполнено');
 for(const minutes of [NaN,undefined,-1,0,'30'])assert.equal(taskEstimate({...a,minutes}),'Уточните оценку');
 assert.equal(taskEstimate({...a,minutes:30,actual_minutes:0}),'30 мин · факт 0 мин');
});
test('fit covers full canvas width and height at mobile and desktop sizes',()=>{
 const {fitScale}=require('../../web/route-map.js');
 const g=layoutRoute(Array.from({length:20},(_,i)=>task(`t${i}`,i ? [`t${i-1}`] : [])));
 for(const [w,h] of [[340,360],[1200,520]]){const scale=fitScale(g,w,h);assert.ok(scale<.35);assert.ok(g.width*scale<=w-16);assert.ok(g.height*scale<=h-16);}
 assert.equal(fitScale(g,0,360),null);
 assert.equal(fitScale({width:100,height:2000},1200,520),(520-16)/2000);
});

test('independent actions have visual order connectors without prerequisite changes',()=>{
 const {taskStatus}=require('../../web/route-map.js');
 const tasks=Array.from({length:5},(_,i)=>task(`t${i}`));
 const before=JSON.stringify(tasks),g=layoutRoute(tasks);
 assert.equal(g.edges.length,0);assert.equal(g.orderEdges.length,4);
 assert.deepEqual(g.orderEdges.map(edge=>[edge.from,edge.to]),[['t0','t1'],['t1','t2'],['t2','t3'],['t3','t4']]);
 assert.ok(g.orderEdges.every(edge=>edge.path.includes(' L ')));
 assert.ok(tasks.every(t=>taskStatus(t,tasks)==='○ Можно начать'));
 assert.equal(JSON.stringify(tasks),before);
 const dag=layoutRoute([task('a'),task('b',['a']),task('c')]);
 assert.equal(dag.orderEdges.length,0);assert.equal(dag.edges.length,1);
});

test('theory reading uses saved text, hides original links and rejects executable URLs',()=>{
 const {renderTheory}=require('../../web/route-map.js');
 const doc={defaultView:{location:{origin:'http://localhost'}},createElement(tag){return {tag,children:[],className:'',_text:'',get textContent(){return this._text+this.children.map(n=>n.textContent).join('');},set textContent(v){this._text=String(v);},append(n){this.children.push(n);},ownerDocument:doc};}};
 const target=doc.createElement('section');
 const group={resources:[{title:'Теория',excerpt:'<img onerror=alert(1)>',summary:'Краткое объяснение',external_url:'https://example.test/theory',scope:'web_theory'},{title:'Литература',excerpt:'Учебник упомянут в условии',reference_note:'Текст книги не загружен',url:'javascript:alert(1)',scope:'project'}]};
 const before=JSON.stringify(group);renderTheory(target,group,{done_when:'Проверить результат'});
 assert.match(target.textContent,/Краткое объяснение/);assert.match(target.textContent,/<img onerror/);assert.match(target.textContent,/Текст книги не загружен/);
 const all=node=>[node,...node.children.flatMap(all)], nodes=all(target),links=nodes.filter(n=>n.tag==='a');
 assert.equal(links.length,1);assert.equal(links[0].href,'https://example.test/theory');
 assert.ok(nodes.filter(n=>n.tag==='details').every(n=>!n.open));
 assert.equal(nodes.some(n=>n.tag==='img'),false);assert.equal(JSON.stringify(group),before);
});

test('attached reading annotations fit inside graph and remain clear of ordering lines',()=>{
 const g=layoutRoute([task('a'),task('b'),task('c')]);
 for(const node of g.nodes)assert.ok(node.y+164+14+104<=g.height);
 assert.ok(g.nodes.every(n=>n.y===g.nodes[0].y));
 assert.ok(g.nodes[1].x>=g.nodes[0].x+236+24);
 assert.equal(g.height,330);
});


test('theory annotations and reading prefer web explanations without changing source data',()=>{
 const {theoryResources}=require('../../web/route-map.js');
 const local={title:'Временный проект',scope:'project',excerpt:'Условия'},web={title:'Экспонента матрицы',scope:'web_theory',summary:'Объяснение'},second={title:'Второй источник',scope:'web_theory',excerpt:'Теория'};
 const group={resources:[local,web,second]},before=JSON.stringify(group);
 assert.deepEqual(theoryResources(group),[web,second,local]);
 assert.equal(JSON.stringify(group),before);
});
