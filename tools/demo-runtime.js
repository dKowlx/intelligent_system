/* Standalone demonstration adapter. No network, storage, model or private database. */
(function (root) {
  'use strict';
  const clone = (value) => JSON.parse(JSON.stringify(value));
  function fail(message, code = 'validation_error', status = 422) { throw Object.assign(new Error(message), { code, status }); }
  function integer(value, min, max) { return Number.isInteger(value) && value >= min && value <= max; }
  function date(value) {
    if (value == null || value === '') return null;
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(Date.parse(value + 'T12:00:00Z')) || new Date(value + 'T12:00:00Z').toISOString().slice(0, 10) !== value) fail('Укажите существующую дату ГГГГ-ММ-ДД.');
    return value;
  }
  function dayDate(start, offset) { const d = new Date(start + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + offset); return d.toISOString().slice(0, 10); }
  function apportion(steps, minutes) {
    if (!steps.length) return [];
    if (minutes < steps.length) fail('Оценка действия должна оставлять хотя бы минуту на каждый шаг.');
    const total = steps.reduce((n, s) => n + s.minutes, 0);
    if (total === minutes) return steps;
    const budget = minutes - steps.length, shares = steps.map(s => budget * s.minutes / total);
    const allocated = shares.map(s => 1 + Math.floor(s));
    const order = steps.map((_, i) => i).sort((a, b) => (shares[b] % 1) - (shares[a] % 1) || a - b);
    for (const i of order.slice(0, minutes - allocated.reduce((a, b) => a + b, 0))) allocated[i]++;
    return steps.map((s, i) => ({...s, minutes: allocated[i]}));
  }
  function taskSlice(task, offset, minutes) {
    let position = 0; const substeps = [];
    for (const step of task.substeps || []) {
      const end = position + step.minutes, overlap = Math.min(end, offset + minutes) - Math.max(position, offset);
      if (overlap > 0) substeps.push({...step, minutes: overlap, total_minutes: step.minutes, continued: offset > position, continues: offset + minutes < end});
      position = end;
    }
    return {...task, minutes, substeps};
  }
  function normalize(tasks) {
    if (!Array.isArray(tasks) || tasks.length < 1 || tasks.length > 20) fail('Добавьте от 1 до 20 действий.');
    const ids = new Set();
    const normalized = tasks.map((task, i) => {
      const t = { requirement_ids: [], depends_on: [], source_ids: [], expected_result: '', done_when: '', estimate_basis: '', estimate_source: 'user', priority: 'medium', priority_reason: '', completed: false, actual_minutes: null, substeps: [], ...clone(task), id: task.id || `task-${i + 1}` };
      if (typeof t.id !== 'string' || !/^[A-Za-z0-9_-]{1,100}$/.test(t.id) || ids.has(t.id)) fail('Идентификаторы действий должны быть уникальными.');
      ids.add(t.id);
      if (typeof t.title !== 'string' || !t.title.trim() || t.title.length > 200 || !integer(t.minutes, 1, 4800)) fail('Нужны название действия и целая оценка от 1 до 4800 минут.');
      if (typeof t.completed !== 'boolean' || (t.actual_minutes != null && !integer(t.actual_minutes, 0, 100000))) fail('Проверьте прогресс и фактическое время.');
      for (const key of ['requirement_ids', 'depends_on', 'source_ids']) if (!Array.isArray(t[key]) || t[key].some(x => typeof x !== 'string')) fail('Некорректные связи действия.');
      if (!Array.isArray(t.substeps) || t.substeps.length > 8 || new Set(t.substeps.map(s => s?.id)).size !== t.substeps.length || t.substeps.some(s => !s || typeof s.id !== 'string' || !/^[A-Za-z0-9_-]{1,100}$/.test(s.id) || typeof s.title !== 'string' || !s.title.trim() || s.title.length > 200 || typeof s.done_when !== 'string' || !s.done_when.trim() || s.done_when.length > 1000 || !integer(s.minutes, 1, 4800))) fail('Проверьте шаги действия: уникальные ID, название, критерий и положительные минуты.');
      t.substeps = apportion(t.substeps, t.minutes);
      return t;
    });
    const map = new Map(normalized.map(t => [t.id, t])), active = new Set(), visited = new Set(), ordered = [];
    function visit(id) { if (active.has(id)) fail('В зависимостях действий найден цикл.'); if (visited.has(id)) return; if (!map.has(id)) fail('Указана неизвестная зависимость.'); active.add(id); for (const ref of map.get(id).depends_on) visit(ref); active.delete(id); visited.add(id); ordered.push(map.get(id)); }
    normalized.forEach(t => visit(t.id));
    return { normalized, ordered, map };
  }
  function computePlan(tasks, availability, startDate = null, reserve = 0, card = null) {
    const { normalized, ordered, map } = normalize(tasks);
    if (!Array.isArray(availability) || availability.length < 1 || availability.length > 30 || availability.some((d, i) => d.day !== i + 1 || !integer(d.minutes, 0, 480))) fail('Укажите подряд от 1 до 30 дней, от 0 до 480 минут в день.');
    if (!integer(reserve, 0, 100)) fail('Резерв должен быть целым числом от 0 до 100%.');
    const start = date(startDate), deadline = date(card?.deadline?.date), warnings = [];
    const warn = (code, message, extra = {}) => warnings.push({ code, message, ...extra });
    normalized.forEach(t => {
      if (!t.expected_result || !t.done_when) warn('missing_result', 'Укажите результат действия и проверяемый признак готовности.', { task_id: t.id });
      if (!t.estimate_basis) warn('missing_estimate_basis', 'Оценка времени не имеет объяснения объёма работы.', { task_id: t.id });
      if (t.completed && t.depends_on.some(id => !map.get(id).completed)) warn('inconsistent_completion', 'Завершённое действие имеет незавершённую предпосылку.', { task_id: t.id });
    });
    const requirements = card?.requirements || [], known = new Set(requirements.map((r, i) => r.id || `req-${i + 1}`));
    normalized.forEach(t => { if (card && t.requirement_ids.some(id => !known.has(id))) fail('Действие ссылается на неизвестное требование.'); if (card && !t.requirement_ids.length) warn('unlinked_task', 'Действие не связано с требованием.', { task_id: t.id }); });
    const items = [...known].map(id => { const linked = normalized.filter(t => t.requirement_ids.includes(id)); if (!linked.length) warn('uncovered_requirement', 'Для требования нет связанного действия.', { requirement_id: id }); return { requirement_id: id, task_ids: linked.map(t => t.id), linked: !!linked.length, complete_check: linked.some(t => t.expected_result && t.done_when && t.estimate_basis && !/^(выполнить:|подготовить:|проверить результат:)/i.test(t.title)) }; });
    const budgets = availability.map((d, i) => deadline && start && dayDate(start, i) > deadline ? 0 : Math.floor(d.minutes * (100 - reserve) / 100));
    if (deadline && !start) warn('deadline_not_checked', 'Для проверки известного срока укажите дату начала.');
    if (deadline && start && dayDate(start, availability.length - 1) > deadline) warn('deadline_limits_availability', 'Доступное время после известного срока исключено из расчёта.');
    const pending = ordered.filter(t => !t.completed), remaining = pending.map(t => t.minutes); let cursor = 0;
    const schedule = availability.map((d, i) => { let budget = budgets[i]; const slots = []; while (cursor < pending.length && budget) { const take = Math.min(remaining[cursor], budget); slots.push(taskSlice(pending[cursor], pending[cursor].minutes - remaining[cursor], take)); remaining[cursor] -= take; budget -= take; if (!remaining[cursor]) cursor++; } return { day: d.day, date: start ? dayDate(start, i) : null, tasks: slots }; });
    const required = pending.reduce((n, t) => n + t.minutes, 0), available = budgets.reduce((a, b) => a + b, 0);
    if (required > available) warn('insufficient_time', 'Работа не помещается: увеличьте доступность или явно пересмотрите объём.');
    return { assignment_id: 'personal-project', status: required > available ? 'insufficient_time' : 'feasible', available_minutes: available, raw_available_minutes: availability.reduce((n, d) => n + d.minutes, 0), required_minutes: required, deficit_minutes: Math.max(0, required - available), reserve_percent: reserve, tasks: normalized, schedule, warnings, completed_task_count: normalized.filter(t => t.completed).length, completed_minutes: normalized.filter(t => t.completed).reduce((n, t) => n + t.minutes, 0), remaining_tasks: pending.map((t, i) => taskSlice(t, t.minutes - remaining[i], remaining[i])).filter(t => t.minutes), coverage: { requirements_total: items.length, linked_count: items.filter(i => i.linked).length, complete_check_count: items.filter(i => i.complete_check).length, items, linked_requirement_ids: items.filter(i => i.linked).map(i => i.requirement_id), uncovered_requirement_ids: items.filter(i => !i.linked).map(i => i.requirement_id) }, assumptions: ['Минуты — текущая оценка оставшейся работы; фактическое время само по себе не означает завершение.', 'Связь с требованием не доказывает качество результата.', 'Резерв вычитается из каждого дня с округлением вниз.'] };
  }
  function totals(tasks) {
    const actuals=tasks.filter(t=>t.actual_minutes != null).map(t=>t.actual_minutes), recorded=actuals.reduce((a,b)=>a+b,0);
    return {estimated_minutes:tasks.reduce((n,t)=>n+t.minutes,0),actual_minutes:actuals.length ? recorded : null,recorded_actual_minutes:recorded,actual_known_count:actuals.length,actual_unknown_count:tasks.length-actuals.length,completed_count:tasks.filter(t=>t.completed).length,task_count:tasks.length};
  }
  function moscowDate(timestamp) { return new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(timestamp)); }
  let database;
  function reset() { database = clone(root.NAVIGATOR_DEMO_SEED || { assignments: [], plans: {} }); database.plans ||= {}; database.history ||= {}; database.proposals ||= {}; }
  function assignment(id) { const a = database.assignments.find(a => a.assignment_id === id); if (!a) fail('Проект не найден.', 'not_found', 404); return a; }
  function sources(a) { return (a.sources || []).map((s, i) => ({ ...s, source_id: s.source_id || s.id || `${a.assignment_id}-${i + 1}`, url: '#demo-source-' + (s.source_id || s.id || `${a.assignment_id}-${i + 1}`) })); }
  function detail(a) { return { ...a, sources: sources(a), documents: a.documents || [] }; }
  function conflict(actual, submitted) { if ((actual || 0) !== (submitted || 0)) fail('Версия изменилась. Откройте проект заново.', 'revision_conflict', 409); }
  async function request(path, options = {}) {
    if (!database) reset();
    if (options.signal?.aborted) fail('Запрос отменён.', 'cancelled', 499);
    const method = (options.method || 'GET').toUpperCase(), url = new URL(path, 'https://demo.invalid'); let body = {};
    if (options.body && typeof options.body === 'string') { try { body = JSON.parse(options.body); } catch (_) { fail('Некорректные данные запроса.'); } }
    if (url.pathname === '/api/status') return { mode: 'offline', live_configured: false, demo: true, model: null };
    if (url.pathname === '/api/assignments/from-text' && method === 'POST') {
      if (typeof body.title !== 'string' || !body.title.trim() || body.title.length > 200 || typeof body.text !== 'string' || !body.text.trim() || body.text.length > 20000) fail('Укажите название и описание проекта (до 20 000 символов).');
      let n = 1; while (database.assignments.some(a => a.assignment_id === `upload-demo-${n}`)) n++;
      const id = `upload-demo-${n}`, field = text => ({ text, origin: 'user', source_ids: [] });
      const a = { assignment_id: id, title: body.title.trim(), description: body.text, is_uploaded: true, status: 'materials_only', card_revision: 0, due_date: null, documents: [], sources: [{ source_id: id + '-text', title: body.title.trim(), kind: 'text', section: 'Описание проекта', excerpt: body.text }], card: { title: body.title.trim(), goal: field(body.text), deliverable: field(''), requirements: [], criteria: [], tasks: [], questions: [], deadline: { date: null, origin: 'unknown', source_ids: [] } } };
      database.assignments.push(a); return clone({ assignment: detail(a) });
    }
    if (url.pathname === '/api/assignments') { if (method === 'GET') return clone({ assignments: database.assignments.map(detail) }); fail('В автономном демо загрузка и анализ PDF недоступны. Создайте проект из текста.', 'demo_unavailable', 503); }
    if (url.pathname === '/api/chat' && method === 'POST') {
      const a = assignment(body.assignment_id), card = a.card || {}, steps = database.plans[a.assignment_id]?.input?.tasks || card.tasks || [], pending = steps.filter(t => !t.completed), next = pending.find(t => !(t.depends_on || []).some(id => pending.some(p => p.id === id)));
      if (typeof body.message !== 'string' || !body.message.trim() || body.message.length > 2000) fail('Введите вопрос длиной до 2000 символов.');
      const normalizedMessage = body.message.toLocaleLowerCase().replace(/[«»"'.,!?]/g, '').trim();
      const requested = normalizedMessage.replace(/^отметь\s+/, '').replace(/\s+выполненн(?:ым|ой|ое|ую)$/, '').trim();
      const target = /^отметь\s/.test(normalizedMessage) && /выполненн/.test(normalizedMessage) ? steps.find(t => t.title.toLocaleLowerCase() === requested) : null;
      if (target) {
        const proposal_id = `proposal-${Object.keys(database.proposals).length + 1}`;
        const action_proposal = {proposal_id,summary:`Отметить «${target.title}» выполненным`,operations:[{op:'complete',task_id:target.id,changes:{}}],card_revision:a.card_revision,plan_revision:database.plans[a.assignment_id]?.revision || 0};
        database.proposals[proposal_id] = {assignment_id:a.assignment_id,...action_proposal};
        return clone({mode:'offline',status:'ok',answer:'Предлагаю отметить выбранное действие выполненным. Проверьте изменение и нажмите кнопку применения.',answer_basis:'project_context',sources:[],trace:[],action_proposal});
      }
      const answer = [ `Проект: ${a.title}.`, card.deliverable?.text ? `Результат: ${card.deliverable.text}` : 'Итоговый результат пока не указан.', next ? `Ближайшее действие: ${next.title}. ${next.expected_result || ''}${next.done_when ? ' Готово, когда: ' + next.done_when : ''}` : 'Незавершённые действия не заданы.', card.deadline?.date ? `Срок в карточке: ${card.deadline.date}.` : 'Срок неизвестен.', 'Источники ниже — материалы выбранного проекта; сверьте с ними карточку.'].join('\n\n');
      return clone({ mode: 'offline', status: 'ok', answer, answer_basis: 'project_context', sources: sources(a), trace: [{ name: 'Чтение карточки проекта', status: 'ok' }] });
    }
    if (url.pathname.startsWith('/api/sources/') && method === 'GET') {
      const id = decodeURIComponent(url.pathname.split('/').pop());
      const source = database.assignments.flatMap(sources).find(s => s.source_id === id);
      if (!source) fail('Источник не найден.', 'not_found', 404);
      return clone({ source: { ...source, summary: source.summary || source.excerpt, preview: source.preview || source.excerpt, external_url: source.external_url || null, file_url: null } });
    }
    const match = url.pathname.match(/^\/api\/assignments\/([^/]+)(?:\/(plan|confirm|materials|analyze|analytics|assistant-actions))?$/);
    if (!match) fail('Эта функция недоступна в автономном демо.', 'demo_unavailable', 503);
    const a = assignment(decodeURIComponent(match[1])), action = match[2], id = a.assignment_id;
    if (action === 'assistant-actions' && method === 'POST') {
      if (body.apply !== true) fail('Применение требует явного подтверждения.', 'apply_required');
      const proposal = database.proposals[body.proposal_id];
      if (!proposal || proposal.assignment_id !== id) fail('Предложение не найдено.', 'not_found',404);
      conflict(a.card_revision,body.card_revision); conflict(database.plans[id]?.revision,body.plan_revision);
      conflict(a.card_revision,proposal.card_revision); conflict(database.plans[id]?.revision,proposal.plan_revision);
      const previous = database.plans[id];
      if (!previous) fail('Сначала сохраните план.');
      const tasks = clone(previous.input.tasks);
      proposal.operations.forEach(op=>{const task=tasks.find(t=>t.id===op.task_id);if(!task) fail('Действие не найдено.');task.completed=true;});
      const saved = await request(`/api/assignments/${id}/plan`,{method:'PUT',body:JSON.stringify({...previous.input,tasks,revision:previous.revision,card_revision:a.card_revision})});
      delete database.proposals[body.proposal_id]; return clone({assignment:detail(a),plan:saved.plan});
    }
    if (action === 'analytics') {
      const tasks = database.plans[id]?.input?.tasks || a.card?.tasks || [];
      const history = database.history[id] || [];
      return clone({ analytics: { recorded_since: history[0]?.recorded_at || null,
        timezone:'Europe/Moscow',totals: totals(tasks),
        tasks: tasks.map(t=>({task_id:t.id,title:t.title,estimated_minutes:t.minutes,actual_minutes:t.actual_minutes,completed:!!t.completed,delta_minutes:t.actual_minutes == null ? null : t.actual_minutes-t.minutes})), history,history_note: history.length ? null : "История появится после сохранения плана.",has_history:history.length>0 } });
    }
    if (action === 'materials' && method === 'POST') {
      if (options.body && typeof options.body !== 'string') fail('В автономном файле PDF не обрабатывается. Добавьте текст и при необходимости ссылку.', 'demo_unavailable', 503);
      if (typeof body.text !== 'string' || !body.text.trim()) fail('Добавьте текст материала.');
      if (body.external_url && !/^https?:\/\//i.test(body.external_url)) fail('Укажите ссылку HTTP или HTTPS.');
      const source = {source_id:`${id}-material-${(a.sources || []).length+1}`,title:body.title?.trim() || 'Материал',kind:'text',section:'Добавленный материал',excerpt:body.text,summary:body.text.slice(0,400),preview:body.text,external_url:body.external_url || null,file_url:null};
      a.sources ||= []; a.sources.push(source); a.documents ||= []; a.documents.push({title:source.title,kind:'text',character_count:body.text.length});
      return clone({assignment:detail(a)});
    }
    if (action === 'analyze') fail('AI-анализ недоступен в автономном демо. Заполните карточку вручную.', 'demo_unavailable', 503);
    if (action === 'materials' && method === 'GET') { const words = (url.searchParams.get('q') || '').toLocaleLowerCase().split(/\s+/).filter(Boolean); return clone({ matches: sources(a).filter(s => words.every(w => `${s.title} ${s.excerpt || ''} ${s.section || ''}`.toLocaleLowerCase().includes(w))) }); }
    if (!action && method === 'GET') return clone({ assignment: detail(a) });
    if (!action && method === 'DELETE') { database.assignments = database.assignments.filter(x => x !== a); delete database.plans[id]; return {}; }
    if (action === 'confirm' && method === 'POST') {
      conflict(a.card_revision, body.revision); const card = clone(body.card || {});
      if (!card.goal?.text?.trim() || !card.deliverable?.text?.trim()) fail('Укажите цель и итоговый результат.');
      date(card.deadline?.date); if (card.tasks?.length) normalize(card.tasks);
      const requirements = card.requirements || [], ids = requirements.map(r => r.id); if (new Set(ids).size !== ids.length || ids.some(id => typeof id !== 'string' || !id)) fail('Требования должны иметь уникальные идентификаторы.');
      if (card.tasks?.some(t => (t.requirement_ids || []).some(ref => !ids.includes(ref)))) fail('Действие ссылается на неизвестное требование.');
      if (!card.tasks?.length) {
        card.tasks = requirements.map((r, i) => ({ id: `task-${i + 1}`, title: ('Выполнить: ' + r.text).slice(0, 200), minutes: 60, estimate_source: 'estimate', requirement_ids: [r.id] }));
        if (!card.tasks.length) card.tasks.push({ id: 'task-1', title: ('Подготовить: ' + card.goal.text).slice(0, 200), minutes: 60, estimate_source: 'estimate', requirement_ids: [] });
        card.tasks.push({ id: `task-${card.tasks.length + 1}`, title: ('Проверить результат: ' + card.deliverable.text).slice(0, 200), minutes: 30, estimate_source: 'estimate', requirement_ids: [] });
        card.tasks = normalize(card.tasks).normalized;
      }
      a.card = card; a.title = card.title || a.title; a.status = 'ready'; a.card_revision = (a.card_revision || 0) + 1; a.due_date = card.deadline?.date || null;
      if (database.plans[id]) database.plans[id].stale = true;
      return clone({ assignment: detail(a) });
    }
    if (action === 'plan' && method === 'GET') return clone({ plan: database.plans[id] || null });
    if (action === 'plan' && method === 'PUT') {
      if (a.status !== 'ready') fail('Сначала подтвердите карточку.');
      conflict(a.card_revision, body.card_revision); conflict(database.plans[id]?.revision, body.revision);
      let base = a.card?.tasks || [];
      if (!base.length) base = (a.card.requirements || []).map((r, i) => ({ id: `task-${i + 1}`, title: ('Выполнить: ' + r.text).slice(0, 200), minutes: 60, requirement_ids: [r.id] }));
      if (!Array.isArray(body.tasks) || body.tasks.length !== base.length || new Set(body.tasks.map(t => t.id)).size !== base.length || base.some(t => !body.tasks.some(x => x.id === t.id))) fail('Передайте все действия подтверждённой карточки.');
      const tasks = base.map(t => { const update = body.tasks.find(x => x.id === t.id); return { ...t, title: update.title, minutes: update.minutes, completed: update.completed, actual_minutes: update.actual_minutes, priority: update.priority || t.priority || 'medium' }; });
      const result = computePlan(tasks, body.availability, body.start_date, body.reserve_percent || 0, a.card); result.assignment_id = id;
      const plan = { revision: (database.plans[id]?.revision || 0) + 1, card_revision: a.card_revision, stale: false, input: { tasks: result.tasks, availability: clone(body.availability), start_date: body.start_date || null, reserve_percent: body.reserve_percent || 0 }, result };
      const prior=database.history[id]?.at(-1), summary=totals(result.tasks), recorded_at=new Date().toISOString();
      const actual_delta=prior ? summary.recorded_actual_minutes-(prior.recorded_actual_minutes || 0) : null, completed_delta=prior ? summary.completed_count-prior.completed_count : null;
      database.history[id] ||= [];
      database.history[id].push({revision:plan.revision,recorded_at,date:moscowDate(recorded_at),...summary,baseline:!prior,actual_delta_minutes:actual_delta,completed_delta_count:completed_delta,correction:actual_delta<0 || completed_delta<0});
      database.plans[id] = plan; return clone({ plan });
    }
    fail('Эта операция недоступна в демо.', 'demo_unavailable', 503);
  }
  const api = { request, computePlan, reset };
  root.NavigatorDemo = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
