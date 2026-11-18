/* Project materials, source reader, progress analytics and reviewed assistant edits. */
"use strict";
(function () {
  let analyticsVersion = 0, sourceVersion = 0, materialBusy = false, applying = false;
  function safeExternal(value) {
    try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch (_) { return null; }
  }
  function status(id, message, error = false) { const node = $(id); node.textContent = message; node.classList.toggle("inline-error", error); }
  function dirtyMessage() { return state.cardDirty || state.planDirty ? "Сначала сохраните или отмените текущие изменения карточки и плана." : null; }
  async function addMaterial(payload, form = null) {
    if (materialBusy || !isUploaded()) return;
    const dirty = dirtyMessage(); if (dirty) { status("material-add-status", dirty, true); $("add-material").open = true; return; }
    const id = state.selectedId, selection = state.selection;
    materialBusy = true; updateExtraControls(); status("material-add-status", "Сохраняем материал…"); $("add-material").open = true;
    try {
      const data = await requestJSON(`/api/assignments/${encodeURIComponent(id)}/materials`, { method: "POST", body: payload instanceof FormData ? payload : JSON.stringify(payload) }, 60000);
      if (selection !== state.selection) return;
      state.detail = data.assignment; state.assignments = state.assignments.map(a => a.assignment_id === id ? data.assignment : a);
      renderAssignments(); renderDocuments(data.assignment); renderSources(data.assignment.sources);
      if (form) { form.reset(); syncMaterialKind(); }
      status("material-add-status", "Материал добавлен. Теперь он доступен в поиске и ассистенту.");
    } catch (error) { if (selection === state.selection) status("material-add-status", error.message, true); }
    finally { materialBusy = false; updateExtraControls(); }
  }
  function syncMaterialKind() {
    const pdf = $("material-kind").value === "pdf";
    $("material-text-fields").hidden = pdf; $("material-file-field").hidden = !pdf;
    $("material-text").required = !pdf; $("material-text").disabled = pdf; $("material-url").disabled = pdf;
    $("material-files").required = pdf; $("material-files").disabled = !pdf;
  }
  $("material-kind").addEventListener("change", syncMaterialKind);
  $("material-add-form").addEventListener("submit", event => {
    event.preventDefault(); const form = event.currentTarget; if (!form.reportValidity()) return;
    const title = $("material-title").value.trim();
    if ($("material-kind").value === "pdf") {
      const files = Array.from($("material-files").files);
      if (!files.length || files.length > 3) { status("material-add-status", "Выберите от одного до трёх PDF.", true); return; }
      const data = new FormData(); data.append("title", title); files.forEach(file => data.append("files", file)); addMaterial(data, form);
    } else {
      const raw = $("material-url").value.trim(), url = raw ? safeExternal(raw) : null;
      if (raw && !url) { status("material-add-status", "Нужна ссылка http:// или https:// без логина и пароля.", true); return; }
      addMaterial({ title, text: $("material-text").value.trim(), external_url: url }, form);
    }
  });

  function sourceIdFromLink(link) {
    const href = link.getAttribute("href") || "";
    const direct = href.match(/^#demo-source-([A-Za-z0-9_-]+)$/) || href.match(/(?:^|\/)sources\/([A-Za-z0-9_-]+)(?:[?#].*)?$/);
    if (direct) return direct[1];
    // A PDF in the document list opens its first saved fragment before the original.
    if (link.classList.contains("document-open")) {
      const source = (state.detail?.sources || []).find(s => s.document_url && href.endsWith(s.document_url));
      if (source) return source.source_id;
    }
    return null;
  }
  async function openSource(id) {
    const version = ++sourceVersion, dialog = $("source-dialog"), target = $("source-dialog-content");
    dialog.classList.remove("is-pdf");
    $("source-dialog-title").textContent = "Материал"; target.replaceChildren(element("p", "", "Открываем материал…"));
    if (!dialog.open) dialog.showModal();
    try {
      const data = await requestJSON(`/api/sources/${encodeURIComponent(id)}`, {}, 15000), source = data.source || data;
      if (version !== sourceVersion || !dialog.open) return;
      $("source-dialog-title").textContent = source.title || "Материал проекта"; target.replaceChildren();
      target.append(element("p", "source-location", sourceLocation(source)));
      if (sourceAttribution(source)) target.append(element("p", "source-location", sourceAttribution(source)));
      if (source.reference_note) target.append(element("p", "empty-note", source.reference_note));
      if (source.scope === "shared_research") target.append(element("p", "source-location", `${Array.isArray(source.authors) ? source.authors.join(", ") : source.authors || ""} · ${source.year || ""} · DOI: ${source.doi || ""}`));
      const excerpt = source.preview || source.excerpt || "Текст материала отсутствует.";
      const summary = element("section", "source-summary"); summary.append(element("h3", "", "Кратко по материалу"));
      renderStructuredText(summary.appendChild(element("div", "structured-text")), source.summary || String(excerpt).slice(0, 400));
      summary.append(element("p", "empty-note", "Краткая выдержка из сохранённого текста. Полный фрагмент — ниже.")); target.append(summary);
      const original = element("details", "source-original"); original.open = true; original.append(element("summary", "", "Сохранённый фрагмент"));
      renderStructuredText(original.appendChild(element("div", "structured-text")), excerpt); target.append(original);
      const links = element("div", "source-original-links");
      for (const [url, label] of [[source.file_url || source.document_url, "Открыть исходный PDF ↗"], [source.external_url, "Открыть статью ↗"]]) {
        const safe = url?.startsWith("/materials/") || url?.startsWith("/research/") ? sourceURL(url) : safeExternal(url);
        if (!safe) continue;
        const a = element("a", "button button-secondary", label); a.href = safe; a.target = "_blank"; a.rel = "noopener noreferrer"; links.append(a);
      }
      target.append(links);
    } catch (error) { if (version === sourceVersion) target.replaceChildren(element("p", "inline-error", error.message)); }
  }
  function openMaterial(link) {
    const dialog = $("source-dialog"), target = $("source-dialog-content");
    const pdf = sourceURL(link.dataset.materialPdf);
    const group = !pdf && materialGroups(state.detail?.sources || []).find(item => item.sources[0].source_id === link.dataset.materialText);
    if (!pdf && !group) return false;
    sourceVersion++;
    dialog.classList.toggle("is-pdf", Boolean(pdf));
    $("source-dialog-title").textContent = link.dataset.materialTitle || group?.title || "Материал проекта";
    target.replaceChildren();
    if (pdf) {
      const toolbar = element("div", "material-reader-toolbar");
      const original = element("a", "button button-secondary", "Открыть в новой вкладке ↗");
      original.href = pdf; original.target = "_blank"; original.rel = "noopener noreferrer";
      toolbar.append(original); target.append(toolbar);
      const frame = element("iframe", "material-pdf");
      frame.src = pdf; frame.title = `PDF: ${link.dataset.materialTitle || "Материал проекта"}`;
      target.append(frame);
    } else {
      const body = element("div", "structured-text material-full-text");
      renderStructuredText(body, group.sources.map(source => source.excerpt || "").join(" "));
      target.append(body);
      const external = safeExternal(group.sources[0].external_url);
      if (external) {
        const original = element("a", "button button-secondary", "Открыть статью ↗");
        original.href = external; original.target = "_blank"; original.rel = "noopener noreferrer"; target.append(original);
      }
    }
    if (!dialog.open) dialog.showModal();
    return true;
  }
  document.addEventListener("click", event => {
    const link = event.target.closest?.("a.source-chip, a.source-open, a.document-open"); if (!link) return;
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    if ((link.dataset.materialPdf || link.dataset.materialText) && openMaterial(link)) { event.preventDefault(); return; }
    const id = sourceIdFromLink(link); if (!id) return; event.preventDefault(); openSource(id);
  });
  $("source-close").addEventListener("click", () => $("source-dialog").close());
  $("source-dialog").addEventListener("close", () => { sourceVersion++; $("source-dialog").classList.remove("is-pdf"); $("source-dialog-content").replaceChildren(); });

  function svgNode(tag, attributes = {}) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key,value] of Object.entries(attributes)) node.setAttribute(key, String(value)); return node;
  }
  function dateText(value) { const d = new Date(value); return Number.isNaN(d.valueOf()) ? "—" : new Intl.DateTimeFormat("ru-RU", { day:"2-digit", month:"2-digit", timeZone:"Europe/Moscow" }).format(d); }
  function trendChart(history, key, heading, unit) {
    const panel = element("section", "analytics-chart panel"); panel.append(element("h3", "", heading));
    const known = history.filter(row => row[key] != null && Number.isFinite(Number(row[key])));
    if (!known.length) { panel.append(element("p", "empty-note", "История появится после сохранения плана и ввода фактических данных.")); return panel; }
    const rows = known.slice(-20), values = rows.map(row => Number(row[key]) || 0), max = Math.max(1, ...values);
    const svg = svgNode("svg", {viewBox:"0 0 600 210", role:"img", "aria-label": `${heading}: ${values.join(", ")} ${unit}; по последним ${rows.length} сохранениям`});
    const points = values.map((value, i) => [46 + (rows.length === 1 ? 250 : i * 518/(rows.length-1)), 164-value/max*130]);
    for (const fraction of (max < 2 ? [0,1] : [0,.5,1])) {
      const y = 164 - fraction*130; svg.append(svgNode("line", {x1:46,x2:568,y1:y,y2:y,class:"chart-grid"}));
      const label = svgNode("text", {x:36,y:y+4,"text-anchor":"end",class:"chart-axis"}); label.textContent = String(Math.round(max*fraction)); svg.append(label);
    }
    if (points.length > 1) svg.append(svgNode("polyline", {points:points.map(p=>p.join(",")).join(" "),class:"chart-line"}));
    points.forEach(([cx,cy], i) => { const dot=svgNode("circle",{cx,cy,r:4,class:"chart-dot"}); const title=svgNode("title");title.textContent=`${dateText(rows[i].recorded_at)} · ${values[i]} ${unit}`; dot.append(title); svg.append(dot); });
    for (const i of [...new Set([0,rows.length-1])]) { const label=svgNode("text",{x:points[i][0],y:194,"text-anchor":"middle",class:"chart-axis"});label.textContent=dateText(rows[i].recorded_at);svg.append(label); }
    panel.append(svg,element("p","empty-note",rows.length === 1 ? "Пока одно сохранение. Следующие покажут изменения." : "По времени сохранения данных, а не по дате выполнения работы.")); return panel;
  }
  function renderAnalytics(data) {
    const target=$("analytics-content"); target.replaceChildren();
    const totals=data.totals || {}, metrics=element("div","analytics-metrics");
    for(const [label,value] of [["Выполнено",`${totals.completed_count || 0} / ${totals.task_count || 0}`],[totals.actual_unknown_count > 0 && totals.actual_minutes != null ? "Факт · заполнен частично" : "Фактическое время", totals.actual_minutes == null ? "Не указано" : formatMinutes(totals.actual_minutes)],["Оценка действий",formatMinutes(totals.estimated_minutes || 0)],["Осталось действий",Math.max(0,(totals.task_count || 0)-(totals.completed_count || 0))]]) {
      const tile=element("div","analytics-metric panel");tile.append(element("span","",label),element("strong","",value));metrics.append(tile);
    }
    target.append(metrics);
    const charts=element("div","analytics-charts");charts.append(trendChart(data.history || [],"actual_minutes","Учтённое время","мин"),trendChart(data.history || [],"completed_count","Выполненные действия","шт."));target.append(charts);
    const comparison=element("section","analytics-comparison panel");comparison.append(element("h3","","Оценка и фактическое время"),element("p","empty-note","Факт поступает от владельца или из отчётов Codex. Измеренное время и оценка затраченного времени различаются. Для незавершённых действий оценка отражает оставшуюся работу; их разница не является перерасходом."));
    if (!data.tasks?.length) comparison.append(element("p","empty-note","Добавьте действия и сохраните план."));
    for(const task of data.tasks || []) {
      const row=element("div","comparison-row"), title=element("div","comparison-title");title.append(element("strong","",task.title),element("span","",({pending:"Не начато",in_progress:"В работе",partial:"Частично выполнено",paused:"Пауза",interrupted:"Прервано",blocked:"Нужна помощь",awaiting_review:"Ожидает проверки",completed:"Выполнено"})[task.progress_status] || (task.completed ? "Выполнено" : "В работе")));row.append(title);
      const max=Math.max(task.estimated_minutes || 0,task.actual_minutes || 0,1), bars=element("div","comparison-bars");
      for(const [label,value,kind] of [[task.completed?"Оценка":"Остаток",task.estimated_minutes,"estimate"],["Факт",task.actual_minutes,"actual"]]) {
        const line=element("div","comparison-line"), track=element("span","comparison-track"), fill=element("span",`comparison-fill ${kind}`);fill.style.width=`${Math.max(0,Number(value)||0)/max*100}%`;track.append(fill);line.append(element("span","",label),track,element("span","",value == null ? "—" : `${value} мин`));bars.append(line);
      }
      if (task.measured_minutes) bars.append(element("p","empty-note",`Измерено: ${task.measured_minutes} мин`));
      if (task.estimated_actual_minutes != null) bars.append(element("p","empty-note",`Оценка затраченного времени: ${task.estimated_actual_minutes} мин`));
      if (task.actual_partial) bars.append(element("p","empty-note","Факт заполнен частично по подшагам"));
      if (task.actual_source) bars.append(element("p","empty-note",`Источник факта: ${{user:"владелец",measured:"измерение",mixed:"смешанный",unknown:"неизвестен"}[task.actual_source] || task.actual_source}`));
      row.append(bars);comparison.append(row);
    } target.append(comparison);
    const history=element("details","analytics-history panel"); history.append(element("summary","","История изменений"));
    const list=element("ol"); for(const row of (data.history || []).slice(-20).reverse()) list.append(element("li","",`${dateText(row.recorded_at)} · версия ${row.revision} · ${row.actual_minutes == null ? "факт не указан" : `учтено ${row.actual_minutes} мин`} · выполнено ${row.completed_count}${row.baseline ? " · первая запись" : row.correction ? " · корректировка предыдущих данных" : ""}`));
    if(!list.childElementCount) history.append(element("p","empty-note","Нет записей."));else history.append(list);target.append(history);
    const ask=element("div","analytics-ask panel");ask.append(element("div","", "Обсудите с ассистентом, что мешает продвижению и какой следующий шаг важнее."));const button=element("button","button button-primary","Разобрать мой прогресс →");button.type="button";button.addEventListener("click",()=>{navigateWorkflow("chat");$("question").value="Проанализируй мой фактический прогресс, оценки и доступное время. Что стоит сделать следующим, а что можно перенести с объяснением?";$("question").focus();});ask.append(button);target.append(ask);
  }
  async function loadAnalytics() {
    const id=state.selectedId, version=++analyticsVersion;
    if(!id || !isUploaded()) { $("analytics-content").replaceChildren(element("p","empty-note","Создайте свой проект, чтобы сохранять прогресс.")); return; }
    status("analytics-status","Загружаем прогресс…");$("analytics-content").replaceChildren();$("analytics-refresh").disabled=true;
    try { const data=await requestJSON(`/api/assignments/${encodeURIComponent(id)}/analytics`,{},15000);if(version!==analyticsVersion || id!==state.selectedId)return;renderAnalytics(data.analytics);status("analytics-status",state.cardDirty || state.planDirty ? "Показаны сохранённые данные. В форме есть несохранённые изменения." : ""); }
    catch(error) { if(version===analyticsVersion) status("analytics-status",error.message,true); }
    finally { if(version===analyticsVersion)$("analytics-refresh").disabled=false; }
  }
  $("analytics-refresh").addEventListener("click",loadAnalytics);

  function renderProposal(node,data) {
    if(data.answer_basis === "project_context") node.append(element("p","answer-basis","По данным текущего плана и сохранённого прогресса."));
    const proposal=data.action_proposal;if(!proposal?.proposal_id)return;
    const card=element("section","assistant-proposal");card.append(element("h3","","Изменения в плане"),element("p","",proposal.summary));
    const list=element("ul");for(const op of proposal.operations || []) {if(op.op === "regroup") { list.append(element("li","","Объединить работу в 5 действий; общий бюджет сохранится")); continue; }const task=state.planTasks.find(t=>t.id===op.task_id);const label={add:"Добавить",update:"Изменить",complete:"Отметить выполненным"}[op.op] || "Обновить";list.append(element("li","",`${label}: ${op.task?.title || task?.title || op.task_id}${op.actual_minutes != null ? ` · факт ${op.actual_minutes} мин` : ""}${op.changes && Object.keys(op.changes).length ? ` · ${Object.entries(op.changes).map(([key,value])=>`${({title:"название",minutes:"оценка",actual_minutes:"факт",completed:"выполнено",priority:"приоритет",priority_reason:"основание приоритета",expected_result:"результат",done_when:"готово, когда",depends_on:"зависит от",requirement_ids:"требования"})[key] || key}: ${Array.isArray(value)?value.join(", "):value}`).join("; ")}` : ""}`));}card.append(list);
    if (Array.isArray(proposal.preview_tasks)) {
      const preview = element("ol", "proposal-preview");
      for (const task of proposal.preview_tasks) {
        const row = element("li");
        row.append(element("strong", "", `${task.title} · ${task.minutes} мин`));
        if (task.expected_result) row.append(element("p", "", `Результат: ${task.expected_result}`));
        if (task.done_when) row.append(element("p", "", `Готово, когда: ${task.done_when}`));
        const steps = element("ul");
        for (const step of task.substeps || []) steps.append(element("li", "", `${step.title}${Number.isFinite(step.minutes) ? ` · ${step.minutes} мин` : ""}`));
        row.append(steps); preview.append(row);
      }
      card.append(preview);
    }
    const actions=element("div","proposal-actions"), apply=element("button","button button-primary","Применить к плану"), dismiss=element("button","button button-secondary","Оставить без изменений"), message=element("p","proposal-status");apply.type=dismiss.type="button";message.setAttribute("role","status");actions.append(apply,dismiss);card.append(actions,message);node.append(card);
    const assignmentId=state.selectedId;
    dismiss.addEventListener("click",()=>{apply.disabled=dismiss.disabled=true;message.textContent="Предложение отклонено. План не изменён.";});
    apply.addEventListener("click",async()=>{
      const dirty=dirtyMessage();if(dirty){message.textContent=dirty;return;}if(applying)return;
      applying=true;apply.disabled=dismiss.disabled=true;message.textContent="Проверяем и сохраняем изменения…";
      try {
        const result=await requestJSON(`/api/assignments/${encodeURIComponent(assignmentId)}/assistant-actions`,{method:"POST",body:JSON.stringify({proposal_id:proposal.proposal_id,card_revision:proposal.card_revision,plan_revision:proposal.plan_revision,apply:true})},15000);
        if(assignmentId!==state.selectedId)return;
        applyAssignment(result.assignment);savedPlan=result.plan;state.planDirty=false;
        if(savedPlan){restoreSavedTasks(savedPlan);renderTaskEditor();renderPlan(savedPlan.result);}
        updateControls();message.textContent="Изменения сохранены. Действия, расписание и аналитика обновлены.";
        const link=element("button","text-button","Открыть действия и план →");link.type="button";link.addEventListener("click",()=>navigateWorkflow("planning"));card.append(link);analyticsVersion++;
      } catch(error){message.textContent=error.message;message.classList.add("inline-error");if(error.status!==409){apply.disabled=dismiss.disabled=false;}}
      finally{applying=false;}
    });
  }
  function updateExtraControls() {
    const busy=Boolean(state.loading || state.analysis || state.confirmation || state.plan || applying);
    $("material-add-submit").disabled=materialBusy || busy || !isUploaded();
    $("save-actions").disabled=busy || !state.cardDraft || !state.cardDirty || state.planDirty;
    $("discard-actions").hidden=!state.cardDirty;
    if(state.cardDirty) status("actions-status",state.actionEditing ? "Есть несохранённые изменения действий." : "В карточке проекта есть несохранённые изменения. Они сохранятся вместе с действиями.");
    else if(!$("actions-status").classList.contains("inline-error")) status("actions-status","");
    if(state.planDirty) status("actions-status","Сохраните изменения времени и прогресса перед редактированием декомпозиции.");
  }
  $("save-actions").addEventListener("click",async event=>{
    if(state.planDirty)return;
    await confirmCard(event);
    if(!$("card-error").hidden)status("actions-status",$("card-error").textContent,true);
    else if(!state.cardDirty)status("actions-status","Действия сохранены. Пересчитайте расписание с текущей доступностью.");
  });
  $("discard-actions").addEventListener("click",async()=>{renderCard(state.detail,true);await loadSavedPlan(state.selection);status("actions-status","Правки действий отменены.");updateControls();});
  window.ProjectWorkspace={loadAnalytics,renderProposal,openSource,updateControls:updateExtraControls,invalidateAnalytics(){analyticsVersion++;},resetProject(){analyticsVersion++;sourceVersion++;if($("source-dialog").open)$("source-dialog").close();$("analytics-content").replaceChildren();status("analytics-status","");status("actions-status","");status("material-add-status","");}};
  syncMaterialKind();updateExtraControls();
})();
