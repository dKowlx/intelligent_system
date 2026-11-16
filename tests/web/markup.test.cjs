const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

test("the shipped HTML contains the DOM targets and accessible field references used by the app", () => {
  const root = path.resolve(__dirname, "../..");
  const html = fs.readFileSync(path.join(root, "web/index.html"), "utf8");
  const script = fs.readFileSync(path.join(root, "web/app.js"), "utf8") + fs.readFileSync(path.join(root,"web/workspace.js"),"utf8");
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
  const available = new Set(ids);
  assert.equal(available.size, ids.length, "HTML IDs must be unique");
  const references = [...script.matchAll(/\$\("([^"]+)"\)/g)].map((match) => match[1]);
  for (const match of html.matchAll(/\b(?:for|aria-controls|aria-labelledby|aria-describedby)="([^"]+)"/g)) {
    references.push(...match[1].split(/\s+/));
  }
  const missing = [...new Set(references.filter((id) => !available.has(id)))];
  assert.deepEqual(missing, [], "Missing DOM targets can prevent application startup");
});

test("workspace provides separate accessible views and loads shared formatting before the app", () => {
  const html = fs.readFileSync(path.resolve(__dirname, "../../web/index.html"), "utf8");
  for (const id of ["workflow-nav", "overview-panel", "card-panel", "planning", "chat-panel", "sources-panel", "plan-tasks", "plan-result"]) {
    assert.match(html, new RegExp(`id="${id}"`), `Stable target ${id}`);
  }
  for (const view of ["card", "chat", "overview", "planning", "analytics"]) {
    assert.match(html, new RegExp(`<button[^>]*data-workflow="${view}"[^>]*aria-controls="[^"]+"`), `${view} navigation names the controlled view`);
  }
  assert.match(html, /<nav[^>]*id="workflow-nav"[^>]*aria-label="[^"]+"/);
  assert.deepEqual([...html.matchAll(/data-workflow="([^"]+)"/g)].map(m=>m[1]), ["card","chat","overview","planning","analytics"]);
  const scripts = [...html.matchAll(/<script\b[^>]*\bsrc="([^"]+)"[^>]*>/g)].map((match) => match[1].split("?")[0]);
  assert.ok(scripts.indexOf("/static/text-format.js") >= 0);
  assert.ok(scripts.indexOf("/static/route-map.js") >= 0 && scripts.indexOf("/static/route-map.js") < scripts.indexOf("/static/app.js"));
  assert.ok(html.indexOf('/static/theme.js') < html.indexOf('/static/styles.css'));
  assert.match(html, /<select[^>]*id="theme-select"/);
  assert.match(html, /id="overview-route-diagram"/);
  assert.match(html, /data-route-view="list"/);
  assert.ok(scripts.indexOf("/static/text-format.js") < scripts.indexOf("/static/app.js"));
});
