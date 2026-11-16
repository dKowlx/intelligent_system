(function (root) {
  "use strict";
  const views = new WeakMap();
  const WIDTH = 236, HEIGHT = 164, GAP_X = 72, GAP_Y = 148, PAD = 24;
  function layoutRoute(tasks) {
    const rows = Array.isArray(tasks) ? tasks : [];
    const ids = new Map();
    for (const task of rows) {
      if (!task || typeof task.id !== 'string' || !task.id.trim() || ids.has(task.id)) throw new Error('У действий должны быть уникальные непустые ID.');
      ids.set(task.id, task);
    }
    const levels = new Map(), visiting = new Set();
    function level(id) {
      if (levels.has(id)) return levels.get(id);
      if (visiting.has(id)) throw new Error('В зависимостях есть цикл. Исправьте связи действий.');
      visiting.add(id);
      const task = ids.get(id), deps = task.depends_on || [];
      if (!Array.isArray(deps)) throw new Error('Не удалось прочитать зависимости действия.');
      let value = 0;
      for (const dep of deps) {
        if (!ids.has(dep)) throw new Error(`Не найдена зависимость «${String(dep)}» для «${task.title || id}».`);
        value = Math.max(value, level(dep) + 1);
      }
      visiting.delete(id); levels.set(id, value); return value;
    }
    rows.forEach(t => level(t.id));
    // Stable topological order places every action on the horizontal route.
    const ordered = [], placed = new Set();
    while (ordered.length < rows.length) {
      const next = rows.find(task => !placed.has(task.id) && (task.depends_on || []).every(id => placed.has(id)));
      ordered.push(next); placed.add(next.id);
    }
    const columns = new Map(ordered.map((task, index) => [task.id, index]));
    const nodes = rows.map(task => ({task, column: columns.get(task.id), row: 0, x: PAD + columns.get(task.id) * (WIDTH + GAP_X), y: PAD}));
    const baseHeight = PAD * 2 + HEIGHT + 118;
    const byId = new Map(nodes.map(n => [n.task.id, n]));
    let lanes = 0;
    const edges = rows.flatMap(t => [...new Set(t.depends_on || [])].map(from => {
      const a = byId.get(from), b = byId.get(t.id), x = a.x + WIDTH, y = a.y + HEIGHT / 2, tx = b.x, ty = b.y + HEIGHT / 2;
      if (b.column - a.column > 1) {
        const lane = baseHeight + (++lanes) * 18;
        const points = [[x,y],[x+24,y],[x+24,lane],[tx-24,lane],[tx-24,ty],[tx-8,ty]];
        return {from, to:t.id, points, path:points.map((p,i)=>`${i ? 'L' : 'M'} ${p[0]} ${p[1]}`).join(' ')};
      }
      const bend = x + (tx-x)/2;
      return {from, to:t.id, path:`M ${x} ${y} C ${bend} ${y}, ${bend} ${ty}, ${tx-8} ${ty}`};
    }));
    const orderEdges = edges.length ? [] : nodes.slice(1).map((node, index) => {
      const previous = nodes[index];
      return {from: previous.task.id, to: node.task.id, path: `M ${previous.x + WIDTH} ${previous.y + HEIGHT / 2} L ${node.x - 8} ${node.y + HEIGHT / 2}`};
    });
    return {nodes, edges, orderEdges, width: PAD * 2 + Math.max(1, rows.length) * (WIDTH + GAP_X) - GAP_X, height:baseHeight + (lanes ? lanes * 18 + PAD : 0)};
  }
  function taskStatus(task, tasks) {
    if (task.completed) return '✓ Выполнено';
    return (task.depends_on || []).some(id => !tasks.find(t => t.id === id)?.completed) ? '◷ Ждёт предыдущих действий' : '○ Можно начать';
  }
  function taskEstimate(task) {
    const estimate = Number.isFinite(task.minutes) && task.minutes > 0 ? `${task.minutes} мин` : 'Уточните оценку';
    return estimate + (Number.isFinite(task.actual_minutes) && task.actual_minutes >= 0 ? ` · факт ${task.actual_minutes} мин` : '');
  }

  function taskTiming(task, result, stale) {
    if (task.completed) return 'Выполнено';
    if (stale || !result) return 'Нужен расчёт';
    const days = (result.schedule || []).filter(day => (day.tasks || []).some(t => t.id === task.id));
    const remaining = (result.remaining_tasks || []).some(t => t.id === task.id);
    if (!days.length) return task.completed ? 'Выполнено' : 'Вне плана';
    return days.map(day => `День ${day.day}${day.date ? ` · ${day.date}` : ''}`).join(', ') + (remaining ? ' · частично' : '');
  }
  function fitScale(graph, viewportWidth, viewportHeight) {
    if (viewportWidth <= 16 || viewportHeight <= 16) return null;
    return Math.max(.01, Math.min(1.5, (viewportWidth - 16) / graph.width, (viewportHeight - 16) / graph.height));
  }
  function theoryResources(group) {
    return (group?.resources || []).filter(resource => resource && (resource.excerpt || resource.summary || resource.kind === 'reference')).sort((a, b) => Number(b.scope === 'web_theory') - Number(a.scope === 'web_theory'));
  }
  function renderTheory(container, group, task) {
    const doc = container.ownerDocument;
    const add = (tag, cls, text, parent = container) => { const node = doc.createElement(tag); node.className = cls; node.textContent = text; parent.append(node); return node; };
    const resources = theoryResources(group);
    add('h4', 'route-theory-heading', 'Теория к действию');
    if (!resources.length) {
      add('p', 'route-theory-empty', 'Связанных объяснений пока нет. Нажмите «Подобрать теорию», чтобы найти материалы по теме действия.');
      if (task?.done_when) add('p', 'route-theory-purpose', `При чтении проверьте: ${task.done_when}`);
      return;
    }
    resources.forEach(resource => {
      const article = doc.createElement('article'); article.className = 'route-theory-resource'; container.append(article);
      add('p', 'route-theory-origin', resource.kind === 'reference' ? 'Упоминание литературы · текст книги не загружен' : resource.scope === 'project' || resource.kind === 'pdf' || resource.kind === 'text' ? 'Из материалов проекта' : 'Открытый источник', article);
      add('h5', '', resource.title || 'Объяснение по теме', article);
      if (resource.scope === 'web_theory') add('p', 'route-theory-origin', `${resource.provider || 'Wikipedia'} · ${resource.author || 'Участники Википедии'} · ${resource.license || 'CC BY-SA'}. Сокращённая выдержка.`, article);
      if (resource.reference_note) add('p', 'route-theory-reference', resource.reference_note, article);
      const text = resource.summary || resource.excerpt;
      if (text) add('p', 'route-theory-text', text, article);
      if (resource.summary && resource.excerpt && resource.summary !== resource.excerpt) {
        const full = doc.createElement('details'); full.className = 'route-theory-original'; article.append(full);
        add('summary', '', 'Сохранённый фрагмент', full); add('p', 'route-theory-text', resource.excerpt, full);
      }
      const url = resource.external_url || resource.document_url || resource.url;
      let safe = null;
      try { const parsed = new URL(url, doc.defaultView?.location?.origin || 'https://navigator.invalid'); if (url && ['http:', 'https:'].includes(parsed.protocol)) safe = parsed.href; } catch (_) { /* Untrusted source URLs stay plain text. */ }
      if (safe) {
        const links = doc.createElement('details'); links.className = 'route-theory-original'; article.append(links);
        add('summary', '', 'Источник и оригинал', links);
        const link = add('a', '', resource.kind === 'reference' ? 'Подробнее об издании ↗' : 'Открыть оригинал ↗', links); link.href = safe; link.target = '_blank'; link.rel = 'noopener noreferrer';
      }
    });
  }
  function render(container, options) {
    const {tasks = [], requirements = [], result = null, stale = false, onOpenTask, projectId, theory = []} = options;
    const doc = container.ownerDocument;
    const el = (tag, cls, text) => { const node = doc.createElement(tag); if (cls) node.className = cls; if (text != null) node.textContent = String(text); return node; };
    const cached = views.get(container);
    const previous = cached && cached.projectId === projectId ? cached : {projectId};
    if (previous.viewport) { previous.left = previous.viewport.scrollLeft; previous.top = previous.viewport.scrollTop; }
    container.replaceChildren(); container.classList.add('route-map');
    let graph;
    try { graph = layoutRoute(tasks); } catch (error) { const alert = el('p', 'route-error', error.message); alert.setAttribute('role', 'alert'); container.append(alert); return {destroy:()=>container.replaceChildren()}; }
    if (!tasks.length) { container.append(el('p', 'route-empty', 'Добавьте действия, чтобы увидеть маршрут проекта.')); return {destroy:()=>container.replaceChildren()}; }
    const toolbar = el('div', 'route-toolbar'), label = el('span', 'route-help', graph.orderEdges.length ? 'Порядок работы: пунктир соединяет действия по порядку списка. Все действия можно начать независимо.' : 'Стрелки показывают зависимости. Выберите действие для подробностей.');
    const zoomLabel = el('span', 'route-zoom-value', '100%'); zoomLabel.setAttribute('aria-live','polite');
    const viewport = el('div', 'route-viewport'); viewport.style.setProperty('--route-height', `${graph.height + 16}px`); viewport.tabIndex = 0; viewport.setAttribute('role','region'); viewport.setAttribute('aria-label','Схема действий проекта. Прокручивайте для просмотра.');
    const space = el('div','route-space'), canvas = el('div','route-canvas'); canvas.style.width = `${graph.width}px`; canvas.style.height = `${graph.height}px`;
    const svg = doc.createElementNS('http://www.w3.org/2000/svg','svg'); svg.setAttribute('width', graph.width); svg.setAttribute('height',graph.height); svg.setAttribute('aria-hidden','true'); svg.classList.add('route-edges');
    const byId = new Map(graph.nodes.map(n=>[n.task.id,n]));
    graph.orderEdges.forEach(edge => {
      const path = doc.createElementNS(svg.namespaceURI, 'path');
      path.setAttribute('d', edge.path); path.classList.add('route-order-edge'); svg.append(path);
    });
    graph.edges.forEach(edge=>{ const b=byId.get(edge.to), tx=b.x,ty=b.y+HEIGHT/2; const path=doc.createElementNS(svg.namespaceURI,'path'); path.setAttribute('d',edge.path); const arrow=doc.createElementNS(svg.namespaceURI,'path'); arrow.setAttribute('d',`M ${tx-13} ${ty-5} L ${tx-5} ${ty} L ${tx-13} ${ty+5}`); svg.append(path,arrow); }); canvas.append(svg);
    const details=el('section','route-details route-map-details'); details.hidden=true; details.setAttribute('aria-label','Подробности действия');
    function show(task, button, focus = true, theoryFocus = false) {
      previous.selected = task.id;
      canvas.querySelectorAll('.route-node').forEach(n=>n.setAttribute('aria-pressed',String(n===button)));
      details.replaceChildren(); details.hidden=false;
      const title=el('h3','',task.title); title.tabIndex=-1; details.append(title);
      const field=(name,value)=>{ const p=el('p','route-detail-field'); p.append(el('strong','',`${name}: `),doc.createTextNode(value || 'Не указано')); details.append(p); };
      field('Когда',taskTiming(task,result,stale)); field('Результат',task.expected_result); field('Готово, когда',task.done_when);
      const links=(task.requirement_ids || []).map(id=>{const req=requirements.find(r=>r.id===id);return req ? (req.title || req.text || req.description || id) : `${id} (требование не найдено)`;});
      field('Зачем', links.join('; ')); field('Основание оценки', task.estimate_basis); field('Зависит от', (task.depends_on || []).map(id=>byId.get(id).task.title).join('; ') || 'Можно начать независимо');
      const reading=el('section','route-theory-reading'); renderTheory(reading,theory.find(group=>group.task_id===task.id),task); details.append(reading);
      if (typeof onOpenTask==='function') { const open=el('button','route-open button button-secondary','Открыть действие'); open.type='button'; open.addEventListener('click',()=>onOpenTask(task)); details.append(open); }
      if (focus) {
        const reduced = doc.defaultView?.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
        details.scrollIntoView({block:'nearest',behavior:reduced ? 'instant' : 'smooth'});
        const focusTarget=theoryFocus ? reading.querySelector('h4') : title; focusTarget.tabIndex=-1; focusTarget.focus({preventScroll:true});
      }
    }
    graph.nodes.forEach(({task,x,y})=>{ const button=el('button',`route-node route-map-node${task.completed ? ' is-complete' : ''}`); button.type='button'; button.style.left=`${x}px`;button.style.top=`${y}px`;button.setAttribute('aria-pressed','false');button.append(el('span','route-node-status',taskStatus(task,tasks)),el('strong','route-node-title',task.title),el('span','route-node-time',taskEstimate(task)),el('span','route-node-day',taskTiming(task,result,stale)));button.addEventListener('click',()=>show(task,button));canvas.append(button);
      const group=theory.find(item=>item.task_id===task.id), resources=theoryResources(group);
      const note=el('button','route-theory-note'); note.type='button'; note.style.left=`${x}px`; note.style.top=`${y+HEIGHT+14}px`;
      note.setAttribute('aria-label',`Теория к действию «${task.title}»`);
      note.append(el('span','route-theory-note-label','Теория и объяснения'),el('strong','route-theory-note-title',resources[0]?.title || 'Что изучить для этого шага'),el('span','route-theory-note-open',resources.length ? `Читать · ${resources.length} ${resources.length === 1 ? 'источник' : resources.length < 5 ? 'источника' : 'источников'}` : 'Открыть подборку →'));
      note.addEventListener('click',()=>show(task,button,true,true)); canvas.append(note);
    });
    let zoom=previous.zoom || 1;
    function setZoom(value, minimum=.01){zoom=Math.max(minimum,Math.min(1.5,value));previous.zoom=zoom;canvas.style.transform=`scale(${zoom})`;space.style.width=`${graph.width*zoom}px`;space.style.height=`${graph.height*zoom}px`;zoomLabel.textContent=`${Math.round(zoom*100)}%`;}
    [['−','Уменьшить',()=>setZoom(zoom-.15,.1)],['+','Увеличить',()=>setZoom(zoom+.15,.1)],['100%','Масштаб 100%',()=>setZoom(1)],['Вместить','Вместить схему',()=>{const fit=fitScale(graph,viewport.clientWidth,viewport.clientHeight);if(fit!==null)setZoom(fit);}]].forEach(([text,aria,handler])=>{const b=el('button','route-zoom',text);b.type='button';b.setAttribute('aria-label',aria);b.addEventListener('click',handler);toolbar.append(b);});
    toolbar.append(zoomLabel);space.append(canvas);viewport.append(space);container.append(label,toolbar,viewport,details);setZoom(zoom);
    previous.viewport=viewport; views.set(container,previous); viewport.scrollLeft=previous.left || 0; viewport.scrollTop=previous.top || 0;
    viewport.addEventListener('scroll',()=>{previous.left=viewport.scrollLeft;previous.top=viewport.scrollTop;},{passive:true});
    const selected = graph.nodes.find(n=>n.task.id===previous.selected);
    if(selected) show(selected.task,canvas.querySelectorAll('.route-node')[graph.nodes.indexOf(selected)],false);
    return {destroy:()=>container.replaceChildren()};
  }
  const api={layoutRoute,taskTiming,taskStatus,taskEstimate,fitScale,theoryResources,renderTheory,render};
  if (typeof module !== 'undefined' && module.exports) module.exports=api;
  else root.NavigatorRouteMap=api;
})(typeof window !== 'undefined' ? window : globalThis);
