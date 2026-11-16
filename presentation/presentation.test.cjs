"use strict";
// DOM/VM checks of the real inline script. These are not browser or visual QA.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const script = /<script id="deck-script">([\s\S]*?)<\/script>/.exec(html)[1];

function harness(hash = "") {
  const nodes = new Map(), documentEvents = {}, windowEvents = {};
  let focused = null, printCalls = 0;
  function node(id = "", tagName = "DIV") {
    return { id, tagName, textContent: "", hidden: false, open: false, showModal() { this.open = true; }, close() { this.open = false; }, value: "", dataset: {}, attributes: {}, children: [], listeners: {},
      classList: { values: new Set(), toggle(name, enabled) { if (enabled) this.values.add(name); else this.values.delete(name); } },
      setAttribute(name, value) { this.attributes[name] = value; },
      addEventListener(name, callback) { this.listeners[name] = callback; },
      append(child) { this.children.push(child); }, focus() { focused = this.id; },
    };
  }
  for (const match of html.matchAll(/<([a-z][\w-]*)\b([^>]*\bid="([^"]+)"[^>]*)>/gi)) {
    const item = node(match[3], match[1].toUpperCase());
    item.hidden = /\bhidden\b/.test(match[2]);
    item.value = /\bvalue="([^"]*)"/.exec(match[2])?.[1] || "";
    nodes.set(item.id, item);
  }
  const slides = Array.from({ length: 7 }, (_, i) => {
    const slide = nodes.get(`slide-${i + 1}`), heading = nodes.get(`title-${i + 1}`);
    heading.textContent = new RegExp(`<h[12] id="title-${i + 1}"[^>]*>([\\s\\S]*?)<\\/h[12]>`).exec(html)[1].replace(/<[^>]+>/g, " ");
    slide.querySelector = () => heading;
    return slide;
  });
  const caseButtons = [...html.matchAll(/<button\b[^>]*\bdata-open-case(?:[\s=>])[^>]*>/g)].map(() => node("", "BUTTON"));
  const location = { hash };
  const context = vm.createContext({
    document: { getElementById: id => nodes.get(id), createElement: tag => node("", tag.toUpperCase()),
      querySelectorAll: selector => selector === ".slide" ? slides : selector === "[data-open-case]" ? caseButtons : [],
      addEventListener: (name, callback) => { documentEvents[name] = callback; } },
    window: { addEventListener: (name, callback) => { windowEvents[name] = callback; }, scrollTo() {}, print() { printCalls += 1; } },
    location, history: { replaceState(_state, _title, nextHash) { location.hash = nextHash; } },
    fetch() { throw new Error("The presentation must never call the network"); },
  });
  vm.runInContext(script, context);
  return { get: id => nodes.get(id), slides, caseButtons, location, focused: () => focused,
    click: id => nodes.get(id).listeners.click(),
    key(key, tagName = "BODY") { let prevented = false; documentEvents.keydown({ key, target: { tagName }, preventDefault() { prevented = true; } }); return prevented; },
    hash(value) { location.hash = value; windowEvents.hashchange(); },
    printCalls: () => printCalls,
  };
}

test("the seven-slide artifact has valid references and a consistent planned duration", () => {
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(new Set(ids).size, ids.length);
  const referenced = [...script.matchAll(/getElementById\("([^"]+)"\)/g)].map(match => match[1]);
  for (const match of html.matchAll(/\b(?:for|aria-labelledby|aria-describedby)="([^"]+)"/g)) referenced.push(...match[1].split(/\s+/));
  assert.deepEqual(referenced.filter(id => !ids.includes(id)), []);
  assert.equal([...html.matchAll(/class="slide"/g)].length, 7);
  assert.doesNotMatch(html, /<(?:script|link|img|iframe)\b[^>]*\b(?:src|href)\s*=/i);
  assert.doesNotMatch(html, /@import|url\(/i);
  assert.doesNotMatch(script, /\b(?:fetch|XMLHttpRequest|WebSocket|innerHTML|eval)\b/);
  assert.match(html, /prefers-reduced-motion/);
  assert.match(html, /@media print/);
  const seconds = [...html.matchAll(/data-duration="(\d+):(\d+)"/g)].reduce((sum, match) => sum + Number(match[1]) * 60 + Number(match[2]), 0);
  assert.equal(seconds + 60, 450, "6:30 of slides plus 1:00 demo is a planned 7:30, not a measured run");
});

test("navigation changes visibility, focus, hash and progress, and leaves range arrows to the control", () => {
  const app = harness("#slide-4");
  assert.equal(app.slides.filter(slide => !slide.hidden)[0].id, "slide-4");
  assert.equal(app.get("deck-progress").value, 4);
  app.click("next");
  assert.equal(app.focused(), "title-5");
  assert.equal(app.location.hash, "#slide-5");
  assert.equal(app.get("slide-count").textContent, "5 / 7");
  assert.equal(app.key("ArrowRight", "INPUT"), false);
  assert.equal(app.location.hash, "#slide-5");
  assert.equal(app.key("End"), true);
  assert.equal(app.get("next").disabled, true);
  assert.equal(app.get("deck-progress").value, 7);
  app.key("Home");
  assert.equal(app.focused(), "title-1");
  assert.equal(app.get("previous").disabled, true);
  app.get("slide-dots").children[5].listeners.click();
  assert.equal(app.focused(), "title-6");
  app.hash("#slide-3");
  assert.equal(app.slides.filter(slide => !slide.hidden)[0].id, "slide-3");
  app.click("print-deck");
  assert.equal(app.printCalls(), 1);
});

test("requested sections are removed and the remaining slides keep their order", () => {
  const section = number => new RegExp(`<section id="slide-${number}"[\\s\\S]*?<\\/section>`).exec(html)[0];
  assert.doesNotMatch(section(3), /аккаунт|браузерн|cookie|без OCR|нет /i);
  assert.match(section(3), /PDF/);
  assert.match(section(5), /available-minutes/);
  assert.match(section(6), /Сравнение с независимым эталоном/);
  assert.doesNotMatch(html, /317|13\/13|8\/8|27\/27/);
  assert.match(section(7), /Что получил пользователь в контрольном случае/);
  assert.doesNotMatch(html, /data-scenario|proof-json|example-answer|Что остаётся проверить|Репетицию защиты|Требования и рубрика|Самодостаточная локальная презентация|class="limitation"|class="local-label"|class="demo-cue"|class="keyboard"|Локальная арифметика|Живое демо — после слайда|При сбое API показать/i);
  assert.doesNotMatch(script, /showScenario|scenarios|example-answer|proof-json/);
  assert.equal(harness().get("slide-dots").children.length, 7);
});

test("daily arithmetic matches actual app planner evidence at the rounding boundary", () => {
  const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, "case-study.json"), "utf8"));
  assert.equal(fixture.planner_evidence.function, "studynavigator.planner.build_adaptive_plan");
  assert.deepEqual(fixture.planner_evidence.cases.map(item => item.daily_minutes), [90, 120, 127, 128, 150, 180]);
  const app = harness(), input = app.get("available-minutes");
  assert.equal(app.get("plan-summary").textContent, "Не хватает 60 минут");
  for (const expected of fixture.planner_evidence.cases) {
    input.value = String(expected.daily_minutes); input.listeners.input();
    const usableDaily = Math.floor(expected.daily_minutes * (100 - fixture.planner_evidence.reserve_percent) / 100);
    assert.equal(expected.available_minutes, fixture.planner_evidence.days * usableDaily);
    assert.equal(expected.required_minutes, fixture.model_output.total_minutes);
    assert.equal(expected.deficit_minutes, Math.max(0, expected.required_minutes - expected.available_minutes));
    assert.equal(expected.scheduled_minutes, Math.min(expected.required_minutes, expected.available_minutes));
    assert.equal(expected.spare_minutes, Math.max(0, expected.available_minutes - expected.required_minutes));
    assert.equal(expected.status, expected.deficit_minutes ? "insufficient_time" : "feasible");
    assert.equal(app.get("plan-summary").textContent, expected.deficit_minutes ? `Не хватает ${expected.deficit_minutes} минут` : "План укладывается в доступное время");
    assert.equal(app.get("plan-output").classList.values.has("is-deficit"), expected.deficit_minutes > 0);
    assert.equal(app.get("plan-detail").textContent, `10 дней × ${usableDaily} мин после резерва = ${expected.available_minutes} мин. Нужно ${expected.required_minutes}.`);
  }
  input.value = "0"; input.listeners.input();
  assert.equal(app.get("plan-summary").textContent, "Не хватает 1020 минут");
});

test("the header contains only the full project name and returns to the first slide without rehearsal controls", () => {
  const header = /<header class="topbar">([\s\S]*?)<\/header>/.exec(html)[1];
  assert.equal(header.replace(/<[^>]*>/g, "").replace(/\s+/g, ""), "ProjectNavigator");
  assert.doesNotMatch(html, /rehearsal-time|timer-start|timer-reset|updateTimer|Защита учебного проекта|27 октября 2026/);
  const app = harness("#slide-7");
  app.click("home");
  assert.equal(app.location.hash, "#slide-1");
  assert.equal(app.focused(), "title-1");
  app.click("next");
  assert.equal(app.location.hash, "#slide-2");
});


test("the PDF case records one actual live model output and its evaluation limits", () => {
  const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, "case-study.json"), "utf8"));
  assert.equal(fixture.source.sha256, "deaa77f98566f723cbf30f7dbd0aac05d2a0f524cb468c1edf254213e0b6a722");
  assert.equal(fixture.source.pages, 3);
  assert.equal(fixture.source.deadline, null);
  assert.equal(fixture.provenance.mode, "live");
  assert.equal(fixture.provenance.analysis_kind, "model");
  assert.equal(fixture.provenance.http_dispatched, 1);
  assert.equal(fixture.provenance.actual_cost_rub, 3.1608);
  assert.equal(fixture.provenance.quality_score, null);
  assert.ok(fixture.provenance.trace.some(item => item.name === "get_assignment" && item.status === "ok"));
  assert.equal(fixture.reference.created_before_app_output_review, true);
  assert.equal(fixture.reference.aspect_count, 22);
  assert.equal(fixture.model_output.tasks.length, 5);
  assert.equal(fixture.model_output.tasks.reduce((sum, item) => sum + item.minutes, 0), 1020);
  assert.equal(fixture.model_output.total_minutes, 1020);
  assert.equal(fixture.model_output.tasks.reduce((sum, item) => sum + item.substeps.length, 0), 20);
  assert.equal(fixture.model_output.substep_count, 20);
  assert.equal(fixture.model_output.sum_min_minutes, 690);
  assert.equal(fixture.model_output.sum_max_minutes, 1490);
  const evalTask = fixture.model_output.tasks.find(item => item.id === "t4");
  assert.equal(evalTask.minutes, 210);
  assert.deepEqual(evalTask.substeps.map(item => item.minutes), [35, 50, 40, 85]);
  assert.equal(evalTask.substeps.reduce((sum, item) => sum + item.minutes, 0), evalTask.minutes);
  assert.equal(fixture.planner_evidence.days, 10);
  assert.equal(fixture.planner_evidence.reserve_percent, 20);
  assert.match(html, /Личный проект.pdf/);
  assert.match(html, /1020/);
  assert.doesNotMatch(html, /пять интервью|I1–I5|Участники уже найдены|1740|470 минут|450 минут/i);
  assert.doesNotMatch(html, /<aside|speaker.notes|rehearsal|timer/i);
});

test("the daily slider exposes its intended range and one-minute rounding boundaries", () => {
  const range = /<input[^>]*id="available-minutes"[^>]*>/.exec(html)[0];
  assert.match(range, /min="90"/);
  assert.match(range, /max="180"/);
  assert.match(range, /step="1"/);
  assert.match(range, /value="120"/);
  const app = harness();
  assert.equal(app.get("budget-value").textContent, "120 мин");
});

test("the standalone case dialog opens real model substeps and pauses deck keys", () => {
  const app = harness("#slide-3");
  assert.ok(app.caseButtons.length >= 1);
  app.caseButtons[0].listeners.click();
  assert.equal(app.get("case-dialog").open, true);
  assert.equal(app.key("ArrowRight"), false);
  assert.equal(app.location.hash, "#slide-3");
  app.click("close-case");
  assert.equal(app.get("case-dialog").open, false);
  assert.equal(app.key("ArrowRight"), true);
  assert.equal(app.location.hash, "#slide-4");
  const dialog = /<dialog\b[^>]*id="case-dialog"[\s\S]*?<\/dialog>/.exec(html)[0];
  assert.equal([...dialog.matchAll(/<details\b/g)].length, 5);
  const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, "case-study.json"), "utf8"));
  const plain = dialog.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
  for (const task of fixture.model_output.tasks) {
    assert.ok(plain.includes(task.title), `dialog shows actual task: ${task.title}`);
    for (const substep of task.substeps) assert.ok(plain.includes(substep.title), `dialog shows actual substep: ${substep.title}`);
  }
});
