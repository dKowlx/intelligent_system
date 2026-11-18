const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('web/codex-connection.js', 'utf8');
function setup(fetcher) {
  const elements = {}, events = {}, calls = [];
  function el() { return { value: '', hidden: false, open: false, children: [], events: {}, textContent: '', addEventListener(name, fn) { this.events[name] = fn; }, append(...items) { this.children.push(...items); }, replaceChildren(...items) { this.children = items; }, reportValidity() { return true; } }; }
  const document = { visibilityState: 'visible', hasFocus: () => true, getElementById(id) { return elements[id] ||= el(); }, createElement: el };
  for (const id of ['planning', 'codex-days', 'codex-label']) document.getElementById(id);
  const window = { addEventListener(name, fn) { events[name] = fn; }, dispatchEvent(event) { calls.push(event); } };
  const fetch = async (url, options) => { calls.push({ url, options }); return { ok: true, json: () => fetcher(url, options) }; };
  vm.runInNewContext(source, { document, window, fetch, navigator: { clipboard: { writeText: async () => {} } }, setInterval(fn) { events.tick = fn; }, CustomEvent: function(type, options) { this.type = type; this.detail = options.detail; } });
  return { elements, events, calls, window, project: value => window.NavigatorCodex.setProject({ id: 'a', ready: true, ...value }) };
}
const settle = () => new Promise(resolve => setImmediate(resolve));
test('token is cleared on project change and late creation cannot reveal it', async () => {
  let resolve;
  const c = setup((url, options) => options.method === 'POST' ? new Promise(r => { resolve = r; }) : {});
  c.project({}); c.elements['codex-days'].value = '7';
  c.elements['codex-create'].events.click(); await settle();
  c.project({ id: 'b' }); resolve({ token: 'SECRET' }); await settle();
  assert.equal(c.elements['codex-token'].value, ''); assert.equal(c.elements['codex-token-box'].hidden, true);
});
test('polling reads changes on visible project tabs even with closed panel', async () => {
  const c = setup(url => url.endsWith('/runs') ? { runs: [] } : { connections: [] });
  c.project({ visible: false }); c.events.tick(); await settle(); assert.equal(c.calls.length, 0);
  c.project({ visible: true }); c.events.tick(); await settle(); assert.equal(c.calls.length, 1);
  assert.match(c.calls[0].url, /changes$/);
  c.elements['codex-panel'].open = true; c.events.tick(); await settle(); assert.equal(c.calls.length, 4);
});
test('run evidence is literal text and dirty drafts block acceptance', async () => {
  const c = setup(url => url.endsWith('/runs') ? { runs: [{ id: 'r', task_title: 'Task', status: 'awaiting_review', summary: '<script>bad</script>', evidence: ['javascript:alert(1)'], card_revision: 2, plan_revision: 3 }] } : { connections: [] });
  c.project({ planDirty: true }); c.elements['codex-panel'].open = true; c.events.tick(); await settle();
  const row = c.elements['codex-runs'].children[0];
  assert.equal(row.children[1].textContent, '<script>bad</script>');
  assert.equal(row.children[2].children[0].textContent, 'javascript:alert(1)');
  row.children[3].events.click(); await settle();
  assert.equal(c.calls.length, 3); assert.match(c.elements['codex-status'].textContent, /Сначала сохраните/);
});
test('accept submits run revisions and notifies owner without replacing plan', async () => {
  const c = setup((url, options) => options.method === 'POST' ? {} : url.endsWith('/runs') ? { runs: [{ id: 'r', task_title: 'Task', status: 'awaiting_review', evidence: [], card_revision: 2, plan_revision: 3 }] } : { connections: [] });
  c.project({}); c.elements['codex-panel'].open = true; c.events.tick(); await settle();
  c.elements['codex-runs'].children[0].children[3].events.click(); await settle();
  assert.deepEqual(JSON.parse(c.calls[3].options.body), { card_revision: 2, plan_revision: 3 });
  assert.equal(c.calls[4].type, 'navigator-codex-accepted');
});

test('grant defaults are read only with reviewed completion', async () => {
  const c = setup((url, options) => options.method === 'POST' ? {token:'secret'} : {});
  c.project({}); c.elements['codex-days'].value = '7'; c.elements['codex-create'].events.click(); await settle();
  const body = JSON.parse(c.calls[0].options.body);
  assert.deepEqual(body.scopes, ['read']); assert.equal(body.completion_mode, 'review');
});
test('auto completion requires explicit progress permission', async () => {
  const c = setup(() => ({})); c.project({});
  c.elements['codex-completion-mode'] ||= {value:'auto'};
  c.elements['codex-completion-mode'].value = 'auto';
  c.elements['codex-create'].events.click(); await settle();
  assert.equal(c.calls.length, 0); assert.match(c.elements['codex-status'].textContent, /разрешите обновление прогресса/);
});
test('first poll detects remote writes since load with panel closed', async () => {
  const c = setup(() => ({card_revision:2,plan_revision:5,activity_version:8}));
  c.project({card_revision:2,plan_revision:4,visible:true});
  c.events.tick(); await settle();
  assert.equal(c.calls.length,2);
  assert.match(c.calls[0].url,/changes$/);
  assert.equal(c.calls[1].type,'navigator-codex-updated');
  assert.equal(c.calls[1].detail.plan_revision,5);
  c.events.tick(); await settle();
  assert.equal(c.calls.filter(call=>call.type==='navigator-codex-updated').length,1);
});
