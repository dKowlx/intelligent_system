"use strict";

const $ = (id) => document.getElementById(id);
const state = { chatId: null, chatSessions: [], chatLoad: 0, chatLoading: false, assignments: [], selectedId: null, detail: null, status: null, selection: 0, chat: null, plan: null, upload: null, search: null, analysis: null, confirmation: null, cardDraft: null, cardDirty: false, cardExpanded: true, planTasks: [], planVersion: 0, deleting: false, loading: false, history: [], workflow: "upload", initializing: false, textCreating: false, planDirty: false };
let savedPlan = null;
let legacyPlan = null;
let availability = [];
let planKeyboardActivation = false;
let conversationAtBottom = true;
let routeView = "diagram";
try {
  if (window.localStorage.getItem("project-navigator-route-view") === "list") routeView = "list";
} catch (_) { /* The route remains usable when browser storage is restricted. */ }
let routeSignature = null;
const theoryByProject = new Map();
const theoryPending = new Set();
function theoryKey() { return `${state.selectedId}:${state.detail?.card_revision || 0}`; }
function renderTheoryStatus(message = '', busy = false, failed = false) {
  const button = $('theory-find'), status = $('theory-status');
  if (!button || !status) return;
  button.disabled = busy || !isUploaded() || !state.planTasks.length;
  button.textContent = busy ? 'Подбираем теорию…' : failed ? 'Повторить подбор' : 'Подобрать теорию';
  button.setAttribute('aria-busy', String(busy)); status.textContent = message;
  status.classList.toggle('inline-error', failed);
}
async function loadRouteTheory(refresh = false) {
  if (!isUploaded() || !state.selectedId || !state.planTasks.length) return;
  const key = theoryKey(), id = state.selectedId;
  if (theoryPending.has(key) || (!refresh && theoryByProject.has(key))) return;
  theoryPending.add(key);
  renderTheoryStatus(refresh ? 'Ищем открытые объяснения по темам действий. Материалы проекта остаются в проекте.' : 'Связываем действия с материалами…', true);
  try {
    const data = await requestJSON(`/api/assignments/${encodeURIComponent(id)}/theory`, refresh ? {method: 'POST', body: JSON.stringify({})} : {}, refresh ? 20000 : 10000);
    theoryByProject.set(key, data);
    if (id !== state.selectedId || key !== theoryKey()) return;
    routeSignature = null; renderOverview();
    renderTheoryStatus(data.notice || (refresh ? 'Подборка готова. Откройте теорию под действием.' : 'Объяснения связаны с действиями. Можно дополнить подборку открытыми источниками.'));
  } catch (error) {
    if (id === state.selectedId && key === theoryKey()) renderTheoryStatus(error.message, false, true);
  } finally { theoryPending.delete(key); }
}


class ApiError extends Error {
  constructor(message, code = "request_failed", requestId = null, status = null) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.requestId = requestId;
    this.status = status;
  }
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function cleanTechnicalText(text) {
  return String(text ?? "").replace(/\[src-[a-z0-9-]+\]/gi, "").replace(/[ \t]+([.,;:!?])/g, "$1");
}

function renderStructuredText(target, text) {
  if (typeof StudyTextFormat !== "undefined" && typeof StudyTextFormat.render === "function") StudyTextFormat.render(target, text);
  else target.textContent = String(text ?? "");
}

function scrollBehavior() { return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"; }

function focusSection(id) {
  const target = $(id);
  const step = { "upload-title": "upload", "materials-title": "card", "overview-heading": "overview", "card-heading": "card", "plan-title": "planning", "chat-title": "chat", "analytics-heading": "analytics" }[id];
  if (step) { state.workflow = step; updateWorkflow(); }
  target.setAttribute("tabindex", "-1");
  target.focus({ preventScroll: true });
  target.scrollIntoView({ behavior: scrollBehavior(), block: "start" });
}

function setUploadExpanded(expanded) {
  $("upload-body").hidden = !expanded;
  $("upload-toggle").setAttribute("aria-expanded", String(expanded));
  $("upload-toggle").textContent = expanded ? "Свернуть" : "Добавить PDF";
}

function collapseUploadIfIdle() {
  if (!state.upload && !$("upload-files").files.length && !$("upload-name").value.trim()) setUploadExpanded(false);
}

function updateCardVisibility() {
  const collapsible = isUploaded() && state.detail?.status === "ready" && Boolean(state.cardDraft) && !state.cardDirty && !state.analysis && !state.confirmation;
  const expanded = !collapsible || state.cardExpanded;
  $("card-body").hidden = !expanded;
  $("card-toggle").hidden = !collapsible;
  $("card-toggle").setAttribute("aria-expanded", String(expanded));
  $("card-toggle").textContent = expanded ? "Свернуть карточку" : "Изменить карточку";
  $("card-summary").hidden = !collapsible || expanded;
  $("card-summary").textContent = `Результат: ${String(state.cardDraft?.deliverable?.text || "карточка подтверждена").slice(0, 180)}`;
}

function collapseCardIfReady() {
  if (state.detail?.status === "ready" && !state.cardDirty && !state.analysis && !state.confirmation) state.cardExpanded = false;
  updateCardVisibility();
}

function workflowAvailability() {
  const loaded = Boolean(state.detail) && !state.loading;
  const ready = loaded && !materialOnly();
  return { upload: true, overview: ready, card: loaded, planning: ready, chat: ready, analytics: ready };
}

function renderViewVisibility() {
  const view = state.workflow;
  $("workspace").dataset.view = view;
  if (view === "upload") {
    $("assignment-title").textContent = "Новый проект";
    $("assignment-description").textContent = "Опишите результат и требования — или начните с материалов.";
  } else if (state.detail) {
    $("assignment-title").textContent = state.detail.title;
    $("assignment-description").textContent = state.detail.card?.goal?.text || state.detail.description || "Требования, действия и доступное время в одном проекте.";
  }
  const loaded = Boolean(state.detail) && !state.loading;
  const ready = loaded && !materialOnly();
  $("upload-panel").hidden = view !== "upload";
  $("overview-panel").hidden = view !== "overview" || !ready;
  $("card-panel").hidden = view !== "card" || !loaded || !isUploaded();
  $("materials-panel").hidden = view !== "card" || !loaded || !isUploaded();
  $("planning").hidden = view !== "planning" || !ready;
  $("chat-panel").hidden = view !== "chat" || !ready;
  $("context-column").hidden = !loaded || !(view === "card" && !isUploaded());
  $("sources-panel").hidden = $("context-column").hidden;
  if ($("analytics-panel")) $("analytics-panel").hidden = view !== "analytics" || !ready;
  $("workspace-grid").hidden = view === "upload" || !loaded;
  $("workspace-grid").dataset.view = view;
}

function updateWorkflow() {
  const available = workflowAvailability();
  if (!available[state.workflow]) state.workflow = available.card ? "card" : "upload";
  document.querySelectorAll("#workflow-nav [data-workflow]").forEach((button) => {
    const step = button.dataset.workflow;
    button.disabled = !available[step];
    const current = state.workflow === step;
    button.setAttribute("aria-selected", String(current));
    button.tabIndex = current || (state.workflow === "upload" && step === "card") ? 0 : -1;
    button.classList.toggle("is-current", current);
    if (current) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  renderViewVisibility();
}

function navigateWorkflow(step) {
  if (!workflowAvailability()[step]) return;
  state.workflow = step;
  if (step === "upload") setUploadExpanded(true);
  if (step === "card") {
    state.cardExpanded = true;
    updateCardVisibility();
    $("materials-body").hidden = false;
    $("materials-toggle").setAttribute("aria-expanded", "true");
    $("materials-toggle").textContent = "Скрыть материалы";
  }
  updateWorkflow();
  if (step === "analytics") window.ProjectWorkspace?.loadAnalytics();
  if (step === "overview") loadRouteTheory();
  focusSection({ overview: "overview-heading", upload: "upload-title", card: isUploaded() ? "card-heading" : "assignment-details", planning: "plan-title", chat: "chat-title", analytics: "analytics-heading" }[step]);
}

function openPlanTask(task) {
  navigateWorkflow("planning");
  const index = state.planTasks.indexOf(task);
  const row = $("plan-tasks").children[index];
  if (row) { row.setAttribute("tabindex", "-1"); row.focus({ preventScroll: true }); row.scrollIntoView({ behavior: scrollBehavior(), block: "center" }); }
}

function overviewButton(text, action) {
  const button = element("button", "button button-primary", text);
  button.type = "button";
  button.addEventListener("click", action);
  return button;
}

function effectiveDayMinutes(day, plan = savedPlan, assignment = state.detail) {
  const reserve = Number(plan?.input?.reserve_percent || 0);
  const start = plan?.input?.start_date;
  const deadline = assignment?.card?.deadline?.date;
  if (start && deadline) {
    const date = new Date(`${start}T12:00:00Z`);
    date.setUTCDate(date.getUTCDate() + day.day - 1);
    if (date.toISOString().slice(0, 10) > deadline) return 0;
  }
  return Math.floor(day.minutes * (100 - reserve) / 100);
}

function taskEstimateLabel(task) {
  const minutes = Number(task.minutes);
  return Number.isInteger(minutes) && minutes > 0 && minutes <= 4800 ? formatMinutes(minutes) : "Уточните оценку";
}

function updateRouteVisibility() {
  const available = typeof window.NavigatorRouteMap?.render === "function";
  $("overview-route-diagram").hidden = routeView !== "diagram" || !available;
  $("overview-route").hidden = routeView === "diagram" && available;
  document.querySelectorAll("[data-route-view]").forEach((button) => {
    button.disabled = button.dataset.routeView === "diagram" && !available;
    button.setAttribute("aria-pressed", String(button.dataset.routeView === (available ? routeView : "list")));
  });
}

function renderRouteDiagram(tasks, requirements, result, stale) {
  updateRouteVisibility();
  if (typeof window.NavigatorRouteMap?.render !== "function") return;
  const theory = theoryByProject.get(theoryKey())?.groups || [];
  const signature = JSON.stringify([state.selectedId, tasks, requirements, result, stale, theory]);
  if (signature === routeSignature) return;
  window.NavigatorRouteMap.render($("overview-route-diagram"), {
    projectId: state.selectedId, tasks, requirements, result, stale, theory,
    onOpenTask: (task) => {
      const current = state.planTasks.find((item) => item.id === task.id);
      if (current) openPlanTask(current);
    },
  });
  routeSignature = signature;
}

function nextProjectTask(tasks) {
  const pending = tasks.filter(task => !task.completed), byId = new Map(tasks.map(task => [task.id, task]));
  const rank = task => ({high:0,medium:1,low:2})[task.priority || "medium"] ?? 1;
  const urgency = (task, seen = new Set()) => {
    if (seen.has(task.id)) return rank(task);
    const path = new Set(seen); path.add(task.id);
    return Math.min(rank(task), ...pending.filter(other => (other.depends_on || []).includes(task.id)).map(other => urgency(other, path)));
  };
  return pending.filter(task => !(task.depends_on || []).some(id => !byId.get(id)?.completed)).sort((a,b) => urgency(a)-urgency(b))[0];
}

function renderOverview() {
  const assignment = state.detail;
  const tasks = state.planTasks;
  const theoryData = theoryByProject.get(theoryKey());
  renderTheoryStatus(theoryData?.notice || 'Теория к каждому действию: материалы проекта и открытые объяснения по теме.', theoryPending.has(theoryKey()));
  const pending = tasks.filter((task) => !task.completed);
  const blocked = (task) => (task.depends_on || []).some((id) => tasks.some((other) => other.id === id && !other.completed));
  const next = nextProjectTask(tasks);
  const result = isUploaded() ? savedPlan?.result : legacyPlan?.result;
  const planInput = isUploaded() ? savedPlan?.input : legacyPlan?.input;
  const validMinutes = pending.every((task) => Number.isInteger(Number(task.minutes)) && Number(task.minutes) > 0 && Number(task.minutes) <= 4800);
  const stale = state.planDirty || state.cardDirty || savedPlan?.stale;
  const metrics = $("overview-metrics");
  metrics.replaceChildren();
  for (const [label, value] of [
    ["Выполнено", tasks.length ? `${tasks.length - pending.length} из ${tasks.length}` : "Действия не заданы"],
    ["Осталось работы", !tasks.length ? "Оценка не задана" : validMinutes ? formatMinutes(pending.reduce((sum, task) => sum + Number(task.minutes), 0)) : "Проверьте оценки"],
    ["Доступно", result ? formatMinutes(result.available_minutes) : "План не рассчитан"],
    ["Дефицит", result ? formatMinutes(result.deficit_minutes) : "—"],
  ]) { const metric = element("div", "metric-item"); metric.append(element("span", "metric-label", label), element("strong", "metric-value", value)); metrics.append(metric); }
  if (stale) metrics.append(element("p", "overview-stale", result ? "Есть изменения · вместимость показана по последнему расчёту" : "Есть изменения · план ещё не рассчитан"));
  if (legacyPlan && !isUploaded()) metrics.append(element("p", "overview-stale", "Расчёт примера действует до перезагрузки страницы и не сохраняется."));
  const focus = $("overview-focus"); focus.replaceChildren();
  focus.className = "focus-task";
  focus.append(element("p", "eyebrow", "Ближайшее действие"));
  if (next) {
    focus.append(element("h2", "", next.title), element("p", "focus-result", next.expected_result || (isUploaded() ? "Уточните ожидаемый результат в карточке проекта." : "Откройте план и проверьте содержание этапа по материалам примера.")));
    if (next.done_when) focus.append(element("p", "focus-done", `Готово, когда: ${next.done_when}`));
    focus.append(element("p", "focus-meta", `${taskEstimateLabel(next)} · предыдущие действия выполнены или не требуются`), overviewButton("Открыть действие →", () => openPlanTask(next)));
  } else {
    if (!isUploaded() && !tasks.length) {
      focus.append(element("h2", "", "Рассчитайте план примера"), element("p", "", "Укажите доступное время. Этот демонстрационный расчёт не сохраняется после перезагрузки."), overviewButton("Составить план →", () => navigateWorkflow("planning")));
    } else {
    focus.append(element("h2", "", tasks.length && !pending.length ? "Все действия завершены" : pending.length ? "Проверьте зависимости действий" : "Составьте маршрут проекта"));
    focus.append(element("p", "", tasks.length && !pending.length ? "Проверьте итоговый результат по требованиям проекта." : "Проверьте карточку и добавьте действия с понятным результатом."), overviewButton("Открыть проект →", () => navigateWorkflow("card")));
  }
    }
  const route = $("overview-route"); route.replaceChildren();
  tasks.forEach((task, index) => {
    const button = overviewButton("", () => openPlanTask(task));
    button.className = `route-item${task.completed ? " is-complete" : ""}`;
    const copy = element("span", "route-copy");
    copy.append(element("strong", "", task.title), element("span", "route-status", task.completed ? "Выполнено" : blocked(task) ? "После предыдущих действий" : "Можно выполнять"));
    button.append(element("span", "route-marker", task.completed ? "✓" : String(index + 1).padStart(2, "0")), copy, element("span", "route-duration", taskEstimateLabel(task)));
    const item = element('div', 'route-list-action'); item.append(button);
    if (window.NavigatorRouteMap?.renderTheory) {
      const reading = element('details', 'route-list-theory');
      reading.append(element('summary', '', 'Теория и объяснения'));
      const content = element('div', 'route-theory-reading');
      window.NavigatorRouteMap.renderTheory(content, theoryData?.groups?.find(group => group.task_id === task.id), task);
      reading.append(content); item.append(reading);
    }
    route.append(item);
  });
  if (!tasks.length) route.append(element("p", "empty-note", isUploaded() ? "Действия появятся после подтверждения карточки." : "Маршрут примера появится после расчёта плана."));
  const capacity = $("overview-capacity"); capacity.replaceChildren();
  if (result) {
    capacity.append(element("p", "capacity-summary", result.deficit_minutes ? `Не помещается ${formatMinutes(result.deficit_minutes)}` : "Работа помещается в доступное время"));
    if (planInput?.reserve_percent) capacity.append(element("p", "empty-note", `Доступность показана после резерва ${planInput.reserve_percent}%. Дни после известного срока исключены.`));
    if (isUploaded() && savedPlan?.stale) capacity.append(element("p", "overview-stale", "Пересчитайте план после изменения карточки"));
    (isUploaded() && savedPlan?.stale ? [] : planInput?.availability || []).forEach((day) => {
      const effective = effectiveDayMinutes(day, { input: planInput });
      const scheduled = result.schedule?.find((item) => item.day === day.day);
      const used = (scheduled?.tasks || []).reduce((sum, task) => sum + task.minutes, 0);
      const row = element("div", "capacity-day");
      const label = element("div", "capacity-label");
      label.append(element("span", "", `День ${day.day}`), element("span", "", `${used} / ${effective} мин`));
      const bar = element("div", "capacity-track");
      const fill = element("span", "capacity-fill");
      fill.style.width = `${Math.min(100, used / Math.max(effective, 1) * 100)}%`;
      bar.append(fill);
      bar.setAttribute("role", "img");
      bar.setAttribute("aria-label", `День ${day.day}: запланировано ${used}, доступно ${effective} минут`);
      row.append(label, bar);
      capacity.append(row);
    });
  } else capacity.append(element("p", "empty-note", "Укажите доступность по дням, чтобы проверить, хватит ли времени."));
  capacity.append(overviewButton(result ? "Изменить доступность" : "Рассчитать план →", () => { navigateWorkflow("planning"); $("availability-settings").open = true; $("plan-days").focus(); }));
  const outcome = $("overview-outcome"); outcome.replaceChildren(element("p", "", assignment?.card?.deliverable?.text || assignment?.description || "Уточните итоговый результат в карточке проекта."));
  const requirements = assignment?.card?.requirements || assignment?.requirements || [];
  if (requirements.length) {
    const list = element("ul", "outcome-requirements");
    requirements.forEach((item) => list.append(element("li", "", typeof item === "string" ? item : item.text)));
    outcome.append(list);
  }
  outcome.append(overviewButton("Требования и материалы →", () => navigateWorkflow("card")));
  renderRouteDiagram(tasks, requirements, result, Boolean(stale));
}

function userErrorMessage(error) {
  const messages = {
    eval_budget_exceeded: "Достигнут тестовый лимит AI-запросов приложения. Владельцу сервиса нужно проверить лимит расходов AI. Материалы и план остаются доступны; карточку своих файлов можно заполнить вручную.",
    eval_budget_invalid: "AI временно недоступен: владельцу сервиса нужно проверить настройки лимита. Можно продолжить с ручной карточкой.",
    provider_auth: "Нет доступа к модели. Владельцу сервиса нужно проверить подключение. Можно продолжить с ручной карточкой.",
    provider_rate_limit: "Сервис модели временно ограничил запросы. Попробуйте позже или заполните карточку вручную.",
  };
  return cleanTechnicalText(messages[error.code] || error.message);
}

async function requestJSON(path, options = {}, timeout = 50000) {
  const controller = new AbortController();
  const upstream = options.signal;
  let timedOut = false;
  const cancel = () => controller.abort();
  if (upstream?.aborted) throw new ApiError("Запрос отменён.", "cancelled");
  upstream?.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeout);
  try {
    const jsonBody = options.body && !(options.body instanceof FormData);
    const response = await fetch(path, { ...options, signal: controller.signal, headers: { "Accept": "application/json", ...(jsonBody ? { "Content-Type": "application/json" } : {}), ...options.headers } });
    if (response.status === 204 && response.ok) return {};
    let data;
    try { data = await response.json(); } catch (_) { throw new ApiError("Сервис вернул ответ в неподдерживаемом формате.", "invalid_response"); }
    if (!response.ok) throw new ApiError(data?.error?.message || `Запрос не выполнен (HTTP ${response.status}).`, data?.error?.code || "request_failed", data?.request_id || null, response.status);
    if (!data || typeof data !== "object") throw new ApiError("Получен некорректный ответ сервиса.", "invalid_response");
    return data;
  } catch (error) {
    if (controller.signal.aborted) throw new ApiError(timedOut ? "Время ожидания истекло. Попробуйте ещё раз." : "Запрос отменён.", timedOut ? "request_timeout" : "cancelled");
    if (error instanceof ApiError) throw error;
    throw new ApiError("Не удалось связаться с сервисом. Проверьте подключение и повторите запрос.", "connection_error");
  } finally {
    clearTimeout(timer);
    upstream?.removeEventListener("abort", cancel);
  }
}

function sourceURL(value) {
  if (typeof value !== "string" || !value) return null;
  if (/^#demo-source-[A-Za-z0-9_-]+$/.test(value)) return value;
  try {
    const url = new URL(value, window.location.origin);
    return url.origin === window.location.origin && ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch (_) { return null; }
}

function sourceLocation(source) {
  if (source.scope === "web_theory") return `Теория · ${source.provider || "Wikipedia"}`;
  if (source.scope === "shared_research") return `Исследование · ${source.section || "фрагмент"}`;
  if (source.kind === "text") return source.section || "Описание проекта";
  if (Number.isInteger(source.page) && source.page > 0) return `Стр. ${source.page}`;
  return typeof source.section === "string" && source.section ? source.section : "Материал";
}

function sourceAttribution(source) {
  if (source.scope !== "web_theory") return "";
  return `${source.provider || "Wikipedia"} · ${source.author || "Участники Википедии"} · ${source.license || "CC BY-SA"}. Текст сокращён до выдержки; оригинал доступен по ссылке.`;
}

function dateLabel(assignment) {
  const value = assignment.due_date || assignment.due_at;
  if (!value) return "Срок проекта не указан";
  const date = new Date(assignment.due_date ? `${assignment.due_date}T12:00:00` : value);
  return Number.isNaN(date.getTime()) ? "Дата требует уточнения" : `Завершить до ${date.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" })}`;
}

function formatMinutes(value) {
  const minutes = Number.isFinite(value) ? Math.max(0, Math.round(value)) : 0;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return hours ? `${hours} ч${rest ? ` ${rest} мин` : ""}` : `${rest} мин`;
}

function showMode(status, assignment = state.detail) {
  state.status = status;
  $("mode-badge").hidden = true;
  const unavailable = status.mode === "live" && !status.live_configured;
  $("mode-notice").hidden = !unavailable;
  $("mode-notice").className = "mode-notice is-error";
  $("mode-notice").textContent = unavailable ? "Ассистент пока недоступен. Проверьте подключение в настройках сервиса." : "";
}

function materialOnly(assignment = state.detail) {
  return Boolean(assignment?.status && assignment.status !== "ready");
}

function isUploaded(assignment = state.detail) {
  return Boolean(assignment?.is_uploaded || assignment?.assignment_id?.startsWith("upload-"));
}

function updateControls() {
  const creationBusy = Boolean(state.initializing || state.textCreating);
  if ($("text-create")) $("text-create").disabled = creationBusy;
  if ($("project-example")) $("project-example").disabled = creationBusy;
  if ($("plan-discard")) $("plan-discard").hidden = !state.planDirty;
  const focused = document.activeElement;
  const cardBusy = Boolean(state.analysis || state.confirmation);
  const ready = Boolean(state.detail) && !state.loading && !materialOnly() && !state.cardDirty && !cardBusy;
  $("question").disabled = !ready || state.chatLoading;
  if ($("regroup-actions")) $("regroup-actions").disabled = !ready || Boolean(state.chat) || state.chatLoading;
  $("send-button").disabled = !ready || Boolean(state.chat) || state.chatLoading;
  $("send-button").hidden = Boolean(state.chat);
  $("cancel-button").hidden = !state.chat;
  document.querySelectorAll("[data-prompt]").forEach((button) => { button.disabled = !ready || Boolean(state.chat) || state.chatLoading; });
  $("plan-button").disabled = !ready || Boolean(state.plan);
  $("plan-button").textContent = state.plan ? "Сохраняем план…" : isUploaded() ? "Рассчитать и сохранить →" : "Составить план →";
  $("refine-estimates").disabled = !ready || !isUploaded() || state.planDirty || Boolean(state.plan) || Boolean(state.chat) || state.chatLoading;
  $("delete-chat").disabled = !ready || !state.chatId || Boolean(state.chat) || state.chatLoading;
  $("new-chat").disabled = !ready || Boolean(state.chat) || state.chatLoading;
  $("chat-form").setAttribute("aria-busy", String(Boolean(state.chat)));
  $("plan-form").setAttribute("aria-busy", String(Boolean(state.plan)));
  const planFieldsBlocked = state.cardDirty || cardBusy || state.loading || Boolean(state.plan);
  document.querySelectorAll("#plan-form input").forEach((input) => {
    const completedEstimate = input.getAttribute("aria-label")?.startsWith("Минут на задачу") && input.closest(".plan-task")?.classList.contains("is-complete");
    input.disabled = planFieldsBlocked || Boolean(completedEstimate);
  });
  if (state.planDirty) $("card-status").textContent = "Сохраните время и прогресс в разделе «Действия и план», прежде чем менять карточку.";
  else if ($("card-status").textContent === "Сохраните время и прогресс в разделе «Действия и план», прежде чем менять карточку.") $("card-status").textContent = "";

  const uploading = Boolean(state.upload);
  $("upload-button").disabled = uploading || !$("upload-files").files.length;
  $("upload-button").textContent = uploading ? "Загружаем и извлекаем текст…" : "Загрузить материалы ↑";
  $("upload-cancel").hidden = !uploading;
  $("upload-name").disabled = uploading;
  $("upload-files").disabled = uploading;
  $("upload-reset").hidden = !$("upload-files").files.length || uploading;
  $("upload-form").setAttribute("aria-busy", String(uploading));
  $("material-search-button").disabled = state.loading || Boolean(state.search);
  $("material-search-button").textContent = state.search ? "Ищем…" : "Найти";
  $("material-search-form").setAttribute("aria-busy", String(Boolean(state.search)));
  $("delete-assignment").disabled = state.deleting;
  $("delete-confirm").disabled = state.deleting;
  $("delete-cancel").disabled = state.deleting;
  $("delete-confirm").textContent = state.deleting ? "Удаляем…" : "Да, удалить";
  $("card-fields").disabled = cardBusy || state.loading || state.planDirty;
  document.querySelectorAll("#card-actions-editor fieldset").forEach((fieldset) => { fieldset.disabled = cardBusy || state.loading || state.planDirty; });
  if ($("add-card-action")) $("add-card-action").disabled = cardBusy || state.loading || state.planDirty;
  $("analyze-button").disabled = !isUploaded() || state.loading || cardBusy || state.cardDirty || state.planDirty;
  $("analyze-button").textContent = state.analysis ? "Получаем карточку…" : state.status?.mode === "live" ? state.cardDraft ? "Повторить AI-разбор" : "Разобрать материалы" : "Заполнить карточку";
  $("analyze-button").hidden = state.status?.mode !== "live" && Boolean(state.cardDraft);
  $("analyze-cancel").hidden = !state.analysis;
  $("manual-card-button").hidden = !isUploaded() || Boolean(state.cardDraft);
  $("manual-card-button").disabled = !state.detail || state.loading || cardBusy;
  $("card-confirm").disabled = !state.cardDraft || state.loading || cardBusy || (state.detail?.status === "ready" && !state.cardDirty);
  $("card-confirm").textContent = state.confirmation ? "Сохраняем карточку…" : state.detail?.status === "ready" ? state.cardDirty ? "Подтвердить изменения и пересчитать план" : "Карточка подтверждена" : "Подтвердить карточку и перейти к плану";
  $("card-form").setAttribute("aria-busy", String(cardBusy));
  $("card-dirty").hidden = !state.cardDirty;
  $("card-discard").hidden = !state.cardDirty;
  $("card-discard").disabled = cardBusy;
  $("card-state").textContent = state.cardDirty ? "Есть изменения" : state.detail?.status === "ready" ? "Подтверждена" : state.cardDraft ? "Проверьте сведения" : "Нужна карточка";
  for (const [cancelId, nextId] of [["cancel-button", "question"], ["upload-cancel", "upload-button"], ["analyze-cancel", "analyze-button"]]) {
    if (focused === $(cancelId) && $(cancelId).hidden && !$(nextId).hidden && !$(nextId).disabled) $(nextId).focus();
  }
  updateCardVisibility();
  updateWorkflow();
  window.ProjectWorkspace?.updateControls();
  renderOverview();
}

function renderAssignments() {
  const list = $("assignments");
  list.replaceChildren();
  $("assignment-count").textContent = String(state.assignments.length);
  if (!state.assignments.length) list.append(element("li", "sidebar-placeholder", "Задания пока не добавлены."));
  state.assignments.forEach((assignment) => {
    const item = element("li");
    const button = element("button", "assignment-button");
    button.type = "button";
    button.dataset.assignmentId = assignment.assignment_id;
    button.setAttribute("aria-pressed", "false");
    const subtitle = isUploaded(assignment) ? assignment.status === "ready" ? "Карточка подтверждена" : assignment.status === "needs_confirmation" ? "Карточка на проверке" : "Свои материалы · текст готов" : `${assignment.demo ? "Демо · " : ""}${dateLabel(assignment)}`;
    button.append(element("span", "assignment-name", assignment.title), element("span", "assignment-subtitle", subtitle));
    const selected = assignment.assignment_id === state.selectedId;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-pressed", String(selected));
    button.addEventListener("click", () => { selectAssignment(assignment.assignment_id); });
    item.append(button);
    list.append(item);
  });
}

function sourceCard(source) {
  const card = element("article", "source-card");
  const heading = element("div", "source-heading");
  heading.append(element("h3", "source-title", source.title || "Материал проекта"), element("span", "source-page", sourceLocation(source)));
  card.append(heading);
  if (sourceAttribution(source)) card.append(element("p", "source-location", sourceAttribution(source)));
  if (source.reference_note) card.append(element("p", "empty-note", source.reference_note));
  if (source.excerpt) {
    const excerpt = element("div", "source-excerpt structured-text");
    renderStructuredText(excerpt, source.excerpt);
    card.append(excerpt);
  }
  const links = element("div", "source-links");
  for (const [value, label] of [[source.url, "Открыть фрагмент ↗"], [source.kind !== "text" && source.document_url ? `${source.document_url}#page=${source.page || 1}` : null, "Страница PDF ↗"]]) {
    const url = sourceURL(value);
    if (!url) continue;
    const link = element("a", "source-open", label);
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    links.append(link);
  }
  card.append(links);
  return card;
}

function renderSources(sources, fromAnswer = false) {
  const entries = Array.isArray(sources) ? sources : [];
  if (fromAnswer) $("sources-panel").hidden = true;
  $("sources-title").textContent = fromAnswer ? "Источники ответа" : "Материалы проекта";
  const groups = fromAnswer ? [] : materialGroups(entries, state.detail?.documents || []);
  $("source-count").textContent = String(fromAnswer ? entries.length : groups.length);
  const list = $("sources");
  list.replaceChildren();
  if (!entries.length) list.append(element("p", "empty-note", fromAnswer ? "В этом ответе нет подтверждающих фрагментов. Уточните вопрос или посмотрите материалы проекта." : "Источники для этого проекта пока отсутствуют."));
  if (fromAnswer) entries.forEach((source) => list.append(sourceCard(source)));
  else groups.forEach(group => list.append(materialCard(group)));
}

function renderRequirements(assignment) {
  const target = $("requirements");
  target.replaceChildren();
  const requirements = Array.isArray(assignment.requirements) ? assignment.requirements : [];
  const criteria = Array.isArray(assignment.criteria) ? assignment.criteria : [];
  if (requirements.length) {
    target.append(element("h3", "", "Что подготовить"));
    const list = element("ul");
    requirements.forEach((item) => list.append(element("li", "", typeof item === "string" ? item : item.text)));
    target.append(list);
  }
  if (criteria.length) {
    target.append(element("h3", "", "Как оценивается работа"));
    const list = element("ul");
    criteria.forEach((item) => list.append(element("li", "", `${item.title || item.text || "Критерий"}${Number.isFinite(item.max_points) ? ` — до ${item.max_points} баллов` : ""}`)));
    target.append(list);
  }
  $("assignment-details").hidden = !requirements.length && !criteria.length;
}

function materialGroups(sources, documents = []) {
  const groups = [], files = new Map();
  let previousText = null;
  for (const source of sources) {
    const file = source.kind !== "text" && (source.document_url || source.file_url);
    if (file || source.document_id) {
      previousText = null;
      const key = source.document_id || file;
      if (!files.has(key)) {
        const document = documents.find(doc => source.document_id ? doc.document_id === source.document_id : (doc.document_url || doc.url) === file);
        const group = { sources: [], document, file, title: document?.filename || source.title || "Материал PDF" };
        files.set(key, group); groups.push(group);
      }
      files.get(key).sources.push(source);
    } else {
      // Text imports have contiguous, numbered chunks; a new import restarts at 1.
      // Equal titles alone must never merge separate materials.
      const section = source.section?.match(/^(Текст проекта|Дополнение) · фрагмент (\d+)$/);
      const number = section ? Number(section[2]) : 0;
      if (previousText && number > 1 && number === previousText.number + 1 &&
          previousText.prefix === section[1] && previousText.title === source.title &&
          previousText.external === source.external_url) {
        previousText.group.sources.push(source); previousText.number = number;
      } else {
        const group = { sources: [source], title: source.title || "Описание проекта" };
        groups.push(group);
        previousText = section ? { group, number, prefix: section[1], title: source.title, external: source.external_url } : null;
      }
    }
  }
  return groups;
}

function materialDescription(value) {
  const text = String(value || "").replace(/\/x[\dA-Fa-f]{2}/g, " ").replace(/\s+/g, " ").trim();
  if (text.length <= 180) return text;
  const end = text.lastIndexOf(" ", 177);
  return text.slice(0, end > 100 ? end : 177).trimEnd() + "…";
}

function materialCard(group, matches = []) {
  const source = matches[0] || group.sources[0];
  const pdf = sourceURL(group.file);
  const page = Number.isInteger(source.page) && source.page > 0 ? source.page : 1;
  const url = pdf ? `${pdf.split("#")[0]}#page=${page}` : sourceURL(group.sources[0].url);
  const card = element("article", "source-card material-card");
  const metadata = element("div", "material-card-meta");
  const pageCount = group.document?.page_count;
  metadata.append(element("span", "material-kind", pdf ? "PDF" : "Текст"));
  if (pdf && Number.isInteger(pageCount)) metadata.append(element("span", "", `Страниц: ${pageCount}`));
  const title = element("h3", "material-card-title");
  const link = element(url ? "a" : "span", "source-open material-open", group.title);
  if (url) {
    link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
    if (pdf) link.dataset.materialPdf = url;
    else link.dataset.materialText = group.sources[0].source_id;
    link.dataset.materialTitle = group.title;
  }
  title.append(link);
  const description = materialDescription(source.summary || source.excerpt);
  card.append(metadata, title, element("p", "material-description", description || (pdf ? "Откройте документ, чтобы прочитать материал." : "Описание проекта.")));
  const footer = element("div", "material-card-footer");
  footer.append(element("span", "", pdf ? "Открыть PDF" : "Прочитать описание"), element("span", "", "↗"));
  footer.setAttribute("aria-hidden", "true"); card.append(footer);
  if (pdf && matches.length) {
    const pages = [...new Set(matches.map(item => item.page).filter(page => Number.isInteger(page) && page > 0))].sort((a, b) => a - b);
    const links = element("div", "material-matches");
    for (const page of pages) {
      const a = element("a", "source-open", `Стр. ${page}`);
      a.href = `${pdf.split("#")[0]}#page=${page}`; a.target = "_blank"; a.rel = "noopener noreferrer";
      a.dataset.materialPdf = a.href; a.dataset.materialTitle = group.title; links.append(a);
    }
    card.append(links);
  }
  return card;
}

function renderMaterialResults(sources, query = "", documents = state.detail?.documents || []) {
  const entries = Array.isArray(sources) ? sources : [];
  const all = query && state.detail?.sources?.length ? state.detail.sources : entries;
  const groups = materialGroups(all, documents);
  const matched = new Set(entries.map(source => source.source_id));
  const shown = query ? groups.filter(group => group.sources.some(source => matched.has(source.source_id))) : groups;
  const target = $("material-results"); target.replaceChildren();
  $("material-search-status").textContent = query
    ? shown.length ? `Найдено материалов: ${shown.length}.` : "Совпадений нет. Попробуйте другое слово из проекта."
    : `Материалов: ${shown.length}.`;
  for (const group of shown) target.append(materialCard(group, query ? entries.filter(source => group.sources.some(item => item.source_id === source.source_id)) : []));
  if (!shown.length && !query) target.append(element("p", "empty-note", "Добавьте файл или описание проекта."));
}

function renderDocuments(assignment) {
  renderMaterialResults(assignment.sources, "", assignment.documents || []);
}

function cloneData(value) { return JSON.parse(JSON.stringify(value)); }

function invalidatePlan(showMessage = false) {
  if (showMessage && state.detail?.status === "ready" && !state.cardDirty) {
    state.planDirty = true;
    if ($("plan-discard")) $("plan-discard").hidden = false;
  }
  const hadPlan = !$("plan-result").hidden || Boolean(state.plan);
  state.plan?.abort();
  state.plan = null;
  state.planVersion += 1;
  $("plan-result").hidden = true;
  $("plan-error").hidden = true;
  $("plan-stale").hidden = !(showMessage || hadPlan);
  renderOverview();
  renderDailyRecommendation();
  if ($("plan-save-status") && (showMessage || hadPlan)) $("plan-save-status").textContent = "Есть несохранённые изменения. Нажмите «Рассчитать и сохранить».";
}

function resetConversation() {
  state.chat?.abort();
  state.chat = null;
  state.history = [];
  state.chatId = null; state.chatSessions = []; state.chatLoad += 1; state.chatLoading = false;
  renderChatSessions();
  conversationAtBottom = true;
  $("conversation").querySelectorAll(".message").forEach((node) => node.remove());
  $("chat-welcome").hidden = false;
}

function cardField(value) {
  return value && typeof value === "object" ? value : { text: "", origin: "user", source_ids: [] };
}

function lineText(value) { return String(value || "").replace(/\s*\n\s*/g, " ").trim(); }

function renderCard(assignment, expanded = assignment.status !== "ready") {
  state.cardDraft = assignment.card ? cloneData(assignment.card) : null;
  state.cardDirty = false;
  state.cardExpanded = expanded;
  state.actionEditing = false;
  $("card-form").hidden = !state.cardDraft;
  $("card-error").hidden = true;
  $("card-reload").hidden = true;
  $("card-status").textContent = "";
  $("card-dirty").hidden = true;
  $("card-notice").textContent = "Проверьте предложенное описание по источникам. Исправления сохранятся как ваш ввод, неизвестную дату можно оставить пустой.";
  if (!state.cardDraft) {
    $("card-questions").hidden = true;
    state.planTasks = [];
    renderTaskEditor();
    return;
  }
  const card = state.cardDraft;
  $("card-title").value = card.title || assignment.title || "";
  $("card-goal").value = cardField(card.goal).text || "";
  $("card-deliverable").value = cardField(card.deliverable).text || "";
  $("card-requirements").value = (card.requirements || []).map((item) => lineText(item.text)).join("\n");
  $("card-requirements").setAttribute("aria-description", (card.requirements || []).map((item,index)=>`${item.id || `req-${index+1}`}: ${item.text}`).join("; "));
  $("card-criteria").value = (card.criteria || []).map((item) => lineText(item.text)).join("\n");
  $("card-deadline").value = card.deadline?.date || "";
  const questions = $("card-questions");
  questions.replaceChildren();
  const pending = Array.isArray(card.questions) ? card.questions : [];
  questions.hidden = !pending.length;
  if (pending.length) {
    questions.append(element("strong", "", "Что ещё стоит уточнить"));
    const list = element("ul");
    pending.forEach((text) => list.append(element("li", "", text)));
    questions.append(list);
  }
  renderActionEditor();
  state.planTasks = (card.tasks || []).map((task) => ({ ...cloneData(task), completed: false }));
  renderTaskEditor();
}

function editedField(text, original) {
  const old = cardField(original);
  return text === old.text ? cloneData(old) : { text, origin: "user", source_ids: [] };
}

function editedLines(value, originals, maximum, name) {
  const lines = value.split("\n").map((text) => text.trim()).filter(Boolean);
  if (lines.length > maximum) throw new ApiError(`${name}: оставьте не больше ${maximum} строк.`);
  if (lines.some((text) => text.length > 1000)) throw new ApiError(`${name}: одна строка должна быть не длиннее 1000 символов.`);
  const previous = originals || [];
  const used = new Set();
  const matches = lines.map((text) => {
    const index = previous.findIndex((item, position) => !used.has(position) && lineText(item.text) === text);
    if (index < 0) return null;
    used.add(index);
    return cloneData(previous[index]);
  });
  const remaining = previous.filter((item, index) => !used.has(index));
  const reservedIds = new Set(previous.map((item) => item.id).filter(Boolean));
  return lines.map((text, index) => {
    if (matches[index]) return matches[index];
    const old = remaining.shift();
    const field = { text, origin: "user", source_ids: [] };
    if (old?.id) field.id = old.id;
    else if (name === "Требования") {
      let number = 1;
      while (reservedIds.has(`req-${number}`)) number += 1;
      field.id = `req-${number}`;
      reservedIds.add(field.id);
    }
    return field;
  });
}

function readCardForm() {
  const original = state.cardDraft;
  if (!original) throw new ApiError("Сначала откройте карточку проекта.");
  const deadline = $("card-deadline").value || null;
  const requirements = editedLines($("card-requirements").value, original.requirements, 12, "Требования");
  const ids = new Set(requirements.map((item) => item.id));
  const broken = (original.tasks || []).find((task) => (task.requirement_ids || []).some((id) => !ids.has(id)));
  if (broken) throw new ApiError(`Требование используется действием «${broken.title}», поэтому его нельзя удалить.`);
  return {
    title: $("card-title").value.trim(),
    goal: editedField($("card-goal").value.trim(), original.goal),
    deliverable: editedField($("card-deliverable").value.trim(), original.deliverable),
    requirements,
    criteria: editedLines($("card-criteria").value, original.criteria, 10, "Критерии"),
    deadline: deadline === (original.deadline?.date || null) ? cloneData(original.deadline || { date: null, origin: "unknown", source_ids: [] }) : { date: deadline, origin: deadline ? "user" : "unknown", source_ids: [] },
    tasks: cloneData(state.cardDraft.tasks || []),
    questions: cloneData(original.questions || []),
  };
}

function applyAssignment(assignment) {
  state.detail = assignment;
  state.assignments = state.assignments.map((item) => item.assignment_id === assignment.assignment_id ? assignment : item);
  $("assignment-title").textContent = assignment.title;
  $("assignment-description").textContent = isUploaded(assignment)
    ? assignment.status === "ready"
      ? "Карточка подтверждена. Рассчитайте план или задайте вопрос по материалам."
      : "Материалы сохранены. Проверьте и подтвердите карточку проекта."
    : assignment.description || "Требования, материалы и план подготовки по этому проекту.";
  $("assignment-deadline").textContent = dateLabel(assignment);
  renderAssignments();
  renderSources(assignment.sources);
  renderRequirements(assignment);
  showAssignmentMode(assignment);
  renderCard(assignment);
  updateControls();
}

function cardError(error) {
  state.cardExpanded = true;
  $("card-status").textContent = "";
  $("card-error").textContent = userErrorMessage(error);
  $("card-error").hidden = false;
  if (error.status === 409) {
    $("card-reload").hidden = false;
    $("card-status").textContent = "Карточка изменилась. Обновите её перед сохранением; несохранённые правки будут заменены.";
  }
}

function openManualCard() {
  if (!isUploaded() || !state.detail || state.loading || state.analysis || state.confirmation) return;
  if (!state.cardDraft) {
    const field = () => ({ text: "", origin: "user", source_ids: [] });
    const card = { title: state.detail.title, goal: field(), deliverable: field(), requirements: [], criteria: [], deadline: { date: null, origin: "unknown", source_ids: [] }, tasks: [], questions: [] };
    renderCard({ ...state.detail, card });
    $("card-notice").textContent = "Заполните карточку по своим материалам. Неизвестную дату оставьте пустой.";
  }
  $("card-status").textContent = "Ручное заполнение. После подтверждения станут доступны план и чат.";
  updateControls();
  collapseUploadIfIdle();
  focusSection("card-heading");
  $("card-goal").focus({ preventScroll: true });
}

async function analyzeCard() {
  if (!isUploaded() || state.loading || state.analysis || state.confirmation || state.cardDirty || state.planDirty) return;
  if (state.status?.mode !== "live") { openManualCard(); return; }
  const controller = new AbortController();
  const selection = ++state.selection;
  resetConversation();
  invalidatePlan();
  state.analysis = controller;
  state.cardExpanded = true;
  $("card-error").hidden = true;
  $("card-status").textContent = state.status?.mode === "live" ? "Разбираем материалы и проверяем структуру карточки…" : "Готовим пустую карточку для ручного заполнения. Модель не вызывается.";
  updateControls();
  try {
    const data = await requestJSON(`/api/assignments/${encodeURIComponent(state.selectedId)}/analyze`, { method: "POST", body: "{}", signal: controller.signal }, 220000);
    if (selection !== state.selection) return;
    if (!data.assignment?.card) throw new ApiError("Сервис не вернул карточку проекта.", "invalid_response");
    applyAssignment(data.assignment);
    if (data.notice) $("card-notice").textContent = data.notice;
    $("card-status").textContent = data.mode === "live" ? "Черновик готов. Проверьте сведения и подтвердите карточку." : "Карточка открыта. Заполните цель и итоговый результат по своим материалам.";
    state.analysis = null;
    updateControls();
    $("card-goal").focus();
  } catch (error) {
    if (selection !== state.selection) return;
    if (error.code === "cancelled") $("card-status").textContent = "Разбор отменён. Сохранённые материалы доступны в разделе «О проекте».";
    else cardError(error);
  } finally {
    if (state.analysis === controller) { state.analysis = null; updateControls(); }
  }
}

async function confirmCard(event) {
  event.preventDefault();
  if (!isUploaded() || !state.cardDraft || state.loading || state.analysis || state.confirmation || state.planDirty) return;
  if (!$("card-form").checkValidity()) { navigateWorkflow("card"); $("card-form").reportValidity(); return; }
  for (const input of document.querySelectorAll("#card-actions-editor input")) if (!input.checkValidity()) { navigateWorkflow("planning"); $("decomposition").open = true; input.reportValidity(); return; }
  let card;
  try { card = readCardForm(); } catch (error) { cardError(error); return; }
  const controller = new AbortController();
  const selection = ++state.selection;
  resetConversation();
  invalidatePlan();
  state.confirmation = controller;
  $("card-error").hidden = true;
  $("card-status").textContent = "Проверяем и сохраняем карточку…";
  updateControls();
  try {
    const data = await requestJSON(`/api/assignments/${encodeURIComponent(state.selectedId)}/confirm`, { method: "POST", body: JSON.stringify({ card, revision: state.detail.card_revision ?? 0 }), signal: controller.signal }, 15000);
    if (selection !== state.selection) return;
    if (!data.assignment?.card || data.assignment.status !== "ready") throw new ApiError("Не удалось подтвердить состояние карточки.", "invalid_response");
    const previousPlan = savedPlan;
    applyAssignment(data.assignment);
    if (previousPlan) {
      // Preserve comparable saved progress immediately, even if the following GET fails.
      savedPlan = { ...previousPlan, stale: true };
      restoreSavedTasks(savedPlan);
      renderTaskEditor();
      await loadSavedPlan(selection);
      if (selection !== state.selection) return;
    }
    $("card-status").textContent = "Карточка подтверждена. Проверьте оценки задач в разделе «Действия и план» и сохраните расчёт.";
    state.confirmation = null;
    updateControls();
    focusSection("plan-title");
  } catch (error) {
    if (selection === state.selection) {
      if (error.code === "cancelled") $("card-status").textContent = "Сохранение отменено. Правки остаются в форме.";
      else cardError(error);
    }
  } finally {
    if (state.confirmation === controller) { state.confirmation = null; updateControls(); }
  }
}

function renderTaskEditor() {
  const target = $("plan-tasks");
  target.replaceChildren();
  $("plan-tasks-editor").hidden = !isUploaded() || !state.planTasks.length;
  state.planTasks.forEach((task, index) => {
    const row = element("div", `plan-task${task.completed ? " is-complete" : ""}`);
    const done = element("input");
    done.type = "checkbox";
    done.checked = task.completed;
    done.setAttribute("aria-label", `Задача ${index + 1} выполнена`);
    const title = element("p", "task-title-text", task.title);
    const minutes = element("input", "task-minutes-input");
    minutes.type = "number";
    minutes.min = String(Math.max(1, task.substeps?.length || 0));
    minutes.max = "4800";
    minutes.step = "1";
    minutes.required = true;
    minutes.value = task.minutes;
    minutes.setAttribute("aria-label", `Минут на задачу ${index + 1}`);
    const minuteLabel = element("label", "task-minutes-label");
    const minuteCaption = element("span", "", task.completed ? "План, мин" : "Остаток, мин");
    minutes.disabled = Boolean(task.completed);
    minuteLabel.append(minuteCaption, minutes);
    const info = element("div", "task-edit-info");
    const origin = element("span", "task-origin", task.estimate_source === "document" ? "Оценка из документа" : task.estimate_source === "user" ? "Ваша оценка" : "Предварительная оценка");
    info.append(title, origin);
    info.append(element("span", `priority-badge priority-${["high","medium","low"].includes(task.priority) ? task.priority : "medium"}`, {high:"Высокий приоритет",medium:"Средний приоритет",low:"Низкий приоритет"}[task.priority || "medium"]));
    const edit = () => {
      task.minutes = Number(minutes.value);
      task.estimate_source = "user";
      task.source_ids = [];
      origin.textContent = "Ваша оценка";
      invalidatePlan(true);
      updateControls();
    };
    minutes.addEventListener("input", edit);
    done.addEventListener("change", () => {
      task.completed = done.checked;
      minutes.disabled = task.completed;
      minuteCaption.textContent = task.completed ? "План, мин" : "Остаток, мин";
      row.classList.toggle("is-complete", task.completed);
      invalidatePlan(true);
      updateControls();
    });
    const actualLabel = element("label", "task-minutes-label");
    actualLabel.append(element("span", "", "Факт, мин"));
    const actual = element("input", "task-minutes-input");
    actual.type = "number";
    actual.min = "0";
    actual.max = "48000";
    actual.value = task.actual_minutes ?? "";
    actual.setAttribute("aria-label", `Фактические минуты: ${task.title}`);
    actual.addEventListener("input", () => {
      task.actual_minutes = actual.value === "" ? null : Number(actual.value);
      invalidatePlan(true);
    });
    actualLabel.append(actual);
    const details = element("details", "task-detail");
    details.append(element("summary", "", "Результат и оценка"));
    for (const [label, value] of [
      ["Результат", task.expected_result], ["Готово, когда", task.done_when],
      ["Основание оценки", task.estimate_basis], ["Почему этот приоритет", task.priority_reason],
      ["Диапазон исходной оценки", task.min_minutes != null && task.max_minutes != null ? `${task.min_minutes}–${task.max_minutes} мин` : null],
    ]) {
      if (value) details.append(element("p", "", `${label}: ${value}`));
    }
    if (task.substeps?.length) {
      const steps = element("ol", "task-substeps");
      task.substeps.forEach(step => steps.append(element("li", "", `${step.title} · ${formatMinutes(step.minutes)}. ${step.done_when}`)));
      details.append(steps);
    }
    details.append(element("p", "empty-note", "Остаток задаётся вручную. Фактическое время не вычитается автоматически."));
    row.append(done, info, minuteLabel, actualLabel, details);
    target.append(row);
  });
  renderOverview();
}

function showAssignmentMode(assignment) {
  $("workspace-grid").hidden = false;
  const uploaded = isUploaded(assignment);
  const pending = materialOnly(assignment);
  if (!state.detail || state.loading) state.workflow = "card";
  if (uploaded) collapseUploadIfIdle();
  $("materials-panel").hidden = !uploaded;
  $("card-panel").hidden = !uploaded;
  $("chat-panel").hidden = pending;
  $("planning").hidden = pending;
  $("context-column").hidden = pending || uploaded;
  $("sources-panel").hidden = uploaded;
  $("workspace-grid").classList.toggle("is-materials", pending);
  $("workspace-grid").classList.toggle("is-uploaded", uploaded);
  $("delete-confirmation").hidden = true;
  $("delete-error").hidden = true;
  $("material-query").value = "";
  $("materials-toggle").hidden = !uploaded || pending;
  $("materials-body").hidden = false;
  $("materials-toggle").setAttribute("aria-expanded", "true");
  $("materials-toggle").textContent = "Скрыть материалы";
  if (uploaded) {
    $("assignment-deadline").textContent = pending ? "Сначала подтвердите карточку" : dateLabel(assignment);
    $("materials-next").textContent = pending ? "Откройте оригиналы, проверьте фрагменты и заполните карточку проекта. После подтверждения станут доступны план и чат." : "Карточка подтверждена. Здесь остаются исходники и поиск для проверки требований.";
    renderDocuments(assignment);
  }
  $("plan-intro").textContent = uploaded ? "Задачи идут по порядку. Проверьте оценки минут и отметьте выполненное перед расчётом." : "Распределим этапы по доступному времени. Длительности — ориентировочные оценки.";
  const prompts = uploaded ? [["Какой результат требуется получить?", "Какой результат нужен?"], ["Посмотри мой прогресс и доступное время. Что сделать следующим и почему?", "На чём сфокусироваться?"], ["Какие перерывы и рабочий цикл можно попробовать с учётом материалов о продуктивности?", "Как распределить нагрузку?"]] : [["Что нужно отправить в LMS?", "Что отправить в LMS?"], ["За что дают баллы и как оценивается работа?", "Критерии оценки"], ["Когда сдавать личный проект?", "Когда сдавать?"]];
  document.querySelectorAll("[data-prompt]").forEach((button, index) => {
    button.dataset.prompt = prompts[index][0];
    button.replaceChildren(document.createTextNode(prompts[index][1] + " "), element("span", "", "↗"));
  });
  $("question").placeholder = uploaded ? "Например: отметь подготовку сценария выполненной" : "Например: что обязательно должно быть в README?";
  if (state.status) showMode(state.status, assignment);
  renderViewVisibility();
}

function selectedFilesChanged() {
  $("upload-error").hidden = true;
  const files = Array.from($("upload-files").files);
  const target = $("selected-files");
  target.replaceChildren();
  target.hidden = !files.length;
  files.forEach((file) => target.append(element("li", "", `${file.name} · ${(file.size / 1024 / 1024).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} МиБ`)));
  $("upload-status").textContent = files.length ? `Выбрано файлов: ${files.length}. Загрузите их в один проект.` : "Текст будет сохранён со ссылками на страницы.";
  updateControls();
}

async function refreshAssignments(preferredId = state.selectedId) {
  const data = await requestJSON("/api/assignments", {}, 15000);
  if (!Array.isArray(data.assignments)) throw new ApiError("Не удалось прочитать список проектов.", "invalid_response");
  state.assignments = data.assignments;
  renderAssignments();
  const next = state.assignments.find((item) => item.assignment_id === preferredId) || state.assignments[0];
  if (next) await selectAssignment(next.assignment_id);
}

async function submitUpload(event) {
  event.preventDefault();
  if (state.upload) return;
  if (state.cardDirty || state.planDirty) {
    $("upload-error").textContent = "Сначала сохраните или отмените изменения открытой карточки и плана.";
    $("upload-error").hidden = false;
    return;
  }
  $("upload-error").hidden = true;
  const files = Array.from($("upload-files").files);
  let problem = "";
  if (files.length < 1 || files.length > 3) problem = "Выберите от одного до трёх PDF для одного проекта.";
  else if (files.some((file) => !/\.pdf$/i.test(file.name))) problem = "Пока принимаются только PDF. Выберите документ с выделяемым текстом.";
  else if (files.some((file) => !file.size)) problem = "Один из файлов пустой. Выберите другой PDF.";
  else if (files.reduce((sum, file) => sum + file.size, 0) > 10 * 1024 * 1024) problem = "Суммарный размер PDF превышает 10 МиБ. Уменьшите размер или число файлов.";
  if (problem) {
    $("upload-error").textContent = problem;
    $("upload-error").hidden = false;
    return;
  }
  const body = new FormData();
  body.append("title", $("upload-name").value.trim());
  files.forEach((file) => body.append("files", file));
  const controller = new AbortController();
  state.upload = controller;
  $("upload-status").textContent = "Передаём PDF и извлекаем текст по страницам. Можно отменить загрузку.";
  updateControls();
  try {
    const data = await requestJSON("/api/assignments", { method: "POST", body, signal: controller.signal }, 65000);
    if (!data.assignment?.assignment_id) throw new ApiError("Материалы отправлены, но сервис не вернул проект. Обновите страницу перед повторной загрузкой.", "invalid_response");
    $("upload-name").value = "";
    $("upload-files").value = "";
    $("selected-files").replaceChildren();
    $("selected-files").hidden = true;
    state.assignments = [data.assignment, ...state.assignments.filter((item) => item.assignment_id !== data.assignment.assignment_id)];
    renderAssignments();
    const opened = await selectAssignment(data.assignment.assignment_id);
    $("upload-status").textContent = opened
      ? "Готово: материалы сохранены. Следующий шаг — проверить и подтвердить карточку проекта."
      : "Материалы сохранены. Выберите новый проект в списке после завершения текущих правок или повторите его открытие.";
    if (opened) { setUploadExpanded(false); focusSection("card-heading"); }
  } catch (error) {
    if (error.code === "cancelled") {
      $("upload-status").textContent = "Загрузка отменена. Если проект успел сохраниться, оно появится в списке.";
      try { await refreshAssignments(); } catch (_) { /* Preserve the cancellation message. */ }
    } else {
      $("upload-status").textContent = "Материалы не загружены. Исправьте причину и повторите попытку.";
      $("upload-error").textContent = error.message;
      $("upload-error").hidden = false;
    }
  } finally {
    if (state.upload === controller) state.upload = null;
    updateControls();
  }
}

async function searchMaterials(event) {
  event.preventDefault();
  if (!state.detail || !isUploaded() || state.search) return;
  const query = $("material-query").value.trim();
  if (!query) return renderMaterialResults(state.detail.sources);
  const controller = new AbortController();
  const selection = state.selection;
  state.search = controller;
  $("material-search-status").textContent = "Ищем фрагменты в материалах этого проекта…";
  updateControls();
  try {
    const data = await requestJSON(`/api/assignments/${encodeURIComponent(state.selectedId)}/materials?q=${encodeURIComponent(query)}`, { signal: controller.signal }, 15000);
    if (selection === state.selection) renderMaterialResults(data.matches, query);
  } catch (error) {
    if (selection === state.selection && error.code !== "cancelled") $("material-search-status").textContent = error.message;
  } finally {
    if (state.search === controller) { state.search = null; updateControls(); }
  }
}

async function deleteAssignment() {
  if (!state.selectedId || !isUploaded() || state.deleting) return;
  const id = state.selectedId;
  state.deleting = true;
  $("delete-error").hidden = true;
  updateControls();
  try {
    await requestJSON(`/api/assignments/${encodeURIComponent(id)}`, { method: "DELETE" }, 15000);
    state.assignments = state.assignments.filter((assignment) => assignment.assignment_id !== id);
    renderAssignments();
    if (state.selectedId === id) {
      const next = state.assignments[0];
      if (next) await selectAssignment(next.assignment_id, true);
    }
    $("upload-status").textContent = "Задание и его материалы удалены.";
  } catch (error) {
    if (state.selectedId === id) {
      $("delete-error").textContent = error.message;
      $("delete-error").hidden = false;
    }
  } finally { state.deleting = false; updateControls(); }
}

async function selectAssignment(id, force = false) {
  if (state.selectedId === id && state.detail && !force) {
    if (state.workflow === "upload") navigateWorkflow("card");
    return true;
  }
  if (state.cardDirty && state.selectedId && !force) {
    $("card-status").textContent = "Сначала подтвердите изменения карточки или нажмите «Отменить правки», затем выберите другой проект.";
    navigateWorkflow(state.actionEditing ? "planning" : "card");
    (state.actionEditing ? $("save-actions") : $("card-confirm")).focus();
    return false;
  }
  if (state.planDirty && state.selectedId && !force) {
    $("plan-save-status").textContent = "Сначала сохраните план или отмените изменения плана, затем выберите другой проект.";
    $("plan-discard").hidden = false;
    navigateWorkflow("planning");
    $("plan-button").focus({ preventScroll: true });
    return false;
  }
  state.planDirty = false;
  state.chat?.abort();
  state.plan?.abort();
  state.search?.abort();
  state.analysis?.abort();
  state.confirmation?.abort();
  state.chat = null;
  state.plan = null;
  state.search = null;
  state.analysis = null;
  state.confirmation = null;
  state.cardDraft = null;
  state.cardDirty = false;
  state.planTasks = [];
  legacyPlan = null;
  state.planVersion += 1;
  const selection = ++state.selection;
  state.selectedId = id;
  state.history = [];
  state.chatId = null; state.chatSessions = []; state.chatLoad += 1; state.chatLoading = true;
  renderChatSessions();
  conversationAtBottom = true;
  state.detail = null;
  state.loading = true;
  $("conversation").querySelectorAll(".message").forEach((node) => node.remove());
  $("chat-welcome").hidden = false;
  $("plan-result").hidden = true;
  $("plan-error").hidden = true;
  $("plan-stale").hidden = true;
  $("card-form").hidden = true;
  $("card-error").hidden = true;
  $("card-reload").hidden = true;
  $("card-questions").hidden = true;
  $("card-status").textContent = "";
  $("question").value = "";
  document.querySelectorAll("[data-assignment-id]").forEach((button) => {
    const selected = button.dataset.assignmentId === id;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  state.workflow = "card";
  const brief = state.assignments.find((assignment) => assignment.assignment_id === id);
  if (brief) showAssignmentMode(brief);
  $("assignment-title").textContent = brief?.title || "Задание";
  $("assignment-description").textContent = "Загружаем описание и материалы проекта…";
  $("assignment-deadline").textContent = brief ? dateLabel(brief) : "";
  $("sources").replaceChildren(element("p", "empty-note", "Загружаем материалы проекта…"));
  $("source-count").textContent = "—";
  $("assignment-details").hidden = true;
  $("delete-confirmation").hidden = true;
  if (isUploaded(brief)) {
    $("material-results").replaceChildren(element("p", "empty-note", "Загружаем материалы проекта…"));
    $("material-search-status").textContent = "Загружаем сохранённый текст…";
  }
  updateControls();
  try {
    const data = await requestJSON(`/api/assignments/${encodeURIComponent(id)}`, {}, 15000);
    if (selection !== state.selection) return false;
    if (!data.assignment || typeof data.assignment.title !== "string") throw new ApiError("Не удалось прочитать проект.", "invalid_response");
    state.loading = false;
    state.workflow = "card";
    applyAssignment(data.assignment);
    state.cardExpanded = true; updateCardVisibility();
    window.ProjectWorkspace?.resetProject();
    savedPlan = null;
    if (isUploaded()) await loadSavedPlan(selection);
    if (selection === state.selection && !materialOnly()) await loadChatSessions(selection);
    return true;
  } catch (error) {
    if (selection !== state.selection) return;
    const target = isUploaded(brief) ? $("material-results") : $("sources");
    target.replaceChildren(element("p", "inline-error", error.message));
    const retry = element("button", "retry-button", "Загрузить проект ещё раз");
    retry.type = "button";
    retry.addEventListener("click", () => { selectAssignment(id); });
    target.append(retry);
    if (isUploaded(brief)) {
      $("materials-body").hidden = false;
      $("materials-toggle").setAttribute("aria-expanded", "true");
      $("materials-toggle").textContent = "Скрыть материалы";
      $("material-search-status").textContent = "Не удалось получить материалы проекта.";
    }
    return false;
  } finally {
    if (selection === state.selection) { state.loading = false; state.chatLoading = false; updateControls(); }
  }
}

function chatPath(id = state.selectedId) {
  return `/api/assignments/${encodeURIComponent(id)}/chats`;
}

function clearConversation() {
  $("conversation").querySelectorAll(".message").forEach(node => node.remove());
  $("chat-welcome").hidden = false;
  state.history = [];
  conversationAtBottom = true;
}

function renderChatSessions() {
  $("chat-delete-confirmation").hidden = true;
  const list = $("chat-sessions-list");
  list.replaceChildren();
  state.chatSessions.forEach(session => {
    const row = element("li"), button = element("button", "chat-session", session.title || "Новый чат");
    button.type = "button";
    button.setAttribute("aria-pressed", String(session.id === state.chatId));
    button.disabled = Boolean(state.chat) || state.chatLoading;
    button.append(element("small", "", `${session.message_count || 0} сообщений`));
    button.addEventListener("click", () => openChatSession(session.id));
    row.append(button); list.append(row);
  });
}

async function openChatSession(id) {
  if (state.chat || state.chatLoading) return;
  const selection = state.selection, load = ++state.chatLoad;
  state.chatLoading = true; updateControls(); renderChatSessions();
  $("chat-sessions-status").textContent = "Загружаем чат…";
  try {
    const data = await requestJSON(`${chatPath()}/${encodeURIComponent(id)}`, {}, 15000);
    if (selection !== state.selection || load !== state.chatLoad) return;
    state.chatId = id; clearConversation();
    for (const message of data.messages || []) {
      if (!["user", "assistant"].includes(message.role)) continue;
      const node = messageNode(message.role, message.content || "");
      if (message.role === "assistant" && message.result) {
        // Stored previews have no valid application token after reload.
        const {action_proposal, ...result} = message.result;
        fillAnswer(node, result);
      }
      if (message.error) { node.classList.add("is-error"); node.querySelector(".message-content").textContent = message.error.message || "Ответ не получен."; }
    }
    $("chat-sessions-status").textContent = "Чат сохранён в этом проекте.";
  } catch (error) {
    if (selection === state.selection && load === state.chatLoad) $("chat-sessions-status").textContent = error.message;
  } finally {
    if (selection === state.selection && load === state.chatLoad) { state.chatLoading = false; renderChatSessions(); updateControls(); }
  }
}

async function loadChatSessions(selection = state.selection, openLatest = true) {
  const id = state.selectedId;
  state.chatLoading = true; updateControls(); renderChatSessions();
  try {
    const data = await requestJSON(chatPath(id), {}, 15000);
    if (selection !== state.selection) return;
    state.chatSessions = Array.isArray(data.sessions) ? data.sessions : [];
    renderChatSessions();
    $("chat-sessions-status").textContent = state.chatSessions.length ? "Выберите сохранённый чат или начните новый." : "Здесь появятся чаты этого проекта.";
    state.chatLoading = false;
    if (openLatest && state.chatSessions.length) await openChatSession(state.chatSessions[0].id);
  } catch (error) {
    if (selection === state.selection) $("chat-sessions-status").textContent = `История недоступна: ${error.message}`;
  } finally {
    if (selection === state.selection) { state.chatLoading = false; renderChatSessions(); updateControls(); }
  }
}

function newChat() {
  if (state.chat || state.chatLoading) return;
  state.chatId = null; state.chatLoad += 1; clearConversation(); renderChatSessions();
  $("chat-sessions-status").textContent = "Новый чат сохранится после первого вопроса.";
  updateControls();
  $("question").focus();
}

async function deleteChatSession() {
  if (!state.chatId || state.chat || state.chatLoading) return;
  const selection = state.selection, id = state.chatId, path = `${chatPath()}/${encodeURIComponent(id)}`;
  state.chatLoading = true; renderChatSessions(); updateControls();
  try {
    await requestJSON(path, {method: "DELETE"}, 15000);
    if (selection !== state.selection) return;
    state.chatId = null; clearConversation();
    state.chatLoading = false;
    await loadChatSessions(selection);
  } catch (error) {
    if (selection === state.selection) $("chat-sessions-status").textContent = error.message;
  } finally {
    if (selection === state.selection) { state.chatLoading = false; renderChatSessions(); updateControls(); }
  }
}

function messageNode(role, text = "", pending = false) {
  $("chat-welcome").hidden = true;
  const node = element("article", `message message-${role}${pending ? " is-pending" : ""}`);
  const header = element("div", "message-header");
  header.append(element("span", "message-author", role === "user" ? "Вы" : "Project Navigator"));
  const content = element("div", "message-content structured-text");
  if (pending) content.textContent = text;
  else renderStructuredText(content, text);
  node.append(header, content);
  $("conversation").append(node);
  scrollConversation();
  return node;
}

function scrollConversation() {
  if (conversationAtBottom) $("conversation").scrollTop = $("conversation").scrollHeight;
}

function fillAnswer(node, data) {
  node.classList.remove("is-pending");
  const header = node.querySelector(".message-header");
  const live = data.mode === "live";
  if (live) header.append(element("span", "message-mode is-live", "Ассистент"));
  const statuses = { needs_clarification: "Нужно уточнение", insufficient_data: "Недостаточно данных", out_of_scope: "Вне материалов", conflict: "Есть противоречие" };
  if (statuses[data.status]) header.append(element("span", "message-status", statuses[data.status]));
  renderStructuredText(node.querySelector(".message-content"), cleanTechnicalText(data.answer));
  const sources = Array.isArray(data.sources) ? data.sources : [];
  if (sources.length) {
    const links = element("div", "response-sources");
    sources.forEach((source) => {
      const url = sourceURL(source.url);
      if (!url) return;
      const link = element("a", "source-chip", `${source.title || "Источник"} · ${sourceLocation(source)} ↗`);
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      links.append(link);
    });
    node.append(links);
  }
  window.ProjectWorkspace?.renderProposal(node, data);
  renderSources(sources, true);
  scrollConversation();
}

async function sendMessage(value) {
  const message = (typeof value === "string" ? value : $("question").value).trim();
  if (!message || !state.detail || state.loading || state.chat || state.chatLoading || materialOnly() || state.cardDirty || state.analysis || state.confirmation) return;
  if (message.length > 2000) { $("question").focus(); return; }
  const assignmentId = state.selectedId;
  const selection = state.selection;
  const controller = new AbortController();
  state.chat = controller;
  renderChatSessions();
  messageNode("user", message);
  const pending = messageNode("assistant", "Обрабатываем запрос. Его можно отменить.", true);
  $("question").value = "";
  updateControls();
  try {
    if (!state.chatId) {
      const created = await requestJSON(chatPath(assignmentId), {method: "POST", body: "{}", signal: controller.signal}, 15000);
      if (selection !== state.selection) return;
      if (!created.session?.id) throw new ApiError("Не удалось создать чат.", "invalid_response");
      state.chatId = created.session.id;
    }
    const history = [];
    const payload = { message, assignment_id: assignmentId, chat_id: state.chatId };
    while (history.length && new TextEncoder().encode(JSON.stringify(payload)).length > 15000) history.splice(0, 2);
    const data = await requestJSON("/api/chat", { method: "POST", body: JSON.stringify(payload), signal: controller.signal }, 220000);
    if (selection !== state.selection) return;
    if (typeof data.answer !== "string" || !data.answer.trim() || !["offline", "live"].includes(data.mode)) throw new ApiError("Получен неполный ответ ассистента.", "invalid_response", data.request_id);
    fillAnswer(pending, data);
    state.history.push({ role: "user", content: Array.from(message).slice(0, 500).join("") },
      { role: "assistant", content: Array.from(data.answer).slice(0, 500).join("") });
    state.history = state.history.slice(-6);
  } catch (error) {
    if (selection !== state.selection) return;
    pending.classList.remove("is-pending");
    pending.classList.add("is-error");
    pending.querySelector(".message-content").textContent = userErrorMessage(error);
    const retry = element("button", "retry-button", error.code === "cancelled" ? "Задать вопрос снова" : "Повторить запрос");
    retry.type = "button";
    retry.addEventListener("click", () => { sendMessage(message); });
    pending.append(retry);
    scrollConversation();
  } finally {
    if (state.chat === controller) { state.chat = null; updateControls(); await loadChatSessions(selection, false); }
  }
}

function renderPlan(data) {
  const totals = [data.available_minutes, data.required_minutes, data.deficit_minutes];
  if (!["feasible", "insufficient_time"].includes(data.status) || !Array.isArray(data.schedule) || !totals.every((value) => Number.isFinite(value) && value >= 0)) throw new ApiError("Получен неполный план подготовки.", "invalid_response");
  if (!data.schedule.every((day) => day && Number.isInteger(day.day) && day.day > 0 && Array.isArray(day.tasks) && day.tasks.every((task) => task && typeof task.title === "string" && Number.isFinite(task.minutes) && task.minutes > 0))) throw new ApiError("Не удалось проверить этапы плана.", "invalid_response");
  const target = $("plan-result");
  target.replaceChildren();
  const insufficient = data.status === "insufficient_time";
  const summary = element("div", `plan-summary${insufficient ? " is-insufficient" : ""}`);
  const heading = element("strong", "", insufficient ? `Не хватает ${formatMinutes(data.deficit_minutes)}` : data.required_minutes === 0 ? "Все задачи отмечены выполненными" : "План укладывается в доступное время");
  heading.setAttribute("role", "heading");
  heading.setAttribute("aria-level", "3");
  heading.setAttribute("tabindex", "-1");
  summary.append(heading);
  summary.append(element("span", "", `Нужно ${formatMinutes(data.required_minutes)} · доступно ${formatMinutes(data.available_minutes)}`));
  target.append(summary);
  const next = nextProjectTask(state.planTasks);
  if (next) {
    const box = element("div", "next-action");
    box.append(
      element("small", "eyebrow", "Следующее действие"),
      element("h3", "", next.title),
      element("p", "", next.expected_result || "Уточните результат действия в карточке"),
      element("small", "", next.done_when || "Уточните признак готовности"),
    );
    target.append(box);
  }
  if (Array.isArray(data.warnings) && data.warnings.length) {
    const notices = element("details", "plan-warnings");
    notices.append(element("summary", "", `Проверить перед началом · ${data.warnings.length}`));
    const grouped = new Map();
    data.warnings.forEach((warning) => {
      const message = typeof warning === "string" ? warning : warning.message || warning.code;
      const task = state.planTasks.find((item) => item.id === warning.task_id);
      const requirement = state.cardDraft?.requirements?.find((item) => item.id === warning.requirement_id);
      const name = task?.title || requirement?.text;
      if (!grouped.has(message)) grouped.set(message, new Set());
      if (name) grouped.get(message).add(name);
    });
    const list = element("ul");
    grouped.forEach((names, message) => {
      list.append(element("li", "", names.size ? `${message} ${Array.from(names).join("; ")}` : message));
    });
    notices.append(list);
    target.append(notices);
  }
  if (Array.isArray(data.remaining_tasks) && data.remaining_tasks.length) {
    const remainder = element("section", "plan-remainder");
    remainder.append(element("h3", "", "Не помещается в план"));
    const list = element("ul");
    data.remaining_tasks.forEach((task) => list.append(element("li", "", `${task.title} · ещё ${formatMinutes(task.minutes)}`)));
    remainder.append(list);
    target.append(remainder);
  }
  data.schedule.forEach((day) => {
    const tasks = Array.isArray(day.tasks) ? day.tasks : [];
    const section = element("section", "plan-day");
    const date = day.date ? new Date(`${day.date}T12:00:00`) : null;
    const dateText = date && !Number.isNaN(date.getTime()) ? ` · ${date.toLocaleDateString("ru-RU", { day: "numeric", month: "short" })}` : "";
    const heading = element("h3", "", `День ${day.day}${dateText}`);
    heading.append(element("span", "", formatMinutes(tasks.reduce((sum, task) => sum + (Number.isFinite(task.minutes) ? task.minutes : 0), 0))));
    const list = element("ol", "day-steps");
    tasks.forEach((task) => {
      const steps = task.substeps?.length ? task.substeps : [{ title: task.title, minutes: task.minutes, done_when: task.done_when || task.expected_result }];
      steps.forEach((step) => {
        const item = element("li", "day-step");
        const header = element("div", "day-step-heading");
        header.append(element("strong", "", step.title), element("span", "day-step-time", formatMinutes(step.minutes)));
        item.append(header);
        if (task.substeps?.length) item.append(element("p", "day-step-parent", task.title));
        if (step.continued || step.continues) item.append(element("small", "day-step-continuation", step.continued && step.continues ? "Продолжение · завершение в другой день" : step.continued ? "Продолжение предыдущего дня" : "Продолжится в следующий рабочий день"));
        if (step.done_when) item.append(element("p", "day-step-result", `${step.continues ? "Результат после всех частей" : "Готово, когда"}: ${step.done_when}`));
        list.append(item);
      });
    });
    section.append(heading);
    if (tasks.length) section.append(list);
    else section.append(element("p", "empty-note", "Без работы · 0 мин"));
    target.append(section);
  });
  target.hidden = false;
  $("plan-stale").hidden = true;
  renderOverview();
  return heading;
}

async function submitPlan(event) {
  event.preventDefault();
  const keyboardActivation = planKeyboardActivation;
  planKeyboardActivation = false;
  if (!state.detail || state.loading || state.plan || materialOnly() || state.cardDirty || state.analysis || state.confirmation) return;
  if (!$("plan-form").reportValidity()) return;
  const days = Number($("plan-days").value);
  const minutes = Number($("plan-minutes").value);
  if (!Number.isInteger(days) || !Number.isInteger(minutes) || days < 1 || days > 30 || minutes < 0 || minutes > 480) return;
  const controller = new AbortController();
  const selection = state.selection;
  const version = state.planVersion;
  state.plan = controller;
  $("plan-error").hidden = true;
  $("plan-result").hidden = true;
  updateControls();
  let focusTarget = null;
  let focusMoved = false;
  const trackFocus = (event) => { if (event.target !== document.body) focusMoved = true; };
  if (keyboardActivation) document.addEventListener("focusin", trackFocus);
  try {
    const payload = { assignment_id: state.selectedId, days, minutes_per_day: minutes };
    if (isUploaded()) {
      payload.card_revision = state.detail.card_revision;
      payload.tasks = cloneData(state.planTasks);
    }
    let data;
    if (isUploaded()) {
      const body = { revision: savedPlan?.revision || 0, card_revision: state.detail.card_revision, availability: readAvailability(days, minutes), tasks: state.planTasks.map(({id,title,minutes,completed,actual_minutes}) => ({id,title,minutes,completed: Boolean(completed),actual_minutes: actual_minutes ?? null})), start_date: $("plan-start-date")?.value || null, reserve_percent: Number($("plan-reserve")?.value || 0) };
      const response = await requestJSON(`/api/assignments/${encodeURIComponent(state.selectedId)}/plan`, {method:"PUT",body:JSON.stringify(body),signal:controller.signal},15000);
      if (selection !== state.selection || version !== state.planVersion) return;
      savedPlan = response.plan;
      restoreSavedTasks(savedPlan);
      renderTaskEditor();
      data = savedPlan.result;
      state.planDirty = false;
      if ($("plan-save-status")) $("plan-save-status").textContent = "План и прогресс сохранены на сервере для текущего браузера · версия " + savedPlan.revision;
    } else {
      data = await requestJSON("/api/plan", { method: "POST", body: JSON.stringify(payload), signal: controller.signal }, 15000);
      if (selection === state.selection && version === state.planVersion) {
        legacyPlan = { result: data, input: { availability: Array.from({ length: days }, (_, index) => ({day: index + 1, minutes})), reserve_percent: 0 } };
        state.planTasks = cloneData(data.tasks || []);
      }
    }
    if (selection === state.selection && version === state.planVersion) {
      state.planDirty = false;
      window.ProjectWorkspace?.invalidateAnalytics();
      focusTarget = renderPlan(data);
    }
  } catch (error) {
    if (selection !== state.selection || error.code === "cancelled") return;
    $("plan-error").textContent = error.message;
    $("plan-error").hidden = false;
    if (error.status === 409 && isUploaded()) { $("card-reload").hidden = false; $("plan-error").textContent += " Обновите карточку перед новым расчётом."; }
    focusTarget = $("plan-button");
  } finally {
    if (keyboardActivation) document.removeEventListener("focusin", trackFocus);
    if (state.plan === controller) {
      state.plan = null;
      updateControls();
      if (keyboardActivation && !focusMoved && !controller.signal.aborted && selection === state.selection
          && version === state.planVersion && focusTarget && document.hasFocus()) {
        focusTarget.focus({ preventScroll: true });
        focusTarget.scrollIntoView({ behavior: scrollBehavior(), block: "nearest" });
      }
    }
  }
}

async function initialize() {
  state.initializing = true;
  updateControls();
  $("initial-error").hidden = true;
  $("reload-button").disabled = true;
  try {
    const [status, data] = await Promise.all([requestJSON("/api/status", {}, 15000), requestJSON("/api/assignments", {}, 15000)]);
    if (!["offline", "live"].includes(status.mode) || !Array.isArray(data.assignments)) throw new ApiError("Сервис вернул неполную конфигурацию.", "invalid_response");
    showMode(status);
    state.assignments = data.assignments;
    renderAssignments();
    const personal = state.assignments.find((assignment) => isUploaded(assignment));
    if (personal) await selectAssignment(personal.assignment_id);
    else {
      $("assignment-title").textContent = "Новый проект";
      $("assignment-description").textContent = "Опишите рабочий, учебный или личный проект. Превратите требования в действия и проверьте, хватит ли времени.";
      $("assignment-deadline").textContent = "Начните с текста или PDF";
      $("workspace-grid").hidden = true;
      renderSources([]);
    }
  } catch (error) {
    $("initial-error-text").textContent = error.message;
    $("initial-error").hidden = false;
    $("mode-badge").className = "mode-badge is-error";
    $("mode-badge").hidden = true;
    $("mode-label").textContent = "Нет связи с сервисом";
    $("mode-notice").className = "mode-notice is-error";
    $("mode-notice").textContent = "Не удалось загрузить состояние сервиса. Сведения о подключении модели пока недоступны.";
  } finally { state.initializing = false; $("reload-button").disabled = false; updateControls(); }
}

$("delete-chat").addEventListener("click", () => { if (!$("delete-chat").disabled) { $("chat-delete-confirmation").hidden = false; $("chat-delete-confirm").focus(); } });
$("chat-delete-cancel").addEventListener("click", () => { $("chat-delete-confirmation").hidden = true; $("delete-chat").focus(); });
$("chat-delete-confirm").addEventListener("click", deleteChatSession);
$("new-chat").addEventListener("click", newChat);
$("chat-form").addEventListener("submit", (event) => { event.preventDefault(); sendMessage(); });
$("question").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); sendMessage(); } });
$("cancel-button").addEventListener("click", () => state.chat?.abort());
$("reload-button").addEventListener("click", initialize);
$("plan-button").addEventListener("click", (event) => { planKeyboardActivation = event.detail === 0; });
$("plan-form").addEventListener("submit", submitPlan);
$("plan-form").addEventListener("input", (event) => { if (!event.target.closest("#plan-tasks")) { invalidatePlan(true); updateControls(); } });
$("upload-form").addEventListener("submit", submitUpload);
$("upload-files").addEventListener("change", selectedFilesChanged);
$("upload-reset").addEventListener("click", () => { $("upload-files").value = ""; selectedFilesChanged(); });
$("upload-cancel").addEventListener("click", () => state.upload?.abort());
$("upload-toggle").addEventListener("click", () => { setUploadExpanded($("upload-body").hidden); });
$("material-search-form").addEventListener("submit", searchMaterials);
$("materials-toggle").addEventListener("click", () => {
  const expanded = $("materials-body").hidden;
  $("materials-body").hidden = !expanded;
  $("materials-toggle").setAttribute("aria-expanded", String(expanded));
  $("materials-toggle").textContent = expanded ? "Скрыть материалы" : "Показать материалы";
});
$("analyze-button").addEventListener("click", analyzeCard);
$("manual-card-button").addEventListener("click", openManualCard);
$("analyze-cancel").addEventListener("click", () => state.analysis?.abort());
$("card-form").addEventListener("submit", confirmCard);
$("card-form").addEventListener("input", () => {
  if (!state.cardDraft) return;
  state.cardDirty = true;
  invalidatePlan();
  updateControls();
});
$("card-toggle").addEventListener("click", () => {
  if (state.cardDirty || state.analysis || state.confirmation || state.detail?.status !== "ready") return;
  state.cardExpanded = !state.cardExpanded;
  updateCardVisibility();
});
$("card-reload").addEventListener("click", () => { if (state.selectedId) selectAssignment(state.selectedId, true); });
$("card-discard").addEventListener("click", () => {
  if (!state.detail) return;
  renderCard(state.detail, true);
  $("card-status").textContent = "Правки отменены. Показана последняя сохранённая карточка.";
  updateControls();
  $("card-goal").focus();
});
$("delete-assignment").addEventListener("click", () => { $("delete-confirmation").hidden = false; $("delete-confirm").focus(); });
$("delete-cancel").addEventListener("click", () => { $("delete-confirmation").hidden = true; $("delete-assignment").focus(); });
$("delete-confirm").addEventListener("click", deleteAssignment);
$("conversation").addEventListener("scroll", () => {
  const log = $("conversation");
  conversationAtBottom = log.scrollHeight - log.scrollTop - log.clientHeight <= 80;
});
document.querySelectorAll(".add-assignment-link").forEach((link) => link.addEventListener("click", (event) => { event.preventDefault(); navigateWorkflow("upload"); }));

for (const formId of ["card-form", "plan-form"]) $(formId).addEventListener("invalid", (event) => {
  const details = event.target.closest?.("details");
  if (details) details.open = true;
}, true);

document.querySelectorAll("#workflow-nav [data-workflow]").forEach((button) => {
  button.addEventListener("click", () => navigateWorkflow(button.dataset.workflow));
  button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const available = Array.from(document.querySelectorAll("#workflow-nav [data-workflow]")).filter((item) => !item.disabled);
    if (!available.length) return;
    event.preventDefault();
    const index = available.indexOf(button);
    const next = event.key === "Home" ? 0 : event.key === "End" ? available.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + available.length) % available.length;
    available.forEach((item, position) => { item.tabIndex = position === next ? 0 : -1; });
    available[next].focus();
  });
});
document.querySelectorAll("[data-prompt]").forEach((button) => button.addEventListener("click", () => { sendMessage(button.dataset.prompt); }));

function markActionChanged() {
  state.cardDirty = true;
  state.actionEditing = true;
  invalidatePlan(true);
  updateControls();
}

async function discardPlanChanges() {
  state.planDirty = false;
  state.plan?.abort();
  state.plan = null;
  state.planTasks = (state.detail?.card?.tasks || []).map((task) => ({ ...cloneData(task), completed: false, actual_minutes: null }));
  availability = [];
  $("plan-days").value = 7;
  $("plan-minutes").value = 60;
  if ($("plan-reserve")) $("plan-reserve").value = 20;
  if ($("plan-start-date")) $("plan-start-date").value = "";
  $("plan-result").hidden = true;
  $("plan-stale").hidden = true;
  $("plan-save-status").textContent = "Несохранённые изменения плана отменены.";
  renderTaskEditor();
  renderAvailability();
  updateControls();
  if (isUploaded()) await loadSavedPlan(state.selection);
}

$("plan-discard")?.addEventListener("click", discardPlanChanges);
window.addEventListener?.("beforeunload", (event) => {
  if (!state.cardDirty && !state.planDirty) return;
  event.preventDefault();
  event.returnValue = "";
});

function renderTaskLinks(box, task, index) {
  for (const [key, title, choices] of [
    ["depends_on", "Начать после", (state.cardDraft.tasks || []).filter((other) => other.id !== task.id)],
  ]) {
    const group = element("fieldset", "action-links");
    group.append(element("legend", "", title));
    if (!choices.length) group.append(element("p", "empty-note", "Других действий пока нет."));
    choices.forEach((choice) => {
      const label = element("label", "action-link-option");
      const input = element("input");
      input.type = "checkbox";
      input.checked = (task[key] || []).includes(choice.id);
      input.setAttribute("aria-label", `${title}: ${choice.text || choice.title}, действие ${index + 1}`);
      input.addEventListener("change", () => {
        const selected = new Set(task[key] || []);
        if (input.checked) selected.add(choice.id);
        else selected.delete(choice.id);
        task[key] = Array.from(selected);
        markActionChanged();
      });
      label.append(input, element("span", "", choice.text || choice.title || "Действие без названия"));
      group.append(label);
    });
    box.append(group);
  }
}

function normalizeSubsteps(task) {
  const steps = task.substeps || [];
  if (!steps.length || !Number.isInteger(task.minutes) || task.minutes < steps.length) return;
  const total = steps.reduce((sum, step) => sum + step.minutes, 0);
  if (total === task.minutes || total <= 0 || steps.some(step => !Number.isInteger(step.minutes) || step.minutes < 1)) return;
  const budget = task.minutes - steps.length;
  const portions = steps.map((step, index) => ({index, minutes: 1 + Math.floor(budget * step.minutes / total), remainder: (budget * step.minutes) % total}));
  let remainder = task.minutes - portions.reduce((sum, item) => sum + item.minutes, 0);
  [...portions].sort((a, b) => b.remainder - a.remainder || a.index - b.index).forEach(item => { if (remainder > 0) { item.minutes++; remainder--; } });
  steps.forEach((step, index) => { step.minutes = portions[index].minutes; });
}

function renderSubstepEditor(target, task, taskIndex, parentInputs) {
  target.replaceChildren();
  const steps = task.substeps || [];
  target.append(element("h4", "", `Шаги действия · ${steps.length}`));
  if (!steps.length) target.append(element("p", "empty-note", "Добавьте конкретные шаги и результат каждого — они появятся в расписании."));
  const updateTotal = () => {
    if (task.substeps.length) task.minutes = task.substeps.reduce((sum, step) => sum + step.minutes, 0);
    task.min_minutes = null; task.max_minutes = null; task.estimate_source = "user"; task.source_ids = [];
    for (const key of ["minutes", "min_minutes", "max_minutes"]) parentInputs[key].value = task[key] ?? "";
    parentInputs.minutes.min = String(Math.max(1, task.substeps.length));
    markActionChanged();
  };
  steps.forEach((step, index) => {
    const row = element("fieldset", "substep-editor-row");
    row.append(element("legend", "", `Шаг ${index + 1}`));
    for (const [key, label] of [["title", "Что сделать"], ["minutes", "Минуты"], ["done_when", "Готово, когда…"]]) {
      const wrap = element("label", key === "minutes" ? "substep-minutes" : "", label);
      const input = element("input"); input.type = key === "minutes" ? "number" : "text"; input.value = step[key] ?? ""; input.required = true;
      if (key === "minutes") { input.min = "1"; input.max = "4800"; } else input.maxLength = key === "title" ? 200 : 1000;
      input.setAttribute("aria-label", `${label}, шаг ${index + 1}, действие ${taskIndex + 1}`);
      input.addEventListener("input", () => {
        step[key] = key === "minutes" ? Number(input.value) : input.value;
        if (key === "minutes") updateTotal(); else markActionChanged();
      });
      wrap.append(input); row.append(wrap);
    }
    const remove = element("button", "text-button danger-text", "Удалить шаг"); remove.type = "button";
    remove.addEventListener("click", () => { task.substeps.splice(index, 1); updateTotal(); renderSubstepEditor(target, task, taskIndex, parentInputs); });
    row.append(remove); target.append(row);
  });
  const add = element("button", "button button-secondary", "Добавить шаг"); add.type = "button"; add.disabled = steps.length >= 8;
  add.addEventListener("click", () => {
    if ((task.substeps || []).length >= 8) return;
    const used = new Set((task.substeps || []).map(step => step.id)); let number = 1;
    while (used.has(`step-${number}`)) number++;
    task.substeps = [...(task.substeps || []), {id: `step-${number}`, title: "", minutes: 15, done_when: ""}];
    updateTotal(); renderSubstepEditor(target, task, taskIndex, parentInputs);
    const fields = target.querySelectorAll("input"); fields[fields.length - 3]?.focus();
  });
  target.append(add);
}

function renderActionEditor() {
  const target = $("card-actions-editor");
  if (!target) return;
  target.replaceChildren();
  (state.cardDraft?.tasks || []).forEach((task, index) => {
    const accordion = element("details", "accordion-action");
    accordion.open = index === 0 || !task.title;
    const summary = element("summary", "", task.title || `Новое действие ${index + 1}`);
    accordion.append(summary);
    const box = element("fieldset", "action-card");
    box.append(element("legend", "", `Действие ${index + 1}`));
    const fields = [
      ["title", "Действие"], ["expected_result", "Ожидаемый результат"],
      ["done_when", "Готово, когда…"], ["minutes", "Оценка, мин"],
      ["min_minutes", "От, мин (необязательно)"], ["max_minutes", "До, мин (необязательно)"],
      ["estimate_basis", "Основание оценки"],
    ];
    const parentInputs = {};
    const substepEditor = element("section", "substep-editor");
    fields.forEach(([key, label]) => {
      const wrap = element("label", "", label);
      const input = element("input");
      input.type = ["minutes", "min_minutes", "max_minutes"].includes(key) ? "number" : "text";
      if (input.type === "number") {
        input.min = key === "minutes" ? String(Math.max(1, task.substeps?.length || 0)) : "1";
        input.max = "4800";
      }
      input.value = task[key] ?? "";
      parentInputs[key] = input;
      input.setAttribute("aria-label", `${label}, действие ${index + 1}`);
      input.addEventListener("input", () => {
        if (key === "title") summary.textContent = input.value || `Действие ${index + 1}`;
        task[key] = input.type === "number" ? input.value === "" ? null : Number(input.value) : input.value;
        if (["minutes", "min_minutes", "max_minutes", "estimate_basis"].includes(key)) {
          task.estimate_source = "user";
          task.source_ids = [];
        }
        markActionChanged();
      });
      if (key === "minutes") input.addEventListener("change", () => { normalizeSubsteps(task); renderSubstepEditor(substepEditor, task, index, parentInputs); updateControls(); });
      wrap.append(input);
      box.append(wrap);
    });
    const priorityLabel = element("label", "", "Приоритет");
    const priority = element("select"); priority.setAttribute("aria-label", `Приоритет, действие ${index + 1}`);
    for (const [value, label] of [["high", "Высокий"], ["medium", "Средний"], ["low", "Низкий"]]) { const option = element("option", "", label); option.value = value; priority.append(option); }
    priority.value = task.priority || "medium";
    priority.addEventListener("change", () => { task.priority = priority.value; markActionChanged(); });
    priorityLabel.append(priority); box.append(priorityLabel);
    const reasonLabel = element("label", "", "Почему этот приоритет");
    const reason = element("input"); reason.value = task.priority_reason || ""; reason.maxLength = 1000; reason.setAttribute("aria-label", `Обоснование приоритета, действие ${index + 1}`);
    reason.addEventListener("input", () => { task.priority_reason = reason.value; markActionChanged(); }); reasonLabel.append(reason); box.append(reasonLabel);
    renderSubstepEditor(substepEditor, task, index, parentInputs);
    box.append(substepEditor);
    renderTaskLinks(box, task, index);
    const remove = element("button", "text-button danger-text", "Удалить действие");
    remove.type = "button";
    remove.addEventListener("click", () => {
      state.cardDraft.tasks.splice(index, 1);
      state.cardDraft.tasks.forEach((other) => {
        other.depends_on = (other.depends_on || []).filter((id) => id !== task.id);
      });
      renderActionEditor();
      markActionChanged();
    });
    box.append(remove);
    accordion.append(box);
    target.append(accordion);
  });
}

function refreshRequirementLinks() {
  if (!state.cardDraft) return;
  try {
    const requirements = editedLines($("card-requirements").value, state.cardDraft.requirements, 12, "Требования");
    const used = new Set(requirements.map((item) => item.id).filter(Boolean));
    requirements.forEach((item) => {
      if (item.id) return;
      let index = 1;
      while (used.has(`req-${index}`)) index += 1;
      item.id = `req-${index}`;
      used.add(item.id);
    });
    const referenced = state.cardDraft.tasks.find((task) => (task.requirement_ids || []).some((id) => !used.has(id)));
    if (referenced) throw new ApiError(`Требование используется действием «${referenced.title}», поэтому его нельзя удалить.`);
    state.cardDraft.requirements = requirements;
    renderActionEditor();
  } catch (error) {
    cardError(error);
  }
}

function renderDailyRecommendation() {
  const target = $("daily-recommendation");
  if (!target) return;
  const days = Math.min(30, Math.max(1, Number($("plan-days").value) || 1));
  const limit = Math.max(0, Number($("plan-minutes").value) || 0);
  const reserve = Math.min(50, Math.max(0, Number($("plan-reserve")?.value) || 0));
  const work = state.planTasks.filter(task => !task.completed).reduce((sum, task) => sum + Math.max(0, Number(task.minutes) || 0), 0);
  const overrides = availability.filter(day => day && Number(day.day) <= days && Number(day.minutes) !== limit);
  const note = $("availability-overrides-note");
  if (note) {
    note.hidden = !overrides.length;
    note.textContent = overrides.length ? `Отдельные дни настроены вручную: ${overrides.length}. Обычный лимит применяется к остальным дням.` : "";
  }
  const input = { reserve_percent: reserve, start_date: $("plan-start-date")?.value || null };
  const budgets = readAvailability(days, limit).map(day => ({ ...day, usable: effectiveDayMinutes(day, {input}) }));
  // Respect busy days and explicit custom budgets; the simple recommendation uses the remaining days.
  const activeDays = budgets.filter(day => day.usable > 0).length;
  const recommended = work ? Math.ceil(work / Math.max(1, activeDays) / (1 - reserve / 100)) : 0;
  const capacity = budgets.reduce((sum, day) => sum + day.usable, 0);
  target.textContent = work
    ? `Рекомендуем примерно ${recommended} мин/день. Осталось работы: ${work} мин; резерв: ${reserve}%.` + (capacity < work ? ` Не хватает ${work - capacity} мин при текущем доступном времени. Увеличьте лимит или число дней.` : "")
    : "Отметьте действия и оценки — рассчитаем рекомендуемое время на день.";
}

function renderAvailability() {
  const target = $("availability-days");
  if (!target) return;
  const days = Math.min(30, Math.max(1, Number($("plan-days").value) || 1));
  renderDailyRecommendation();
  target.replaceChildren();
  for (let day = 1; day <= days; day += 1) {
    const label = element("label", "", `День ${day}`);
    const input = element("input");
    input.type = "number";
    input.min = "0";
    input.max = "480";
    input.value = availability[day - 1]?.minutes ?? Number($("plan-minutes").value);
    input.dataset.day = String(day);
    input.setAttribute("aria-label", `Доступно минут в день ${day}`);
    input.addEventListener("input", () => {
      availability[day - 1] = { day, minutes: Number(input.value) };
      invalidatePlan(true);
    });
    label.append(input);
    target.append(label);
  }
}

function readAvailability(days, minutes) {
  return Array.from({ length: days }, (_, index) => ({ day: index + 1, minutes: availability[index]?.minutes ?? minutes }));
}

function sameActionMeaning(current, previous) {
  if (!previous) return false;
  const sameFields = ["id", "title", "expected_result", "done_when"].every((key) => (current[key] || "") === (previous[key] || ""));
  const currentRequirements = [...(current.requirement_ids || [])].sort();
  const previousRequirements = [...(previous.requirement_ids || [])].sort();
  const stepsMeaning = (task) => (task.substeps || []).map(({id, title, done_when}) => ({id, title, done_when}));
  return sameFields && JSON.stringify(currentRequirements) === JSON.stringify(previousRequirements)
    && JSON.stringify(stepsMeaning(current)) === JSON.stringify(stepsMeaning(previous));
}

function restoreSavedTasks(plan) {
  const resetNames = [];
  state.planTasks = state.planTasks.map((task) => {
    const previous = plan.input.tasks.find((item) => item.id === task.id);
    if (!plan.stale) return previous ? { ...task, ...cloneData(previous) } : task;
    if (sameActionMeaning(task, previous)) {
      // A changed card owns its current estimate and metadata; only equivalent progress survives.
      return { ...task, completed: Boolean(previous.completed), actual_minutes: previous.actual_minutes ?? null };
    }
    if (previous && (previous.completed || previous.actual_minutes != null)) resetNames.push(task.title);
    return { ...task, completed: false, actual_minutes: null };
  });
  return resetNames;
}

async function loadSavedPlan(selection) {
  try {
    const data = await requestJSON(`/api/assignments/${encodeURIComponent(state.selectedId)}/plan`, {}, 15000);
    if (selection !== state.selection) return;
    savedPlan = data.plan;
    if (!savedPlan) {
      availability = [];
      if ($("plan-reserve")) $("plan-reserve").value = 20;
      renderAvailability();
      renderOverview();
      return;
    }
    availability = cloneData(savedPlan.input.availability);
    $("plan-days").value = availability.length;
    if ($("plan-reserve")) $("plan-reserve").value = savedPlan.input.reserve_percent || 0;
    if ($("plan-start-date")) $("plan-start-date").value = savedPlan.input.start_date || "";
    const resetNames = restoreSavedTasks(savedPlan);
    renderTaskEditor();
    renderAvailability();
    renderOverview();
    if (!savedPlan.stale) renderPlan(savedPlan.result);
    else $("plan-result").hidden = true;
    $("plan-stale").hidden = !savedPlan.stale;
    if ($("plan-save-status")) {
      $("plan-save-status").textContent = savedPlan.stale
        ? "Карточка изменилась. Прогресс неизменённых действий сохранён. Оценки взяты из новой карточки: проверьте оставшееся время и пересчитайте план."
          + (resetNames.length ? ` Сброшен прогресс изменённых действий: ${resetNames.join("; ")}.` : "")
        : "Сохранённый на сервере план и прогресс восстановлены";
    }
  } catch (error) {
    if (selection === state.selection) {
      $("plan-error").textContent = error.message;
      $("plan-error").hidden = false;
    }
  }
}

$("add-card-action")?.addEventListener("click", () => {
  if (!state.cardDraft) return;
  const used = new Set(state.cardDraft.tasks.map((task) => task.id));
  let index = 1;
  while (used.has(`action-${index}`)) index += 1;
  state.cardDraft.tasks.push({
    id: `action-${index}`, title: "", minutes: 30, estimate_source: "user", source_ids: [],
    requirement_ids: [], expected_result: "", done_when: "", estimate_basis: "Ваша предварительная оценка", depends_on: [],
  });
  renderActionEditor();
  markActionChanged();
});
$("regroup-actions")?.addEventListener("click", () => {
  if (state.planTasks.some(task => task.completed || Number(task.actual_minutes) > 0)) {
    $("actions-status").textContent = "Чтобы сохранить отмеченный прогресс, группировка недоступна. Можно отредактировать действия вручную.";
    return;
  }
  navigateWorkflow("chat");
  sendMessage("Сгруппировать в 5 действий");
});
$("card-requirements").addEventListener("change", refreshRequirementLinks);
$("plan-days").addEventListener("input", () => { renderAvailability(); invalidatePlan(true); });
$("plan-minutes").addEventListener("input", () => {
  renderAvailability();
  invalidatePlan(true);
});
$("plan-reserve")?.addEventListener("input", () => invalidatePlan(true));
$("plan-start-date")?.addEventListener("input", () => invalidatePlan(true));

$("text-project-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.initializing || state.textCreating) return;
  if (state.cardDirty || state.planDirty) {
    $("text-project-status").textContent = "Сначала сохраните или отмените изменения открытой карточки и плана.";
    return;
  }
  state.textCreating = true;
  updateControls();
  try {
    const data = await requestJSON("/api/assignments/from-text", {
      method: "POST",
      body: JSON.stringify({ title: $("project-name").value.trim(), text: $("project-text").value.trim() }),
    }, 15000);
    state.assignments.push(data.assignment);
    renderAssignments();
    const opened = await selectAssignment(data.assignment.assignment_id, true);
    if (!opened) throw new ApiError("Проект сохранён, но не открыт. Выберите его в списке проектов.");
    openManualCard();
    if (window.projectExampleCard) {
      const example = window.projectExampleCard;
      availability = cloneData(example.availability);
      $("plan-days").value = availability.length;
      renderAvailability();
      renderCard({ ...state.detail, card: cloneData(example.card) });
      state.cardDirty = true;
      updateControls();
      window.projectExampleCard = null;
    }
    $("text-project-status").textContent = "Проект создан. Проверьте карточку и добавьте действия.";
  } catch (error) {
    $("text-project-status").textContent = error.message;
  } finally {
    state.textCreating = false;
    updateControls();
  }
});
for (const id of ["project-name", "project-text"]) {
  $(id)?.addEventListener("input", () => { window.projectExampleCard = null; });
}
renderAvailability();

$("project-example")?.addEventListener("click", () => {
  const example = {
  "brief": "Рабочий проект: пять интервью о поиске функции. Участники найдены. Нужны синтез, три обоснованные рекомендации и отчёт с ограничениями и доказательствами.",
  "card": {
    "title": "Исследование поиска функции",
    "goal": {
      "text": "Найти препятствия при поиске функции и предложить изменения",
      "origin": "user",
      "source_ids": []
    },
    "deliverable": {
      "text": "Исследовательский отчёт с доказательствами",
      "origin": "user",
      "source_ids": []
    },
    "requirements": [
      {
        "id": "req-interviews",
        "text": "Провести пять 30-минутных интервью о поиске нужной функции.",
        "origin": "user",
        "source_ids": []
      },
      {
        "id": "req-synthesis",
        "text": "Сгруппировать наблюдения в темы, сохранив связь с интервью.",
        "origin": "user",
        "source_ids": []
      },
      {
        "id": "req-recommendations",
        "text": "Предложить три рекомендации; каждая опирается на наблюдения.",
        "origin": "user",
        "source_ids": []
      },
      {
        "id": "req-report",
        "text": "Собрать отчёт: метод, ограничения, выводы, рекомендации и приложение с доказательствами.",
        "origin": "user",
        "source_ids": []
      }
    ],
    "criteria": [],
    "deadline": {
      "date": null,
      "origin": "unknown",
      "source_ids": []
    },
    "tasks": [
      {
        "id": "guide",
        "title": "Подготовить сценарий и согласия участников",
        "minutes": 45,
        "estimate_source": "estimate",
        "source_ids": [],
        "requirement_ids": [
          "req-interviews"
        ],
        "expected_result": "Сценарий с пятью открытыми вопросами и формой согласия",
        "done_when": "Сценарий покрывает поиск функции; согласия готовы",
        "estimate_basis": "Допущение: участники уже найдены; 30 минут сценарий + 15 проверка",
        "min_minutes": 30,
        "max_minutes": 60,
        "depends_on": [],
        "substeps": [
          {
            "id": "guide-1",
            "title": "Зафиксировать цель интервью и критерий поиска функции",
            "minutes": 10,
            "done_when": "Цель интервью и критерий поиска записаны"
          },
          {
            "id": "guide-2",
            "title": "Написать пять открытых вопросов без подсказки ответа",
            "minutes": 15,
            "done_when": "Есть пять открытых вопросов без наводящих формулировок"
          },
          {
            "id": "guide-3",
            "title": "Подготовить форму согласия и правила обезличивания",
            "minutes": 10,
            "done_when": "Форма согласия и правила обезличивания готовы"
          },
          {
            "id": "guide-4",
            "title": "Пройти сценарий вслух и убрать наводящие вопросы",
            "minutes": 10,
            "done_when": "Пробный проход завершён; наводящие вопросы исправлены"
          }
        ]
      },
      {
        "id": "interviews",
        "title": "Провести пять интервью и записать наблюдения",
        "minutes": 200,
        "estimate_source": "estimate",
        "source_ids": [],
        "requirement_ids": [
          "req-interviews"
        ],
        "expected_result": "Пять обезличенных протоколов с идентификаторами I1–I5",
        "done_when": "Есть 5 протоколов; согласие зафиксировано; цитаты отделены от интерпретации",
        "estimate_basis": "5 × (30 минут интервью + 10 минутзаметок); поиск участников не включён",
        "min_minutes": 175,
        "max_minutes": 250,
        "depends_on": [
          "guide"
        ],
        "substeps": [
          {
            "id": "interviews-1",
            "title": "Провести интервью I1: 30 минут беседы и 10 минут обезличенных заметок",
            "minutes": 40,
            "done_when": "Согласие I1 зафиксировано; 30-минутная беседа завершена; цитаты и интерпретации разделены в заметках"
          },
          {
            "id": "interviews-2",
            "title": "Провести интервью I2: 30 минут беседы и 10 минут обезличенных заметок",
            "minutes": 40,
            "done_when": "Согласие I2 зафиксировано; 30-минутная беседа завершена; цитаты и интерпретации разделены в заметках"
          },
          {
            "id": "interviews-3",
            "title": "Провести интервью I3: 30 минут беседы и 10 минут обезличенных заметок",
            "minutes": 40,
            "done_when": "Согласие I3 зафиксировано; 30-минутная беседа завершена; цитаты и интерпретации разделены в заметках"
          },
          {
            "id": "interviews-4",
            "title": "Провести интервью I4: 30 минут беседы и 10 минут обезличенных заметок",
            "minutes": 40,
            "done_when": "Согласие I4 зафиксировано; 30-минутная беседа завершена; цитаты и интерпретации разделены в заметках"
          },
          {
            "id": "interviews-5",
            "title": "Провести интервью I5: 30 минут беседы и 10 минут обезличенных заметок",
            "minutes": 40,
            "done_when": "Согласие I5 зафиксировано; 30-минутная беседа завершена; цитаты и интерпретации разделены в заметках"
          }
        ]
      },
      {
        "id": "synthesis",
        "title": "Сгруппировать наблюдения и проверить темы",
        "minutes": 90,
        "estimate_source": "estimate",
        "source_ids": [],
        "requirement_ids": [
          "req-synthesis"
        ],
        "expected_result": "Таблица тем с ссылками на I1–I5 и противоречащими наблюдениями",
        "done_when": "Каждая тема имеет ссылки на исходные записи; частота относится только к выборке",
        "estimate_basis": "5 протоколов × 10 минут кодирования + 40 минут группировки и проверки",
        "min_minutes": 70,
        "max_minutes": 120,
        "depends_on": [
          "interviews"
        ],
        "substeps": [
          {
            "id": "synthesis-1",
            "title": "Выписать наблюдения из I1–I2 со ссылками на протоколы",
            "minutes": 20,
            "done_when": "Наблюдения I1–I2 выписаны со ссылками"
          },
          {
            "id": "synthesis-2",
            "title": "Закодировать наблюдения I3–I5 теми же метками",
            "minutes": 30,
            "done_when": "Наблюдения I3–I5 размечены согласованными метками"
          },
          {
            "id": "synthesis-3",
            "title": "Объединить повторяющиеся наблюдения в темы",
            "minutes": 15,
            "done_when": "Повторяющиеся наблюдения сгруппированы в темы"
          },
          {
            "id": "synthesis-4",
            "title": "Найти исключения и противоречащие наблюдения",
            "minutes": 15,
            "done_when": "Для тем записаны исключения или отсутствие найденных противоречий"
          },
          {
            "id": "synthesis-5",
            "title": "Проверить ссылки тем на записи и границы выборки",
            "minutes": 10,
            "done_when": "Ссылки открываются; частоты описывают только пять интервью"
          }
        ]
      },
      {
        "id": "recommend",
        "title": "Сформулировать три рекомендации с доказательствами",
        "minutes": 60,
        "estimate_source": "estimate",
        "source_ids": [],
        "requirement_ids": [
          "req-recommendations"
        ],
        "expected_result": "Три рекомендации: проблема, изменение, наблюдения, способ проверки",
        "done_when": "Все три рекомендации имеют ссылки на темы и интервью; причинность не заявлена",
        "estimate_basis": "3 рекомендации × 15 минут + 15 минут проверки связей",
        "min_minutes": 45,
        "max_minutes": 90,
        "depends_on": [
          "synthesis"
        ],
        "substeps": [
          {
            "id": "recommend-1",
            "title": "Сформулировать первое изменение и связать с темой",
            "minutes": 15,
            "done_when": "Первая рекомендация содержит проблему, изменение и ссылку на тему"
          },
          {
            "id": "recommend-2",
            "title": "Сформулировать второе изменение и связать с темой",
            "minutes": 15,
            "done_when": "Вторая рекомендация содержит проблему, изменение и ссылку на тему"
          },
          {
            "id": "recommend-3",
            "title": "Сформулировать третье изменение и связать с темой",
            "minutes": 15,
            "done_when": "Третья рекомендация содержит проблему, изменение и ссылку на тему"
          },
          {
            "id": "recommend-4",
            "title": "Указать способ проверки рекомендаций и сверить доказательства",
            "minutes": 15,
            "done_when": "Для всех рекомендаций записан способ проверки; ссылки ведут на наблюдения"
          }
        ]
      },
      {
        "id": "report",
        "title": "Собрать и проверить исследовательский отчёт",
        "minutes": 75,
        "estimate_source": "estimate",
        "source_ids": [],
        "requirement_ids": [
          "req-report"
        ],
        "expected_result": "Отчёт с методом, ограничениями, выводами, рекомендациями и приложением",
        "done_when": "Разделы присутствуют; ссылки открываются; персональные данные удалены",
        "estimate_basis": "45 минут сборка + 30 минут проверка по списку разделов",
        "min_minutes": 60,
        "max_minutes": 100,
        "depends_on": [
          "recommend"
        ],
        "substeps": [
          {
            "id": "report-1",
            "title": "Описать метод и ограничения пяти участников",
            "minutes": 15,
            "done_when": "Метод и ограничения выборки описаны"
          },
          {
            "id": "report-2",
            "title": "Собрать выводы и ссылки на темы",
            "minutes": 15,
            "done_when": "Выводы включены и связаны с темами"
          },
          {
            "id": "report-3",
            "title": "Включить три рекомендации и способы их проверки",
            "minutes": 15,
            "done_when": "В отчёте есть три рекомендации со способами проверки"
          },
          {
            "id": "report-4",
            "title": "Оформить приложение с обезличенными протоколами",
            "minutes": 15,
            "done_when": "В приложении есть обезличенные протоколы I1–I5"
          },
          {
            "id": "report-5",
            "title": "Пройти список разделов и проверить ссылки и персональные данные",
            "minutes": 15,
            "done_when": "Все разделы на месте; ссылки открываются; персональные данные удалены"
          }
        ]
      }
    ],
    "questions": []
  },
  "availability": [
    {
      "day": 1,
      "minutes": 90
    },
    {
      "day": 2,
      "minutes": 0
    },
    {
      "day": 3,
      "minutes": 120
    },
    {
      "day": 4,
      "minutes": 120
    },
    {
      "day": 5,
      "minutes": 120
    }
  ]
};
  window.projectExampleCard = example;
  $("project-name").value = example.card.title;
  $("project-text").value = example.brief + "\n\n" + example.card.requirements.map((item) => item.text).join("\n");
  $("text-project-status").textContent = "Рабочий пример. Нажмите «Создать проект», затем проверьте карточку и оценки. Пять дней включают занятый день; ожидаемый дефицит — 20 минут.";
});

$("theory-find").addEventListener("click", () => loadRouteTheory(true));

document.querySelectorAll("[data-route-view]").forEach((button) => {
  button.addEventListener("click", () => {
    routeView = button.dataset.routeView;
    try { window.localStorage.setItem("project-navigator-route-view", routeView); } catch (_) { /* Keep the choice for this page. */ }
    updateRouteVisibility();
  });
});


$("refine-estimates")?.addEventListener("click", () => {
  if ($("refine-estimates").disabled) return;
  navigateWorkflow("chat");
  sendMessage("Переоцени длительность всех действий с учётом объёма, сложности, подготовки, решения и проверки. Используй доступную теорию и мой фактический темп, если он есть. Предложи обоснованные диапазоны времени и более подробные подшаги; сохрани прогресс и зависимости.");
});

initialize();
