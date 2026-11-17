const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const appSource = fs.readFileSync(process.env.STUDYNAVIGATOR_WEB_APP || path.join(__dirname, "../../web/app.js"), "utf8");
const formatterSource = fs.readFileSync(path.join(__dirname, "../../web/text-format.js"), "utf8");
const uploaded = { assignment_id: "upload-a", title: "Работа A", is_uploaded: true, status: "awaiting_analysis", sources: [], documents: [] };
const seed = { assignment_id: "personal-project", title: "Личный проект", status: "ready", sources: [] };
const card = {
  title: "Работа A", goal: { text: "Изучить тему", origin: "user", source_ids: [] },
  deliverable: { text: "Отчёт", origin: "user", source_ids: [] },
  requirements: [], criteria: [], deadline: { date: null, origin: "unknown", source_ids: [] }, tasks: [], questions: [],
};

// A small DOM substitute for state-transition tests; browser checks are separate.
function harness(fetchResponse, { reducedMotion = false, formatter = true, routeMap, codex } = {}) {
  const nodes = new Map();
  const documentListeners = new Map();
  let focused = null;
  function node(id = "") {
    return {
      id, hidden: true, _disabled: false, _text: "", value: "", files: [], dataset: {}, style: {}, children: [], attributes: {}, listeners: {},
      get textContent() { return this._text + this.children.map((child) => child.textContent || "").join(""); },
      set textContent(value) { this._text = String(value); this.children = []; },
      get disabled() { return this._disabled; },
      set disabled(value) { this._disabled = value; if (value && focused === this) focused = document.body; },
      classList: { add() {}, remove() {}, toggle() {} },
      setAttribute(name, value) { this.attributes[name] = value; }, removeAttribute(name) { delete this.attributes[name]; },
      append(...items) { this.children.push(...items); },
      replaceChildren(...items) { this._text = ""; this.children = items; },
      addEventListener(name, listener) { this.listeners[name] = listener; }, querySelectorAll() { return []; }, reportValidity() { return true; }, checkValidity() { return true; },
      querySelector(selector) {
        for (const child of this.children) {
          if (selector.startsWith(".") && child.className?.split(/\s+/).includes(selector.slice(1))) return child;
          const match = child.querySelector?.(selector);
          if (match) return match;
        }
        return null;
      },
      focus() { focused = this; for (const listener of documentListeners.get("focusin") || []) listener({ target: this }); }, scrollIntoView(options) { this.lastScroll = options; },
    };
  }
  const document = {
    body: node("body"),
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, node(id)); return nodes.get(id); },
    createElement(tagName) { const created = node(); created.tagName = tagName; return created; }, createTextNode(text) { return { textContent: text }; },
    querySelectorAll(selector) { return selector.includes("[data-workflow]") ? workflow : []; },
    get activeElement() { return focused; }, hasFocus() { return true; },
    addEventListener(name, listener) { if (!documentListeners.has(name)) documentListeners.set(name, new Set()); documentListeners.get(name).add(listener); },
    removeEventListener(name, listener) { documentListeners.get(name)?.delete(listener); },
  };
  const workflow = ["card", "chat", "overview", "planning", "analytics"].map((step) => {
    const button = node(`workflow-${step}`);
    button.dataset.workflow = step;
    return button;
  });
  const calls = [];
  const context = vm.createContext({
    document, window: { NavigatorCodex: codex, NavigatorRouteMap: routeMap, location: { origin: "http://localhost" }, matchMedia: () => ({ matches: reducedMotion }) }, URL, AbortController, FormData, TextEncoder, setTimeout, clearTimeout,
    fetch: async (url, options = {}) => {
      calls.push({ url, options });
      const result = await fetchResponse(url, options);
      if (options.signal?.aborted) throw new Error("Mock request aborted");
      return { ok: result.status < 400, status: result.status, json: async () => result.body };
    },
  });
  if (formatter) vm.runInContext(formatterSource, context);
  vm.runInContext(appSource.replace(/initialize\(\);\s*$/, "") + "\nglobalThis.app = { state, selectAssignment, submitUpload, deleteAssignment, confirmCard, submitPlan, updateControls, analyzeCard, showMode, scrollConversation, sourceCard, renderMaterialResults, messageNode, fillAnswer, renderActionEditor, readCardForm, renderAvailability, initialize, loadSavedPlan, restoreSavedTasks, renderTaskEditor, renderOverview, navigateWorkflow, effectiveDayMinutes, renderPlan, normalizeSubsteps, loadRouteTheory, loadChatSessions, openChatSession, newChat, sendMessage, deleteChatSession };", context);
  const app = context.app;
  app.state.status = { mode: "offline" };
  app.state.assignments = [uploaded, seed];
  app.state.selectedId = uploaded.assignment_id;
  app.state.detail = uploaded;
  return { ...app, get: document.getElementById, calls, workflow, focused: () => focused?.id, active: () => focused };
}

function response(assignment) { return { status: 200, body: { assignment } }; }
function unavailable() { return { status: 503, body: { error: { message: "Сервис временно недоступен." } } }; }
function event() { return { preventDefault() {} }; }

test("failed analysis clears its busy status and offers a readable budget failure", async () => {
  const app = harness(() => ({ status: 503, body: { error: { code: "eval_budget_exceeded", message: "Technical budget guard exceeded." } } }));
  app.state.status = { mode: "live", live_configured: true };
  await app.analyzeCard();
  assert.equal(app.state.analysis, null);
  assert.doesNotMatch(app.get("card-status").textContent, /Разбираем|проверяем структуру/);
  assert.match(app.get("card-error").textContent, /тестовый лимит/i);
  assert.equal(app.get("analyze-button").disabled, false);
  assert.equal(app.get("analyze-cancel").hidden, true);
  assert.equal(app.get("card-form").attributes["aria-busy"], "false");
});

test("healthy workspace hides provider notices and never exposes provider URIs", () => {
  const app = harness(unavailable);
  for (const model of ["gpt://private-folder/yandexgpt-5.1/latest", "gpt://private-folder/aliceai-llm-flash/latest", "unknown-private-model"]) {
    app.showMode({ mode: "live", live_configured: true, model });
    assert.doesNotMatch(app.get("mode-notice").textContent, /gpt:\/\/|private-folder|unknown-private-model|Живая модель/);
    assert.equal(app.get("mode-badge").hidden, true);
  }
  app.showMode({ mode: "live", live_configured: true, model: "gpt://private-folder/yandexgpt-5.1/latest" });
  assert.equal(app.get("mode-notice").hidden, true);
  app.showMode({mode:"live",live_configured:false});
  assert.equal(app.get("mode-notice").hidden, false);
  assert.match(app.get("mode-notice").textContent, /недоступен/);
});

test("source excerpts and completed AI answers use safe blocks without changing source data or links", () => {
  const app = harness(unavailable);
  const source = { source_id: "private-source", page: 2, title: "Требования", excerpt: 'Нужно: ● README • <img src=x onerror="alert(1)">', url: "/sources/private-source", document_url: "/documents/private-document" };
  const original = JSON.stringify(source);
  const sourceNode = app.sourceCard(source);
  const excerpt = sourceNode.querySelector(".source-excerpt");
  assert.equal(excerpt.tagName, "div");
  assert.deepEqual(excerpt.children.map((child) => child.tagName), ["p", "ul"]);
  assert.match(excerpt.children[1].children[1].textContent, /<img src=x onerror=/);
  assert.equal(sourceNode.querySelector(".source-open").href, "http://localhost/sources/private-source");
  app.renderMaterialResults([source]);
  assert.equal(app.get("material-results").children[0].querySelector(".source-excerpt"), null);
  assert.match(app.get("material-results").children[0].querySelector(".material-description").textContent, /<img src=x onerror=/);
  assert.equal(JSON.stringify(source), original);

  const message = app.messageNode("assistant", "Обрабатываем запрос…", true);
  const content = message.querySelector(".message-content");
  assert.equal(content.tagName, "div");
  assert.equal(content.textContent, "Обрабатываем запрос…");
  app.fillAnswer(message, { answer: "План:\n\n3. Python 3.12\n5. https://example.test/v1.2", mode: "live", sources: [] });
  assert.deepEqual(content.children.map((child) => child.tagName), ["p", "ol"]);
  assert.equal(content.children[1].attributes.start, "3");
  assert.equal(content.children[1].children[1].attributes.value, "5");
  assert.doesNotMatch(content.textContent, /Обрабатываем/);
});

test("the standalone planning panel stays hidden until the selected assignment is confirmed", async () => {
  let selected = uploaded;
  const app = harness(() => response(selected));
  await app.selectAssignment(uploaded.assignment_id, true);
  assert.equal(app.get("planning").hidden, true);
  assert.equal(app.get("plan-button").disabled, true);
  selected = { ...uploaded, status: "ready", card, card_revision: 1 };
  await app.selectAssignment(uploaded.assignment_id, true);
  assert.equal(app.get("planning").hidden, true);
  assert.equal(app.get("card-panel").hidden, false);
  assert.equal(app.get("context-column").hidden, true, "Uploaded materials already have their own panel");
  assert.equal(app.get("plan-button").disabled, false);
  app.state.cardDirty = true;
  app.updateControls();
  assert.equal(app.get("plan-button").disabled, true);
  selected = seed;
  await app.selectAssignment(seed.assignment_id, true);
  app.navigateWorkflow("chat");
  assert.equal(app.get("context-column").hidden, true, "Citations stay inside answers, without a separate source pane");
});

test("user messages keep paragraphs and list items without interpreting embedded HTML", () => {
  const app = harness(unavailable);
  const message = app.messageNode("user", "Мой вопрос:\n\n● Первый пункт\n● <img src=x onerror=alert(1)>");
  const content = message.querySelector(".message-content");
  assert.deepEqual(content.children.map((node) => node.tagName), ["p", "ul"]);
  assert.equal(content.children[1].children[1].textContent, "<img src=x onerror=alert(1)>");
});

test("an unavailable formatter script leaves source and answer text readable without stopping the application", () => {
  const app = harness(unavailable, { formatter: false });
  const text = '<script>alert(1)</script> ● Сдать PDF';
  const source = app.sourceCard({ excerpt: text, url: "/sources/private-source" });
  const excerpt = source.querySelector(".source-excerpt");
  assert.equal(excerpt.textContent, text);
  assert.deepEqual(excerpt.children, []);
  const message = app.messageNode("assistant", "Ожидаем…", true);
  app.fillAnswer(message, { answer: text, mode: "live", sources: [] });
  assert.equal(message.querySelector(".message-content").textContent, text);
  assert.deepEqual(message.querySelector(".message-content").children, []);
  app.updateControls();
  assert.equal(app.get("manual-card-button").disabled, false);
});

test("manual entry before analysis confirms revision zero with user provenance and no model request", async () => {
  const confirmed = { ...uploaded, status: "ready", card, card_revision: 1 };
  const app = harness(() => response(confirmed));
  app.state.detail = { ...uploaded, card: null, card_revision: 0 };
  app.state.status = { mode: "live", live_configured: true };
  app.updateControls();
  assert.equal(app.get("manual-card-button").hidden, false);
  app.get("manual-card-button").listeners.click();
  assert.equal(app.calls.length, 0);
  assert.equal(app.get("card-form").hidden, false);
  assert.equal(app.get("card-goal").value, "");
  assert.equal(app.state.cardDraft.deadline.date, null);
  app.get("card-goal").value = "Моя цель";
  app.get("card-deliverable").value = "Мой отчёт";
  app.get("card-requirements").value = "Включить график";
  app.get("card-form").listeners.input();
  await app.confirmCard(event());
  assert.equal(app.calls.length, 1);
  assert.match(app.calls[0].url, /\/confirm$/);
  const sent = JSON.parse(app.calls[0].options.body);
  assert.equal(sent.revision, 0);
  for (const field of [sent.card.goal, sent.card.deliverable, sent.card.requirements[0]]) {
    assert.equal(field.origin, "user");
    assert.deepEqual(field.source_ids, []);
  }
  assert.deepEqual(sent.card.deadline, { date: null, origin: "unknown", source_ids: [] });
  assert.equal(app.state.detail.status, "ready");
});

test("budget failure allows explicit manual recovery without retrying analysis or changing provider mode", async () => {
  const app = harness(() => ({ status: 503, body: { error: { code: "eval_budget_exceeded", message: "guard" } } }));
  app.state.status = { mode: "live", live_configured: true };
  await app.analyzeCard();
  app.get("manual-card-button").listeners.click();
  assert.equal(app.calls.length, 1);
  assert.match(app.calls[0].url, /\/analyze$/);
  assert.equal(app.state.selectedId, uploaded.assignment_id);
  assert.equal(app.state.detail, uploaded);
  assert.equal(app.state.status.mode, "live");
  assert.equal(app.get("card-form").hidden, false);
  assert.equal(app.get("card-fields").disabled, false);
  assert.match(app.get("card-notice").textContent, /Неизвестную дату оставьте пустой/);
});

test("manual action preserves an existing dirty draft and its evidence", () => {
  const app = harness(unavailable);
  app.state.cardDraft = card;
  app.state.cardDirty = true;
  app.get("card-goal").value = "Несохранённая цель";
  app.get("manual-card-button").listeners.click();
  assert.equal(app.state.cardDraft, card);
  assert.equal(app.state.cardDirty, true);
  assert.equal(app.get("card-goal").value, "Несохранённая цель");
  assert.equal(app.calls.length, 0);
});

test("late cancelled analysis cannot open a manual draft or show an error for another assignment", async () => {
  let finish;
  const app = harness((url) => url.endsWith("/analyze") ? new Promise((resolve) => { finish = resolve; }) : response(seed));
  app.state.status = { mode: "live", live_configured: true };
  const pending = app.analyzeCard();
  await app.selectAssignment(seed.assignment_id);
  finish({ status: 503, body: { error: { code: "eval_budget_exceeded", message: "guard" } } });
  await pending;
  assert.equal(app.state.detail, seed);
  assert.equal(app.state.analysis, null);
  assert.equal(app.state.cardDraft, null);
  assert.equal(app.get("card-status").textContent, "");
  assert.equal(app.get("card-error").hidden, true);
  assert.equal(app.get("manual-card-button").hidden, true);
});

test("workflow disables unavailable steps and navigation preserves current drafts, history and sources", () => {
  const app = harness(unavailable);
  app.state.cardDraft = card;
  app.state.cardDirty = true;
  const history = [{ role: "user", content: "Мой вопрос" }];
  app.state.history = history;
  app.get("card-goal").value = "Правка";
  app.get("question").value = "Неотправленный вопрос";
  const source = { source_id: "source-private", excerpt: "Мой материал" };
  app.get("material-results").append(source);
  app.updateControls();
  assert.deepEqual(app.workflow.map((button) => button.disabled), [false, true, true, true, true]);
  app.workflow[3].listeners.click();
  assert.equal(app.focused(), undefined);

  app.workflow[0].listeners.click();
  assert.equal(app.focused(), "card-heading");
  assert.equal(app.workflow[0].attributes["aria-current"], "page");
  assert.equal(app.workflow[2].attributes["aria-current"], undefined);
  assert.equal(app.get("card-goal").value, "Правка");
  assert.equal(app.get("question").value, "Неотправленный вопрос");
  assert.equal(app.state.history, history);
  assert.equal(app.get("material-results").children[0], source);
  assert.equal(app.calls.length, 0);
});

test("workflow keyboard skips disabled steps and only scrolls after activation, respecting reduced motion", () => {
  for (const reducedMotion of [false, true]) {
    const app = harness(unavailable, { reducedMotion });
    app.updateControls();
    const press = (index, key) => app.workflow[index].listeners.keydown({ key, preventDefault() {} });
    press(2, "End");
    assert.equal(app.focused(), "workflow-card");
    assert.equal(app.workflow[0].tabIndex, 0);
    assert.equal(app.get("card-heading").lastScroll, undefined);
    press(0, "ArrowRight");
    assert.equal(app.focused(), "workflow-card");
    app.workflow[0].listeners.click();
    assert.equal(app.get("card-heading").lastScroll.behavior, reducedMotion ? "auto" : "smooth");
    app.state.detail = { ...uploaded, status: "ready" };
    app.updateControls();
    assert.equal(app.workflow.every((button) => !button.disabled), true);
    app.workflow[1].listeners.click();
    assert.equal(app.workflow[1].attributes["aria-current"], "page");
    press(1, "Home");
    assert.equal(app.focused(), "workflow-card");
  }
});

test("upload can collapse without clearing selected files or an unfinished assignment name", () => {
  const app = harness(unavailable);
  const file = { name: "private.pdf", size: 10 };
  app.get("upload-files").files = [file];
  app.get("upload-name").value = "Новое задание";
  app.get("upload-body").hidden = false;
  app.get("upload-toggle").listeners.click();
  assert.equal(app.get("upload-body").hidden, true);
  assert.equal(app.get("upload-toggle").attributes["aria-expanded"], "false");
  app.get("upload-toggle").listeners.click();
  assert.equal(app.get("upload-body").hidden, false);
  app.workflow[0].listeners.click();
  assert.equal(app.get("upload-body").hidden, false);
  assert.equal(app.get("upload-name").value, "Новое задание");
  assert.equal(app.get("upload-files").files[0], file);
});

test("new chat content follows the bottom but does not move a reader reviewing earlier messages", () => {
  const app = harness(unavailable);
  const log = app.get("conversation");
  log.scrollHeight = 1000;
  log.clientHeight = 200;
  log.scrollTop = 100;
  log.listeners.scroll();
  log.scrollHeight = 1200;
  app.scrollConversation();
  assert.equal(log.scrollTop, 100);
  log.scrollTop = 1000;
  log.listeners.scroll();
  log.scrollHeight = 1300;
  app.scrollConversation();
  assert.equal(log.scrollTop, 1300);
});

test("confirmed project opens on its card and dirty fields cannot be collapsed", async () => {
  const saved = { ...uploaded, status: "ready", card, card_revision: 1 };
  const app = harness(() => response(saved));
  await app.selectAssignment(uploaded.assignment_id, true);
  assert.equal(app.get("card-body").hidden, false);
  app.get("card-toggle").listeners.click();
  assert.equal(app.get("card-body").hidden, true);
  assert.equal(app.get("card-summary").hidden, false);
  assert.match(app.get("card-summary").textContent, /Отчёт/);
  assert.equal(app.get("card-toggle").textContent, "Изменить карточку");
  app.get("card-toggle").listeners.click();
  assert.equal(app.get("card-body").hidden, false);
  app.workflow[1].listeners.click();
  assert.equal(app.get("card-panel").hidden, true);
  app.workflow[0].listeners.click();
  assert.equal(app.get("card-body").hidden, false);
  app.get("card-goal").value = "Не потерять эту цель";
  app.get("card-form").listeners.input();
  assert.equal(app.get("card-toggle").hidden, true);
  app.get("card-toggle").listeners.click();
  assert.equal(app.get("card-body").hidden, false);
  assert.equal(app.get("card-goal").value, "Не потерять эту цель");
  app.get("card-discard").listeners.click();
  assert.equal(app.get("card-body").hidden, false);
  assert.equal(app.focused(), "card-goal");
});

test("switching assignments preserves an unsaved card and explains the next action", async () => {
  const app = harness(() => response(seed));
  app.state.cardDraft = card;
  app.state.cardDirty = true;
  app.get("card-goal").value = "Несохранённая цель";
  const opened = await app.selectAssignment(seed.assignment_id);
  assert.equal(app.state.selectedId, uploaded.assignment_id);
  assert.equal(app.state.cardDirty, true);
  assert.equal(app.get("card-goal").value, "Несохранённая цель");
  assert.equal(app.calls.length, 0);
  assert.match(app.get("card-status").textContent, /Отменить правки/);
  assert.equal(app.focused(), "card-confirm");
  assert.equal(opened, false);
});

test("explicit card reload can replace a dirty draft", async () => {
  const saved = { ...uploaded, status: "ready", card, card_revision: 2 };
  const app = harness(() => response(saved));
  app.state.cardDirty = true;
  await app.selectAssignment(uploaded.assignment_id, true);
  assert.equal(app.state.cardDirty, false);
  assert.equal(app.get("card-goal").value, card.goal.text);
  assert.equal(app.workflow[0].attributes["aria-current"], "page");
});

test("failed assignment loading returns a retryable failure", async () => {
  const app = harness(unavailable);
  await app.selectAssignment(seed.assignment_id);
  assert.equal(app.state.loading, false);
  assert.equal(app.state.detail, null);
  assert.equal(app.get("sources").children.at(-1).textContent, "Загрузить проект ещё раз");
});

test("upload does not announce an unopened card as the next active screen", async () => {
  const app = harness((url, options) => options.method === "POST" ? response(uploaded) : unavailable());
  app.get("upload-files").files = [{ name: "task.pdf", size: 100 }];
  app.state.selectedId = seed.assignment_id;
  app.state.detail = seed;
  await app.submitUpload(event());
  assert.equal(app.state.assignments[0].assignment_id, uploaded.assignment_id);
  assert.match(app.get("upload-status").textContent, /повторите его открытие/);
  assert.notEqual(app.focused(), "card-heading");
  assert.equal(app.state.upload, null);
});

test("successful upload moves keyboard focus to the card heading", async () => {
  const app = harness(() => response(uploaded));
  app.get("upload-files").files = [{ name: "task.pdf", size: 100 }];
  app.state.selectedId = seed.assignment_id;
  app.state.detail = seed;
  await app.submitUpload(event());
  assert.equal(app.focused(), "card-heading");
  assert.equal(app.get("card-heading").attributes.tabindex, "-1");
});

test("successful card confirmation moves keyboard focus to planning", async () => {
  const confirmed = { ...uploaded, status: "ready", card, card_revision: 2 };
  const app = harness(() => response(confirmed));
  app.state.detail = { ...uploaded, card_revision: 1 };
  app.state.cardDraft = card;
  app.get("card-title").value = card.title;
  app.get("card-goal").value = card.goal.text;
  app.get("card-deliverable").value = card.deliverable.text;
  await app.confirmCard(event());
  assert.equal(app.focused(), "plan-title");
  assert.equal(app.state.confirmation, null);
  assert.equal(app.workflow[3].attributes["aria-current"], "page");
});

test("successful deletion removes the item even when the next detail request fails", async () => {
  const app = harness((url, options) => options.method === "DELETE" ? { status: 204 } : unavailable());
  await app.deleteAssignment();
  assert.deepEqual(Array.from(app.state.assignments, (item) => item.assignment_id), [seed.assignment_id]);
  assert.equal(app.state.selectedId, seed.assignment_id);
  assert.equal(app.state.detail, null);
  assert.equal(app.get("sources").children.at(-1).textContent, "Загрузить проект ещё раз");
  assert.match(app.get("upload-status").textContent, /удалены/);
  assert.equal(app.calls.some((call) => call.url === "/api/assignments"), false);
  assert.equal(app.state.deleting, false);
});

test("ending cancelled operations restores keyboard focus from the hidden cancel button", () => {
  for (const [cancelId, nextId] of [["cancel-button", "question"], ["upload-cancel", "upload-button"], ["analyze-cancel", "analyze-button"]]) {
    const app = harness(() => response(seed));
    app.state.detail = { ...uploaded, status: "ready" };
    app.get("upload-files").files = [{ name: "task.pdf", size: 100 }];
    app.get(nextId).hidden = false;
    app.get(cancelId).focus();
    app.updateControls();
    assert.equal(app.get(cancelId).hidden, true);
    assert.equal(app.focused(), nextId);
  }
});

test("discarding card edits restores the saved fields and keyboard focus", () => {
  const app = harness(() => response(seed));
  app.state.detail = { ...uploaded, status: "ready", card };
  app.state.cardDraft = card;
  app.state.cardDirty = true;
  app.get("card-goal").value = "Несохранённая цель";
  app.get("card-discard").focus();
  app.get("card-discard").listeners.click();
  assert.equal(app.state.cardDirty, false);
  assert.equal(app.get("card-goal").value, card.goal.text);
  assert.equal(app.get("card-discard").hidden, true);
  assert.equal(app.focused(), "card-goal");
});

test("async plan restores keyboard focus without stealing it after mouse, user movement or cancellation", async () => {
  const result = { status: "insufficient_time", available_minutes: 120, required_minutes: 570, deficit_minutes: 450, schedule: [] };
  for (const scenario of ["keyboard-success", "keyboard-error", "mouse", "moved", "moved-back-to-body", "cancelled"]) {
    let finish;
    const app = harness(() => new Promise((resolve) => { finish = resolve; }));
    app.state.detail = seed;
    app.state.selectedId = seed.assignment_id;
    app.get("plan-days").value = "1";
    app.get("plan-minutes").value = "120";
    app.get("plan-button").focus();
    app.get("plan-button").listeners.click?.({ detail: scenario === "mouse" ? 1 : 0 });
    const pending = app.submitPlan(event());
    assert.equal(app.focused(), "body", scenario + ": disabling the active button loses focus");
    if (scenario.startsWith("moved")) app.get("question").focus();
    if (scenario === "moved-back-to-body") app.get("body").focus();
    if (scenario === "cancelled") app.state.plan.abort();
    finish(scenario === "keyboard-error" ? unavailable() : { status: 200, body: result });
    await pending;
    if (scenario === "keyboard-success") {
      const heading = app.get("plan-result").children[0].children[0];
      assert.equal(app.active(), heading);
      assert.equal(heading.attributes.role, "heading");
      assert.equal(heading.attributes.tabindex, "-1");
    } else {
      assert.equal(app.focused(), scenario === "keyboard-error" ? "plan-button" : scenario === "moved" ? "question" : "body", scenario);
    }
    assert.equal(app.get("plan-button").disabled, false, scenario);
  }
});


test("action metadata is explicitly edited in the card and preserved on confirmation", () => {
  const app = harness(() => unavailable());
  app.state.cardDraft = {...structuredClone(card), requirements:[{id:"req-interviews",text:"Пять интервью",origin:"user",source_ids:[]}], tasks:[{id:"research",title:"Пять интервью",minutes:200,requirement_ids:["req-interviews"],expected_result:"Пять протоколов",done_when:"Есть I1–I5",estimate_basis:"5 × 40",depends_on:[]}]};
  app.get("card-title").value="Исследование";app.get("card-goal").value="Понять поиск";app.get("card-deliverable").value="Отчёт";
  app.get("card-requirements").value="Пять интервью";
  app.renderActionEditor();
  assert.doesNotMatch(app.get("card-actions-editor").textContent,/Какие требования покрывает/);
  assert.match(app.get("card-actions-editor").textContent,/Начать после/);
  const action = app.get("card-actions-editor").children[0].children[1];
  const resultInput = action.children[2].children[0];
  resultInput.value="Пять обезличенных протоколов";resultInput.listeners.input();
  assert.equal(app.state.cardDirty,true);
  assert.equal(app.readCardForm().tasks[0].expected_result,"Пять обезличенных протоколов");
  assert.deepEqual(Array.from(app.readCardForm().tasks[0].requirement_ids),["req-interviews"]);
  assert.equal(app.calls.length,0);
});

test("availability accepts busy days without changing other days", () => {
  const app=harness(()=>unavailable());app.get("plan-days").value="3";app.get("plan-minutes").value="60";app.renderAvailability();
  const days=app.get("availability-days").children;
  assert.equal(days.length,3);const input=days[1].children[0];input.value="0";input.listeners.input();
  app.renderAvailability();assert.equal(app.get("availability-days").children[1].children[0].value,0);
  assert.equal(app.get("availability-days").children[0].children[0].value,60);
});


test("editing custom requirement text keeps its stable ID and action links", () => {
  const app=harness(()=>unavailable());
  app.state.cardDraft={...structuredClone(card), requirements:[{id:"req-interviews",text:"Пять интервью",origin:"user",source_ids:[]}], tasks:[{id:"interviews",title:"Интервью",minutes:200,requirement_ids:["req-interviews"]}]};
  app.get("card-title").value="Исследование";app.get("card-goal").value="Понять поиск";app.get("card-deliverable").value="Отчёт";
  app.get("card-requirements").value="Пять интервью с фиксацией наблюдений";
  const edited=app.readCardForm();
  assert.equal(edited.requirements[0].id,"req-interviews");
  assert.equal(edited.tasks[0].requirement_ids[0],"req-interviews");
  app.get("card-requirements").value="";
  assert.throws(()=>app.readCardForm(),/Требование используется действием «Интервью», поэтому его нельзя удалить\./);
});


test("text creation waits until startup requests finish", async () => {
  let statusDone; let listDone;
  const app=harness((url)=>new Promise(resolve=>{ if(url==="/api/status")statusDone=resolve;else listDone=resolve; }));
  const ready=app.initialize();
  assert.equal(app.get("text-create").disabled,true);
  assert.equal(app.get("project-example").disabled,true);
  await app.get("text-project-form").listeners.submit(event());
  assert.equal(app.calls.length,2);
  statusDone({status:200,body:{mode:"offline"}});listDone({status:200,body:{assignments:[]}});
  await ready;
  assert.equal(app.get("text-create").disabled,false);
  assert.equal(app.get("project-example").disabled,false);
});

test("unsaved plan progress prevents switching until explicitly discarded", async () => {
  const app=harness(()=>response(seed));
  app.state.detail={...uploaded,status:"ready",card};app.state.planDirty=true;
  assert.equal(await app.selectAssignment(seed.assignment_id),false);
  assert.equal(app.calls.length,0);
  assert.match(app.get("plan-save-status").textContent,/Сначала сохраните план/);
  assert.equal(app.get("plan-discard").hidden,false);
});

test("text sources expose description instead of a fictitious PDF page", () => {
  const app=harness(()=>unavailable());
  const source=app.sourceCard({kind:"text",title:"Описание",page:1,excerpt:"План проекта",url:"/api/sources/text-1",document_url:"/material.pdf"});
  assert.match(source.textContent,/Описание проекта/);
  assert.doesNotMatch(source.textContent,/Стр\. 1|Страница PDF/);
});


test("confirming a card preserves saved progress for equivalent actions and current card estimates", async () => {
  const task={id:"interview",title:"Пять интервью",minutes:45,expected_result:"Протоколы",done_when:"Есть 5 записей",requirement_ids:[],estimate_basis:"Исходная оценка",min_minutes:30,max_minutes:60};
  const originalCard={...structuredClone(card),tasks:[task]};
  const changedCard={...structuredClone(originalCard),tasks:[{...task,minutes:90,estimate_basis:"Уточнённая оценка"}]};
  let confirmed=false;
  const plan={revision:1,input:{tasks:[{...task,completed:true,actual_minutes:40}],availability:[{day:1,minutes:60}],reserve_percent:0,start_date:null},result:{status:"feasible",required_minutes:0,available_minutes:60,deficit_minutes:0,schedule:[]}};
  const app=harness((url)=>{
    if(url.endsWith("/confirm")){confirmed=true;return response({...uploaded,status:"ready",card:changedCard,card_revision:2});}
    return {status:200,body:{plan:{...plan,stale:confirmed}}};
  });
  app.state.detail={...uploaded,status:"ready",card:originalCard,card_revision:1};
  app.state.cardDraft=structuredClone(originalCard);app.state.planTasks=[{...task,completed:false}];
  await app.loadSavedPlan(app.state.selection);
  assert.equal(app.state.planTasks[0].completed,true);
  app.get("card-title").value=card.title;app.get("card-goal").value=card.goal.text;app.get("card-deliverable").value=card.deliverable.text;
  app.state.cardDirty=true;
  await app.confirmCard(event());
  assert.equal(app.state.planTasks[0].completed,true);
  assert.equal(app.state.planTasks[0].actual_minutes,40);
  assert.equal(app.state.planTasks[0].minutes,90);
  assert.equal(app.state.planTasks[0].estimate_basis,"Уточнённая оценка");
  assert.equal(app.get("plan-result").hidden,true);
  assert.match(app.get("plan-save-status").textContent,/Прогресс неизменённых действий сохранён/);
});

test("stale progress resets changed outcomes and fresh saved metadata replaces old estimate ranges", () => {
  const app=harness(()=>unavailable());
  const old={id:"action",title:"Интервью",expected_result:"Записи",done_when:"5 записей",requirement_ids:["req-1"],minutes:45,completed:true,actual_minutes:40,min_minutes:30,max_minutes:60};
  app.state.planTasks=[{...old,expected_result:"Аналитический отчёт",minutes:90}];
  const reset=app.restoreSavedTasks({stale:true,input:{tasks:[old]}});
  assert.equal(reset[0],"Интервью");assert.equal(app.state.planTasks[0].completed,false);assert.equal(app.state.planTasks[0].actual_minutes,null);assert.equal(app.state.planTasks[0].minutes,90);
  app.restoreSavedTasks({stale:false,input:{tasks:[{...app.state.planTasks[0],minutes:20,min_minutes:null,max_minutes:null,estimate_basis:"Новая оценка остатка"}]}});
  assert.equal(app.state.planTasks[0].min_minutes,null);assert.equal(app.state.planTasks[0].max_minutes,null);assert.equal(app.state.planTasks[0].estimate_basis,"Новая оценка остатка");
});


test("exclusive views preserve dirty drafts and do not change from focus", () => {
  const app = harness(unavailable);
  app.state.detail = { ...uploaded, status: "ready", card };
  app.state.cardDirty = true;
  app.state.planDirty = true;
  app.get("card-goal").value = "Моя несохранённая цель";
  app.get("question").value = "Черновик вопроса";
  for (const view of ["overview", "planning", "card", "chat", "upload"]) {
    app.navigateWorkflow(view);
    assert.equal(app.state.workflow, view);
    for (const [panel, active] of [["overview-panel", "overview"], ["planning", "planning"], ["card-panel", "card"], ["chat-panel", "chat"], ["upload-panel", "upload"]]) assert.equal(app.get(panel).hidden, view !== active);
    assert.equal(app.get("card-goal").value, "Моя несохранённая цель");
    assert.equal(app.get("question").value, "Черновик вопроса");
    assert.equal(app.state.cardDirty, true);
    assert.equal(app.state.planDirty, true);
  }
  assert.equal(app.get("chat-panel").listeners.focusin, undefined);
});

test("overview next action advances immediately when completion changes", () => {
  const app = harness(unavailable);
  app.state.detail = { ...uploaded, status: "ready", card };
  app.state.planTasks = [
    { id: "a", title: "Первое действие", minutes: 30, expected_result: "Протокол", done_when: "Есть файл", depends_on: [] },
    { id: "b", title: "Второе действие", minutes: 40, depends_on: ["a"] },
  ];
  app.renderTaskEditor();
  assert.match(app.get("overview-focus").textContent, /Первое действие/);
  const checkbox = app.get("plan-tasks").children[0].children[0];
  checkbox.checked = true;
  checkbox.listeners.change();
  assert.match(app.get("overview-focus").textContent, /Второе действие/);
  assert.match(app.get("overview-metrics").textContent, /1 из 2/);
  assert.equal(app.state.planDirty, true);
});


test("overview effective capacity matches reserve rounding and excludes days after deadline", () => {
  const app = harness(unavailable);
  const plan = { input: { reserve_percent: 10, start_date: "2026-11-09" } };
  const assignment = { card: { deadline: { date: "2026-11-10" } } };
  assert.equal(app.effectiveDayMinutes({ day: 1, minutes: 61 }, plan, assignment), 54);
  assert.equal(app.effectiveDayMinutes({ day: 2, minutes: 0 }, plan, assignment), 0);
  assert.equal(app.effectiveDayMinutes({ day: 3, minutes: 60 }, plan, assignment), 0);
});

test("overview does not turn invalid pending estimates into available work totals", () => {
  const app = harness(unavailable);
  app.state.detail = { ...uploaded, status: "ready", card };
  app.state.planTasks = [{ id: "a", title: "Работа", minutes: 0 }];
  app.renderOverview();
  assert.match(app.get("overview-metrics").textContent, /Проверьте оценки/);
  assert.match(app.get("overview-focus").textContent, /Уточните оценку/);
  assert.match(app.get("overview-route").textContent, /Уточните оценку/);
  app.state.planTasks[0].completed = true;
  app.renderOverview();
  assert.match(app.get("overview-metrics").textContent, /1 из 1/);
  assert.doesNotMatch(app.get("overview-metrics").textContent, /Проверьте оценки/);
});

test("invalid controls reveal collapsed availability settings", () => {
  const app = harness(unavailable);
  const details = app.get("availability-settings"); details.open = false;
  app.get("plan-form").listeners.invalid({ target: { closest: () => details } });
  assert.equal(details.open, true);
});


test("legacy example overview uses real calculated tasks and marks the result as temporary", async () => {
  const result = { status: "feasible", required_minutes: 30, available_minutes: 60, deficit_minutes: 0, schedule: [{ day: 1, tasks: [{ id: "step", title: "Разобрать пример", minutes: 30 }] }], tasks: [{ id: "step", title: "Разобрать пример", minutes: 30 }] };
  const app = harness(() => ({ status: 200, body: result }));
  app.state.detail = seed;
  app.state.selectedId = seed.assignment_id;
  app.get("plan-days").value = "1";
  app.get("plan-minutes").value = "60";
  await app.submitPlan(event());
  assert.match(app.get("overview-focus").textContent, /Разобрать пример/);
  assert.match(app.get("overview-metrics").textContent, /0 из 1/);
  assert.match(app.get("overview-metrics").textContent, /не сохраняется/);
  assert.match(app.get("overview-capacity").textContent, /30 \/ 60 мин/);
});


test("stale saved plan hides daily budgets derived from a changed card deadline", async () => {
  const assignment = { ...uploaded, status: "ready", card: { ...card, deadline: { date: "2026-11-09" }, tasks: [{ id: "a", title: "Работа", minutes: 60 }] } };
  const saved = { revision: 1, stale: true, input: { start_date: "2026-11-09", reserve_percent: 0, availability: [{ day: 1, minutes: 60 }, { day: 2, minutes: 60 }], tasks: [{ id: "a", title: "Работа", minutes: 60 }] }, result: { status: "feasible", required_minutes: 60, available_minutes: 120, deficit_minutes: 0, schedule: [{ day: 1, tasks: [{ title: "Работа", minutes: 60 }] }] } };
  const app = harness((url) => url.endsWith("/plan") ? { status: 200, body: { plan: saved } } : response(assignment));
  await app.selectAssignment(uploaded.assignment_id, true);
  assert.match(app.get("overview-capacity").textContent, /Пересчитайте план после изменения карточки/);
  assert.equal(app.get("overview-capacity").children.some((child) => child.className === "capacity-day"), false);
  assert.match(app.get("overview-metrics").textContent, /по последнему расчёту/);
});


test("route rendering preserves the active diagram for unchanged state and invalidates dates after edits", () => {
  const renders = [];
  const app = harness(() => response(uploaded), { routeMap: { render: (container, options) => { renders.push(options); return { destroy() {} }; } } });
  app.state.detail = { ...uploaded, status: "ready", card: { ...card, requirements: [{ id: "req", text: "Проверить результат" }] } };
  app.state.planTasks = [{ id: "a", title: "Первое действие", minutes: 30, completed: false, depends_on: [], requirement_ids: ["req"] }];
  app.renderOverview();
  app.renderOverview();
  assert.equal(renders.length, 1, "a repeated controls update must not replace the selected diagram");
  assert.equal(renders[0].projectId, uploaded.assignment_id);
  assert.equal(renders[0].requirements[0].id, "req");
  assert.equal(app.get("overview-route").hidden, true);
  assert.equal(app.get("overview-route-diagram").hidden, false);
  app.state.planDirty = true;
  app.renderOverview();
  assert.equal(renders.length, 2);
  assert.equal(renders[1].stale, true, "unsaved progress must not retain apparently current dates");
  renders[1].onOpenTask({ id: "a" });
  assert.equal(app.state.workflow, "planning");
});

// The visible next step must use the same dependency-first priority as the scheduler.
test("overview advances the prerequisite of a high-priority task before independent low work", () => {
 const app = harness(unavailable); app.state.detail={...uploaded,status:"ready",card};
 app.state.planTasks=[{id:"low",title:"Low independent",minutes:30,priority:"low"},{id:"prerequisite",title:"Prepare high work",minutes:20,priority:"low"},{id:"high",title:"High dependent",minutes:30,priority:"high",depends_on:["prerequisite"]}];
 app.renderOverview();assert.match(app.get("overview-focus").textContent,/Prepare high work/);
 app.state.planTasks[1].completed=true;app.renderOverview();assert.match(app.get("overview-focus").textContent,/High dependent/);
});

test("unsaved progress blocks card confirmation and analysis rather than overwriting plan edits", async () => {
 const app=harness(unavailable);app.state.detail={...uploaded,status:"ready",card};app.state.cardDraft=card;app.state.planDirty=true;app.state.status={mode:"live",live_configured:true};
 app.updateControls();assert.equal(app.get("card-fields").disabled,true);assert.equal(app.get("analyze-button").disabled,true);
 await app.analyzeCard();await app.confirmCard(event());assert.equal(app.calls.length,0);assert.equal(app.state.planDirty,true);
});

test("daily plan shows concrete pieces, completion criteria and honest continuation", () => {
  const app = harness(unavailable);
  app.renderPlan({status:"feasible",required_minutes:25,available_minutes:25,deficit_minutes:0,
    coverage:{items:[{linked:true}]},assumptions:["Hidden technical assumption"],
    schedule:[{day:1,tasks:[{title:"Прототип",minutes:25,substeps:[
      {id:"screen",title:"Экран регистрации",minutes:10,done_when:"Все поля подписаны"},
      {id:"test",title:"Проверить сценарий",minutes:15,done_when:"Нет тупиков",continues:true},
    ]}]},{day:2,tasks:[]}]});
  const result = app.get("plan-result");
  const day = result.children.find(node => node.className === "plan-day");
  const list = day.querySelector(".day-steps");
  assert.equal(list.tagName,"ol");assert.equal(list.children.length,2);
  assert.match(day.textContent,/Экран регистрации.*10 мин.*Все поля подписаны/);
  assert.match(day.textContent,/Продолжится в следующий рабочий день.*Результат после всех частей: Нет тупиков/);
  assert.match(result.textContent,/Без работы · 0 мин/);
  assert.doesNotMatch(result.textContent,/Hidden technical assumption|Связи с требованиями/);
});

test("substep meaning changes reset progress but time-only changes preserve it", () => {
  const app = harness(unavailable);
  const old = {id:"a",title:"Прототип",minutes:30,expected_result:"Макет",done_when:"Можно пройти",completed:true,actual_minutes:28,
    substeps:[{id:"s",title:"Экран",minutes:30,done_when:"Есть все поля"}]};
  app.state.planTasks=[{...structuredClone(old),minutes:45,substeps:[{...old.substeps[0],minutes:45}]}];
  app.restoreSavedTasks({stale:true,input:{tasks:[old]}});
  assert.equal(app.state.planTasks[0].completed,true);assert.equal(app.state.planTasks[0].actual_minutes,28);
  app.state.planTasks[0].substeps[0].done_when="Есть все поля и ошибки валидации";
  app.restoreSavedTasks({stale:true,input:{tasks:[old]}});
  assert.equal(app.state.planTasks[0].completed,false);assert.equal(app.state.planTasks[0].actual_minutes,null);
});

test("substep editor updates parent time, bounds and editable criteria without a request", () => {
  const app = harness(unavailable);
  const task = {id:"a",title:"Прототип",minutes:40,min_minutes:30,max_minutes:60,substeps:[
    {id:"s1",title:"Экран",minutes:30,done_when:"Есть поля"},{id:"s2",title:"Проверка",minutes:10,done_when:"Нет тупиков"}]};
  app.state.cardDraft={...structuredClone(card),tasks:[task]};app.renderActionEditor();
  const editor=app.get("card-actions-editor").querySelector(".substep-editor");
  const row=editor.children[1];const input=row.children[2].children[0];
  input.value="20";input.listeners.input();
  assert.equal(task.minutes,30);assert.equal(task.min_minutes,null);assert.equal(task.max_minutes,null);
  assert.equal(task.estimate_source,"user");assert.equal(app.state.cardDirty,true);
  const done=row.children[3].children[0];done.value="Все поля и подписи";done.listeners.input();
  assert.equal(task.substeps[0].done_when,"Все поля и подписи");
  task.minutes=7;app.normalizeSubsteps(task);
  assert.equal(task.substeps.reduce((sum,s)=>sum+s.minutes,0),7);assert.ok(task.substeps.every(s=>s.minutes>=1));
  const snapshot=JSON.stringify(task);app.normalizeSubsteps(task);assert.equal(JSON.stringify(task),snapshot);
  assert.equal(app.calls.length,0);
});

test("materials group PDF pages by file identity, keeping same-name files separate", () => {
  const app = harness(unavailable);
  const sources = [1, 2, 3].map(page => ({ source_id: `s${page}`, document_id: "one", kind: "pdf", title: "same.pdf", page, excerpt: "Описание. ".repeat(100), url: `/sources/s${page}`, document_url: "/materials/one.pdf" }));
  sources.push({ ...sources[0], source_id: "other", document_id: "two", document_url: "/materials/two.pdf" });
  const original = JSON.stringify(sources);
  app.renderMaterialResults(sources);
  const cards = app.get("material-results").children;
  assert.equal(cards.length, 2);
  assert.equal(cards[0].querySelector(".material-open").href, "http://localhost/materials/one.pdf#page=1");
  assert.equal(cards[1].querySelector(".material-open").href, "http://localhost/materials/two.pdf#page=1");
  assert.ok(cards[0].querySelector(".material-description").textContent.length <= 180);
  assert.equal(JSON.stringify(sources), original);
});

test("material search retains original page links while grouping matching chunks", () => {
  const app = harness(unavailable);
  const sources = [1, 5, 6].map(page => ({ source_id: `page-${page}`, kind: "pdf", file_url: "/materials/seed.pdf", title: "Seed", page, excerpt: `Page ${page}`, url: `/sources/page-${page}` }));
  app.state.detail = { ...uploaded, sources };
  app.renderMaterialResults(sources.slice(1), "query");
  const cards = app.get("material-results").children;
  assert.equal(cards.length, 1);
  assert.equal(cards[0].querySelector(".material-open").href, "http://localhost/materials/seed.pdf#page=5");
  assert.deepEqual(cards[0].querySelector(".material-matches").children.map(link => link.href), ["http://localhost/materials/seed.pdf#page=5", "http://localhost/materials/seed.pdf#page=6"]);
});

test("text imports combine sequential chunks but never combine equal titles across imports", () => {
  const app = harness(unavailable);
  const sources = [1, 2, 1, 2].map((n, i) => ({ source_id: `text-${i}`, kind: "text", title: "Бриф", section: `Дополнение · фрагмент ${n}`, excerpt: `Text ${i}`, url: `/sources/text-${i}` }));
  app.renderMaterialResults(sources);
  const cards = app.get("material-results").children;
  assert.equal(cards.length, 2);
  assert.equal(cards[0].querySelector(".material-open").dataset.materialText, "text-0");
  assert.equal(cards[1].querySelector(".material-open").dataset.materialText, "text-2");
});

test("chat hides technical citations and telemetry while retaining formulas and source links", () => {
  const app = harness(unavailable);
  const message = app.messageNode("assistant", "", true);
  app.fillAnswer(message, {answer: "Расчёт: x = a[0] + 2; Python 3.12 [src-ab123].", mode:"live", request_id:"private-request", elapsed_ms:2000, trace:[{name:"search_materials",duration_ms:42}], sources:[{source_id:"src-ab123",title:"Задание.pdf",page:2,url:"/sources/src-ab123"}]});
  assert.match(message.textContent, /x = a\[0\] \+ 2/);
  assert.match(message.textContent, /Python 3.12/);
  assert.doesNotMatch(message.textContent, /src-ab123|private-request|Как получен ответ|search_materials/);
  assert.equal(message.querySelector(".source-chip").href, "http://localhost/sources/src-ab123");
});

test("daily recommendation uses remaining work, reserve and busy days without losing overrides", () => {
  const app = harness(unavailable);
  app.state.detail = {...uploaded,status:"ready"};
  app.state.planTasks = [{id:"a",minutes:120},{id:"b",minutes:600,completed:true}];
  app.get("plan-days").value = 2;
  app.get("plan-minutes").value = 60;
  app.get("plan-reserve").value = 20;
  app.renderAvailability();
  assert.match(app.get("daily-recommendation").textContent, /75 мин\/день/);
  assert.match(app.get("daily-recommendation").textContent, /Не хватает 24 мин/);
  const first = app.get("availability-days").children[0].children[0];
  first.value = 0; first.listeners.input();
  app.get("plan-minutes").value = 180;
  app.get("plan-minutes").listeners.input();
  assert.equal(app.get("availability-days").children[0].children[0].value, 0);
  assert.match(app.get("daily-recommendation").textContent, /150 мин\/день/);
  assert.match(app.get("availability-overrides-note").textContent, /Отдельные дни настроены вручную: 1/);
  assert.equal(app.get("availability-overrides-note").hidden, false);
});

test("saved zero reserve remains zero and busy day survives daily limit edit", async () => {
  const saved = {revision:1,input:{tasks:[],availability:[{day:1,minutes:0},{day:2,minutes:60}],reserve_percent:0,start_date:null},result:{status:"feasible",schedule:[]}};
  const app = harness(()=>({status:200,body:{plan:saved}}));
  app.state.detail = {...uploaded,status:"ready",card:structuredClone(card)};
  app.get("plan-reserve").value = 20;
  await app.loadSavedPlan(app.state.selection);
  assert.equal(app.get("plan-reserve").value, 0);
  app.get("plan-minutes").value = 120; app.get("plan-minutes").listeners.input();
  assert.equal(app.get("availability-days").children[0].children[0].value, 0);
  assert.equal(app.get("availability-days").children[1].children[0].value, 60);
});


test("theory GET is local-on-demand; explicit POST refreshes, preserving plan progress", async () => {
  const app = harness(() => ({status:200,body:{assignment_id:'upload-a',status:'ok',groups:[{task_id:'a',resources:[{title:'Основы',excerpt:'Объяснение'}]}],notice:'Подборка готова'}}));
  app.state.detail = {...uploaded,status:'confirmed',card_revision:2,card:{...card,tasks:[]}};
  app.state.planTasks = [{id:'a',title:'Действие',minutes:30,completed:true,actual_minutes:35,depends_on:[]}];
  const before = JSON.stringify(app.state.planTasks);
  await app.loadRouteTheory();
  await app.loadRouteTheory();
  assert.equal(app.calls.length,1);
  assert.equal(app.calls[0].url,'/api/assignments/upload-a/theory');
  assert.equal(app.calls[0].options.method,undefined);
  await app.loadRouteTheory(true);
  assert.equal(app.calls.length,2);
  assert.equal(app.calls[1].options.method,'POST');
  assert.equal(app.calls[1].options.body,'{}');
  assert.equal(app.get('theory-find').disabled,false);
  assert.match(app.get('theory-status').textContent,/Подборка готова/);
  assert.equal(JSON.stringify(app.state.planTasks),before);
});

test("theory failure enables retry", async () => {
  const app = harness(unavailable);
  app.state.planTasks = [{id:'a',title:'Действие',minutes:30,depends_on:[]}];
  await app.loadRouteTheory(true);
  assert.equal(app.get('theory-find').disabled,false);
  assert.equal(app.get('theory-find').textContent,'Повторить подбор');
  assert.match(app.get('theory-status').textContent,/недоступен/);
});

test('a delayed theory result never changes the newly selected project',async()=>{
 let resolve;
 const app=harness(()=>new Promise(done=>{resolve=done;}));
 app.state.planTasks=[{id:'a',title:'Первое действие',minutes:30,depends_on:[]}];
 const pending=app.loadRouteTheory(true);
 app.state.selectedId='upload-b';app.state.detail={...uploaded,assignment_id:'upload-b'};
 app.get('theory-status').textContent='Другой проект';
 resolve({status:200,body:{groups:[],notice:'Старый результат'}});
 await pending;
 assert.equal(app.get('theory-status').textContent,'Другой проект');
});


test('web theory sources show origin, license and reference limitations as text',()=>{
 const app=harness(unavailable);
 const source={scope:'web_theory',provider:'Wikipedia',author:'Участники Википедии',license:'CC BY-SA',title:'Производная',excerpt:'Определение',reference_note:'Текст книги не загружен',url:'https://ru.wikipedia.org/wiki/Test'};
 const node=app.sourceCard(source);
 assert.equal(node.querySelector('.source-page').textContent,'Теория · Wikipedia');
 assert.match(node.textContent,/Участники Википедии · CC BY-SA/);
 assert.match(node.textContent,/Текст книги не загружен/);
});


test("saved chats load their messages safely and never revive an application token", async () => {
  const app = harness(url => ({status:200, body: url.endsWith('/chats') ? {sessions:[{id:'chat-a',title:'Первый вопрос',message_count:2}]} : {messages:[{role:'user',content:'Мой вопрос'}, {role:'assistant',content:'Ответ',result:{answer:'Сохранённый ответ <img>',mode:'live',sources:[],action_proposal:{token:'expired'}}}]}}));
  app.state.detail = seed;
  await app.loadChatSessions();
  assert.equal(app.state.chatId, 'chat-a');
  assert.match(app.get('conversation').textContent, /Мой вопрос/);
  assert.match(app.get('conversation').textContent, /Сохранённый ответ <img>/);
  assert.equal(app.state.chatLoading, false);
  assert.equal(app.get('chat-sessions-list').children[0].children[0].attributes['aria-pressed'], 'true');
  app.newChat();
  assert.equal(app.state.chatId, null);
  assert.equal(app.get('chat-welcome').hidden, false);
});

test("first question creates a saved session and supplies its ID instead of browser history", async () => {
  const app = harness((url, options) => ({status:200, body: url==='/api/chat' ? {answer:'Ответ',mode:'offline',sources:[]} : options.method==='POST' ? {session:{id:'chat-new'}} : {sessions:[{id:'chat-new',title:'Вопрос',message_count:2}]}}));
  app.state.detail=seed;
  await app.sendMessage('Вопрос');
  const request=app.calls.find(call=>call.url==='/api/chat');
  assert.equal(JSON.parse(request.options.body).chat_id,'chat-new');
  assert.equal(JSON.parse(request.options.body).history,undefined);
  assert.equal(app.state.chatId,'chat-new');
  assert.equal(app.state.chat,null);
});

test("late stored chat response cannot replace a newly selected project", async () => {
  let finish; const app=harness(()=>new Promise(resolve=>{finish=resolve;}));
  const pending=app.openChatSession('old');
  app.state.selection+=1; app.state.chatId=null; app.state.chatLoad+=1;
  finish({status:200,body:{messages:[{role:'assistant',content:'Чужой ответ'}]}});
  await pending;
  assert.equal(app.state.chatId,null);
  assert.doesNotMatch(app.get('conversation').textContent,/Чужой ответ/);
});

test("explicit estimate refinement stays blocked while card or plan changes are unsaved", () => {
  const app=harness(unavailable);
  app.state.detail={...uploaded,status:'ready',card};
  app.updateControls();
  assert.equal(app.get('refine-estimates').disabled,false);
  app.state.planDirty=true;app.updateControls();
  assert.equal(app.get('refine-estimates').disabled,true);
  app.get('refine-estimates').listeners.click();
  assert.equal(app.calls.length,0);
  app.state.planDirty=false;app.state.cardDirty=true;app.updateControls();
  assert.equal(app.get('refine-estimates').disabled,true);
  app.state.cardDirty=false;app.state.chatLoading=true;app.updateControls();
  assert.equal(app.get('refine-estimates').disabled,true);
});


test("deleting a chat requires explicit confirmation and clears the selected conversation", async () => {
  const app=harness((url,options)=>({status: options.method==='DELETE'?204:200,body:{sessions:[]}}));
  app.state.detail=seed;app.state.chatId='chat-delete';app.updateControls();
  app.get('delete-chat').listeners.click();
  assert.equal(app.get('chat-delete-confirmation').hidden,false);
  assert.equal(app.calls.length,0);
  await app.deleteChatSession();
  assert.ok(app.calls.some(c=>c.options.method==='DELETE'&&c.url.endsWith('/chat-delete')));
  assert.equal(app.state.chatId,null);
  assert.equal(app.state.chatLoading,false);
  assert.equal(app.get('chat-welcome').hidden,false);
});

test("a running answer blocks starting and deleting chats without clearing its history", () => {
  const app=harness(unavailable);app.state.detail=seed;app.state.chatId='busy';app.state.chat=new AbortController();
  app.state.history=[{role:'user',content:'Сохрани'}];app.updateControls();
  assert.equal(app.get('new-chat').disabled,true);assert.equal(app.get('delete-chat').disabled,true);
  app.newChat();assert.equal(app.state.chatId,'busy');assert.equal(app.state.history[0].content,'Сохрани');
});

test("restoring the session list blocks early sending until history has loaded", async () => {
  let finish;const app=harness(()=>new Promise(resolve=>{finish=resolve;}));app.state.detail=seed;
  const loading=app.loadChatSessions();
  assert.equal(app.state.chatLoading,true);assert.equal(app.get('send-button').disabled,true);
  await app.sendMessage('Не опережай историю');assert.equal(app.calls.length,1);
  finish({status:200,body:{sessions:[]}});await loading;
  assert.equal(app.state.chatLoading,false);assert.equal(app.get('send-button').disabled,false);
});


test("editing only actual minutes immediately blocks Codex acceptance through dirty context", () => {
  const contexts = [];
  const app = harness(unavailable, { codex: { setProject(context) { contexts.push(context); } } });
  app.state.detail = { ...uploaded, status: "ready", card };
  app.state.planTasks = [{ id: "a", title: "Первое действие", minutes: 30, actual_minutes: null, depends_on: [] }];
  app.renderTaskEditor(); app.updateControls();
  assert.equal(contexts.at(-1).planDirty, false);
  const row = app.get("plan-tasks").children[0];
  function findActual(node) {
    if (node.attributes?.["aria-label"] === "Фактические минуты: Первое действие") return node;
    return node.children?.map(findActual).find(Boolean);
  }
  const actual = findActual(row);
  actual.value = "17"; actual.listeners.input();
  assert.equal(app.state.planTasks[0].actual_minutes, 17);
  assert.equal(app.state.planDirty, true);
  assert.equal(contexts.at(-1).planDirty, true);
  assert.equal(app.calls.length, 0);
});
