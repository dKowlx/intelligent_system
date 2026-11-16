const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('web/theme.js', 'utf8');
function setup({ saved = null, dark = false, fail = false, legacy = false } = {}) {
  const events = {}, selectEvents = {}, docEvents = {}, writes = [];
  const select = { value: '', addEventListener: (name, fn) => { selectEvents[name] = fn; } };
  const media = { matches: dark };
  media[legacy ? 'addListener' : 'addEventListener'] = legacy ? fn => { events.media = fn; } : (_, fn) => { events.media = fn; };
  const themeColor = { content: '', setAttribute(name, value) { this[name] = value; } };
  const document = { querySelector: () => themeColor, readyState: 'loading', documentElement: { dataset: {}, style: {} }, getElementById: () => null, addEventListener: (name, fn) => { docEvents[name] = fn; } };
  const window = { matchMedia: () => media, localStorage: { getItem: () => { if (fail) throw Error('restricted'); return saved; }, setItem: (...args) => { if (fail) throw Error('restricted'); writes.push(args); } }, addEventListener: (name, fn) => { events[name] = fn; } };
  vm.runInNewContext(source, { document, window });
  return { window, document, media, events, writes, select, selectEvents, themeColor, mount() { document.getElementById = () => select; docEvents.DOMContentLoaded(); } };
}
test('initial paint uses OS with absent/invalid preference and explicit choice takes priority', () => {
  assert.equal(setup({ dark: true }).document.documentElement.dataset.theme, 'dark');
  assert.equal(setup({ saved: 'invalid' }).document.documentElement.dataset.theme, 'light');
  assert.equal(setup({ saved: 'light', dark: true }).document.documentElement.dataset.theme, 'light');
  assert.equal(setup({ saved: 'dark' }).document.documentElement.style.colorScheme, 'dark');
});
test('select binds on readiness, persists choice and follows system only in system mode', () => {
  const ctx = setup(); ctx.mount();
  assert.equal(ctx.select.value, 'system');
  ctx.select.value = 'dark'; ctx.selectEvents.change();
  assert.deepEqual(ctx.writes[0], ['project-navigator-theme', 'dark']);
  ctx.media.matches = false; ctx.events.media();
  assert.equal(ctx.document.documentElement.dataset.theme, 'dark');
  ctx.window.NavigatorTheme.setPreference('system');
  assert.equal(ctx.document.documentElement.dataset.theme, 'light');
  ctx.media.matches = true; ctx.events.media();
  assert.equal(ctx.document.documentElement.dataset.theme, 'dark');
});
test('storage exceptions preserve working theme controls', () => {
  const ctx = setup({ fail: true, dark: true }); ctx.mount();
  ctx.window.NavigatorTheme.setPreference('light');
  assert.equal(ctx.document.documentElement.dataset.theme, 'light');
  assert.equal(ctx.window.NavigatorTheme.getPreference(), 'light');
});
test('storage changes synchronize tabs and deletion returns to system', () => {
  const ctx = setup({ dark: true }); ctx.mount();
  ctx.events.storage({ key: 'other', newValue: 'light' });
  assert.equal(ctx.select.value, 'system');
  ctx.events.storage({ key: 'project-navigator-theme', newValue: 'light' });
  assert.equal(ctx.document.documentElement.dataset.theme, 'light');
  ctx.events.storage({ key: null, newValue: null });
  assert.equal(ctx.document.documentElement.dataset.theme, 'dark');
});
test('legacy media listeners and unsupported media queries are safe', () => {
  const ctx = setup({ legacy: true }); ctx.media.matches = true; ctx.events.media();
  assert.equal(ctx.document.documentElement.dataset.theme, 'dark');
  const document = { readyState: 'complete', documentElement: { dataset: {}, style: {} }, getElementById: () => null };
  vm.runInNewContext(source, { document, window: { addEventListener() {} } });
  assert.equal(document.documentElement.dataset.theme, 'light');
});

test('browser theme color follows initial choice and later switches', () => {
  const ctx = setup({ saved: 'dark' });
  assert.equal(ctx.themeColor.content, '#101722');
  ctx.window.NavigatorTheme.setPreference('light');
  assert.equal(ctx.themeColor.content, '#f5f7fb');
});
