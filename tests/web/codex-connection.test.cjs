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
test('polling reads only while planning is visible and focused', async () => {
  const c = setup(url => url.endsWith('/runs') ? { runs: [] } : { connections: [] });
  c.project({}); c.elements['codex-panel'].open = true;
  c.elements.planning.hidden = true; c.events.tick(); await settle(); assert.equal(c.calls.length, 0);
  c.elements.planning.hidden = false; c.events.tick(); await settle(); assert.equal(c.calls.length, 2);
});
test('run evidence is literal text and dirty drafts block acceptance', async () => {
  const c = setup(url => url.endsWith('/runs') ? { runs: [{ id: 'r', task_title: 'Task', status: 'awaiting_review', summary: '<script>bad</script>', evidence: ['javascript:alert(1)'], card_revision: 2, plan_revision: 3 }] } : { connections: [] });
  c.project({ planDirty: true }); c.elements['codex-panel'].open = true; c.events.tick(); await settle();
  const row = c.elements['codex-runs'].children[0];
  assert.equal(row.children[1].textContent, '<script>bad</script>');
  assert.equal(row.children[2].children[0].textContent, 'javascript:alert(1)');
  row.children[3].events.click(); await settle();
  assert.equal(c.calls.length, 2); assert.match(c.elements['codex-status'].textContent, /Сначала сохраните/);
});
test('accept submits run revisions and notifies owner without replacing plan', async () => {
  const c = setup((url, options) => options.method === 'POST' ? {} : url.endsWith('/runs') ? { runs: [{ id: 'r', task_title: 'Task', status: 'awaiting_review', evidence: [], card_revision: 2, plan_revision: 3 }] } : { connections: [] });
  c.project({}); c.elements['codex-panel'].open = true; c.events.tick(); await settle();
  c.elements['codex-runs'].children[0].children[3].events.click(); await settle();
  assert.deepEqual(JSON.parse(c.calls[2].options.body), { card_revision: 2, plan_revision: 3 });
  assert.equal(c.calls[3].type, 'navigator-codex-accepted');
});
