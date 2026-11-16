'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const demo = require('../../tools/demo-runtime.js');
const tasks = [45, 200, 90, 60, 75].map((minutes, i) => ({ id: `t${i}`, title: `Action ${i}`, minutes, expected_result: 'Artifact', done_when: 'Verified', estimate_basis: 'Work volume', requirement_ids: ['r'], depends_on: i ? [`t${i - 1}`] : [] }));
const availability = [90, 0, 120, 120, 120].map((minutes, i) => ({ day: i + 1, minutes }));
const card = { title: 'Example', goal: { text: 'Goal' }, deliverable: { text: 'Report' }, requirements: [{ id: 'r', text: 'Report requirement' }], deadline: { date: null }, tasks };
function seed() { global.NAVIGATOR_DEMO_SEED = { assignments: [{ assignment_id: 'upload-example', is_uploaded: true, title: 'Example', status: 'ready', card_revision: 1, card, sources: [{ source_id: 's', title: 'Brief', excerpt: 'Report requirement', kind: 'text' }] }], plans: {} }; demo.reset(); }
test('reference effort, busy day and deficit; dependency order and coverage', () => {
  const p = demo.computePlan([...tasks].reverse(), availability, null, 0, card);
  assert.equal(p.required_minutes, 470); assert.equal(p.available_minutes, 450); assert.equal(p.deficit_minutes, 20);
  assert.deepEqual(p.schedule[1].tasks, []); assert.equal(p.schedule[0].tasks[0].id, 't0'); assert.equal(p.coverage.complete_check_count, 1);
});
test('reserve rounds each day, deadline removes later days; actual does not subtract remaining work', () => {
  const p = demo.computePlan(tasks.map((t, i) => ({ ...t, completed: i === 0, actual_minutes: i === 1 ? 199 : null })), availability, '2026-11-09', 10, { ...card, deadline: { date: '2026-11-11' } });
  assert.equal(p.required_minutes, 425); assert.equal(p.available_minutes, 189); assert.equal(p.completed_task_count, 1); assert.equal(p.schedule[3].tasks.length, 0);
  assert.ok(p.warnings.some(w => w.code === 'deadline_limits_availability'));
});
test('reject duplicates, cycles, invalid minutes and invalid days', () => {
  for (const bad of [[tasks[0], tasks[0]], [{ ...tasks[0], depends_on: ['t0'] }], [{ ...tasks[0], minutes: 0 }]]) assert.throws(() => demo.computePlan(bad, availability), { code: 'validation_error' });
  assert.throws(() => demo.computePlan(tasks, [{ day: 2, minutes: 60 }]), { code: 'validation_error' });
});
test('save progress, confirm card marks plan stale; returned data cannot mutate seed', async () => {
  seed();
  const saved = await demo.request('/api/assignments/upload-example/plan', { method: 'PUT', body: JSON.stringify({ revision: 0, card_revision: 1, tasks: tasks.map((t, i) => ({ ...t, completed: i === 0, actual_minutes: i === 0 ? 50 : null })), availability, reserve_percent: 0 }) });
  assert.equal(saved.plan.result.required_minutes, 425);
  saved.plan.input.tasks[0].title = 'mutated';
  assert.equal((await demo.request('/api/assignments/upload-example/plan')).plan.input.tasks[0].title, 'Action 0');
  await demo.request('/api/assignments/upload-example/confirm', { method: 'POST', body: JSON.stringify({ revision: 1, card }) });
  assert.equal((await demo.request('/api/assignments/upload-example/plan')).plan.stale, true);
  await assert.rejects(demo.request('/api/assignments/upload-example/confirm', { method: 'POST', body: JSON.stringify({ revision: 1, card }) }), { status: 409 });
});
test('text and deterministic context summary remain offline; unsupported services reject', async () => {
  seed();
  const created = await demo.request('/api/assignments/from-text', { method: 'POST', body: JSON.stringify({ title: 'Personal', text: '<script>sample</script>' }) });
  assert.equal(created.assignment.card.deadline.date, null); assert.equal(created.assignment.status, 'materials_only');
  const reply = await demo.request('/api/chat', { method: 'POST', body: JSON.stringify({ assignment_id: 'upload-example', message: 'Invent a deadline' }) });
  assert.equal(reply.mode, 'offline'); assert.equal(reply.answer_basis, 'project_context'); assert.match(reply.answer, /Срок неизвестен/); assert.match(reply.answer, /Action 0/); assert.equal(reply.sources[0].url, '#demo-source-s');
  assert.equal((await demo.request('/api/assignments/upload-example/materials?q=unrelated')).matches.length, 0);
  await assert.rejects(demo.request('/api/assignments/upload-example/analyze', { method: 'POST' }), { code: 'demo_unavailable' });
  await assert.rejects(demo.request('/api/assignments', { method: 'POST' }), { code: 'demo_unavailable' });
  demo.reset(); assert.equal((await demo.request('/api/assignments')).assignments.length, 1);
});

test('materials, sources, analytics and explicit proposal application stay local', async () => {
  seed();
  await demo.request('/api/assignments/upload-example/materials',{method:'POST',body:JSON.stringify({title:'Extra',text:'Evidence',external_url:'https://example.org'})});
  const source=(await demo.request('/api/sources/upload-example-material-2')).source;
  assert.equal(source.preview,'Evidence'); assert.equal(source.external_url,'https://example.org');
  await assert.rejects(demo.request('/api/assignments/upload-example/materials',{method:'POST',body:{}}),{code:'demo_unavailable'});
  await demo.request('/api/assignments/upload-example/plan',{method:'PUT',body:JSON.stringify({revision:0,card_revision:1,tasks:tasks.map(t=>({...t,completed:false})),availability})});
  const baseline=(await demo.request('/api/assignments/upload-example/analytics')).analytics;
  assert.equal(baseline.totals.actual_minutes,null);assert.equal(baseline.totals.actual_unknown_count,5);assert.equal(baseline.history[0].baseline,true);assert.equal(baseline.history[0].actual_delta_minutes,null);
  const response=await demo.request('/api/chat',{method:'POST',body:JSON.stringify({assignment_id:'upload-example',message:'отметь Action 0 выполненным'})});
  assert.equal(response.action_proposal.operations[0].task_id,'t0');
  assert.equal((await demo.request('/api/assignments/upload-example/plan')).plan.input.tasks[0].completed,false);
  const p=response.action_proposal;
  await assert.rejects(demo.request('/api/assignments/upload-example/assistant-actions',{method:'POST',body:JSON.stringify({...p,apply:false})}),{code:'apply_required'});
  const result=await demo.request('/api/assignments/upload-example/assistant-actions',{method:'POST',body:JSON.stringify({...p,apply:true})});
  assert.equal(result.plan.input.tasks[0].completed,true);
  const analytics=(await demo.request('/api/assignments/upload-example/analytics')).analytics;
  assert.equal(analytics.totals.completed_count,1); assert.equal(analytics.history.length,2);
  await assert.rejects(demo.request('/api/assignments/upload-example/assistant-actions',{method:'POST',body:JSON.stringify({...p,apply:true})}),{status:404});
});

test('authored steps retain exact effort across split days and parent estimate edits', () => {
  const t = {...tasks[0], minutes: 30, substeps: [10, 15, 5].map((minutes,i)=>({id:`s${i}`,title:`Concrete ${i}`,minutes,done_when:`Artifact ${i} exists`}))};
  const p = demo.computePlan([t], [12,0,18].map((minutes,i)=>({day:i+1,minutes})));
  assert.deepEqual(p.schedule[1].tasks, []);
  assert.equal(p.schedule[0].tasks[0].substeps.reduce((n,s)=>n+s.minutes,0),12);
  assert.equal(p.schedule[2].tasks[0].substeps.reduce((n,s)=>n+s.minutes,0),18);
  assert.equal(p.schedule[0].tasks[0].substeps[1].continues,true);
  assert.equal(p.schedule[2].tasks[0].substeps[0].continued,true);
  assert.equal(p.schedule[2].tasks[0].substeps[0].total_minutes,15);
  const edited = demo.computePlan([{...t,minutes:17}],[{day:1,minutes:17}]);
  assert.deepEqual(edited.tasks[0].substeps.map(s=>s.minutes),[6,8,3]);
  assert.throws(()=>demo.computePlan([{...t,minutes:2}],[{day:1,minutes:20}]),{code:'validation_error'});
  assert.throws(()=>demo.computePlan([{...t,substeps:[t.substeps[0],t.substeps[0]]}],[{day:1,minutes:20}]),{code:'validation_error'});
  const limited=demo.computePlan([t],[{day:1,minutes:12}]);
  assert.equal(limited.remaining_tasks[0].substeps.reduce((n,s)=>n+s.minutes,0),18);
});
