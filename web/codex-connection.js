(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('codex-panel')) return;
  let project = {}, generation = 0, busy = false, refreshing = false, lastActivity = null;
  const message = text => { $('codex-status').textContent = text; };
  const clearToken = () => { $('codex-token').value = ''; $('codex-token-box').hidden = true; };
  const active = () => project.id && project.ready && project.visible !== false && document.visibilityState !== 'hidden' && document.hasFocus();
  const node = (tag, text) => { const el = document.createElement(tag); el.textContent = text; return el; };
  const button = (text, fn) => { const el = node('button', text); el.type = 'button'; el.className = 'button button-secondary'; el.addEventListener('click', fn); return el; };
  const operations = {connect:'Подключение создано', revoke:'Доступ отозван', claim:'Взято в работу',
    partial:'Частичный отчёт', blocked:'Сообщение о блокировке', awaiting_review:'Отправлено на проверку',
    accept:'Результат принят', dismiss:'Работа закрыта', start:'Начало работы', resume:'Работа продолжена',
    pause:'Пауза', interrupt:'Работа прервана', complete:'Выполнено', reopen:'Открыто повторно',
    record:'Запись времени', update_plan:'Изменение плана'};
  const timeSources = {user:'со слов пользователя', measured:'измерено', estimate:'оценка', unknown:'неизвестно'};
  function auditEntry(entry) {
    const row = node('li', '');
    const when = new Date(entry.created_at).toLocaleString('ru-RU');
    row.append(node('strong', operations[entry.operation] || entry.operation), node('p', `${when} · ${entry.actor}`));
    if (entry.reason) row.append(node('p', entry.reason));
    const details = entry.details || {};
    if (details.time) {
      const {minutes, source, basis} = details.time;
      row.append(node('p', [minutes != null ? `${minutes} мин` : '', timeSources[source], basis].filter(Boolean).join(' · ')));
    }
    if (entry.operation === 'update_plan') {
      for (const task of details.tasks || []) {
        const fields = [task.title || task.id, task.minutes != null ? `оценка ${task.minutes} мин` : '',
          task.priority ? `приоритет ${task.priority}` : '', task.substeps ? `подшагов: ${task.substeps.length}` : '',
          task.depends_on ? `предшественники: ${task.depends_on.join(', ') || 'нет'}` : ''];
        row.append(node('p', fields.filter(Boolean).join(' · ')));
      }
      if (details.availability) row.append(node('p', details.availability.map(d => `День ${d.day} — ${d.minutes} мин`).join(', ')));
      if (details.reserve_percent != null) row.append(node('p', `Резерв ${details.reserve_percent}%`));
    }
    if (entry.task_id) row.append(node('small', [entry.task_id, entry.substep_id].filter(Boolean).join(' / ')));
    for (const item of details.evidence || []) row.append(node('p', item));
    return row;
  }
  async function api(path, options = {}, context = project.id) {
    const response = await fetch(`/api/assignments/${encodeURIComponent(context)}/codex/${path}`, { credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, ...options });
    if (!response.ok) throw Error(response.status === 409 ? 'Версия проекта изменилась. Обновите проект и повторите.' : 'Не удалось выполнить запрос. Повторите позже.');
    return response.json();
  }
  async function action(fn) {
    if (busy || !project.ready) return;
    busy = true; const stamp = generation; message('Выполняем…');
    try { await fn(stamp); if (stamp === generation) message('Готово.'); }
    catch (error) { if (stamp === generation) message(error.message); }
    finally { if (stamp === generation) busy = false; }
  }
  async function refresh() {
    if (!active() || busy || refreshing) return;
    refreshing = true;
    const stamp = generation, id = project.id;
    try {
      const changes = await api('changes', {}, id);
      if (stamp !== generation) return;
      const activity = `${changes.card_revision}:${changes.plan_revision}:${changes.activity_version}`;
      if ((lastActivity != null && activity !== lastActivity) || (lastActivity == null && ((changes.plan_revision != null && changes.plan_revision !== project.plan_revision) || (changes.card_revision != null && changes.card_revision !== project.card_revision)))) window.dispatchEvent(new CustomEvent('navigator-codex-updated', { detail: { id, ...changes } }));
      lastActivity = activity;
      if (!$('codex-panel').open) return;
      const [connections, result] = await Promise.all([api('connections', {}, id), api('runs', {}, id)]);
      if (stamp !== generation) return;
      $('codex-connections').replaceChildren();
      for (const item of connections.connections || []) {
        const row = node('li', `${item.label} · до ${item.expires_at}${item.revoked_at ? ' · отозвано' : ''} · права: ${(item.scopes || ['read']).join(', ')} · ${item.completion_mode === 'auto' ? 'автопринятие' : 'проверка владельцем'} `);
        if (!item.revoked_at) row.append(button('Отозвать', () => action(async stamp => {
          await api(`connections/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
          if (stamp === generation) { clearToken(); busy = false; await refresh(); }
        })));
        $('codex-connections').append(row);
      }
      if (!(connections.connections || []).length) $('codex-connections').append(node('li', 'Подключений пока нет.'));
      $('codex-runs').replaceChildren();
      const statuses = { in_progress: 'В работе', partial: 'Частично выполнено', awaiting_review: 'Ожидает проверки', blocked: 'Нужна помощь', accepted: 'Принято', dismissed: 'Отклонено' };
      for (const run of result.runs || []) {
        const row = node('li', '');
        row.append(node('strong', [run.task_title, run.substep_id, statuses[run.status] || run.status].filter(Boolean).join(' · ')), node('p', run.summary || 'Результат ещё не описан.'));
        const evidence = node('ul', '');
        for (const text of run.evidence || []) evidence.append(node('li', text));
        row.append(evidence);
        if (run.status === 'awaiting_review') row.append(button('Принять результат', () => {
          if (project.planDirty) { message('Сначала сохраните или отмените изменения карточки и плана.'); return; }
          action(async stamp => {
            await api(`runs/${encodeURIComponent(run.id)}/accept`, { method: 'POST', body: JSON.stringify({ card_revision: run.card_revision, plan_revision: run.plan_revision }) });
            if (stamp !== generation) return;
            window.dispatchEvent(new CustomEvent('navigator-codex-accepted', { detail: { id: project.id } }));
          });
        }));
        if (!['accepted', 'dismissed'].includes(run.status)) row.append(button('Отклонить результат', () => action(async stamp => {
          await api(`runs/${encodeURIComponent(run.id)}/dismiss`, { method: 'POST', body: '{}' });
          if (stamp === generation) { busy = false; await refresh(); }
        })));
        $('codex-runs').append(row);
      }
      if (!(result.runs || []).length) $('codex-runs').append(node('li', 'Результатов от Codex пока нет.'));
      for (const entry of result.audit || []) $('codex-runs').append(auditEntry(entry));
    } catch (error) { if (stamp === generation) message(error.message); }
    finally { if (stamp === generation) refreshing = false; }
  }
  $('codex-create').addEventListener('click', () => {
    if (!$('codex-label').reportValidity() || !$('codex-days').reportValidity()) return;
    action(async stamp => {
      clearToken();
      if ($('codex-completion-mode')?.value === 'auto' && !$('codex-scope-progress')?.checked) throw Error('Для автопринятия разрешите обновление прогресса.');
      const result = await api('connections', { method: 'POST', body: JSON.stringify({ label: $('codex-label').value.trim() || 'Мой Codex', expires_days: Number($('codex-days').value), scopes: ['read', ...['reports', 'progress', 'plan'].filter(scope => $('codex-scope-' + scope)?.checked)], completion_mode: $('codex-completion-mode')?.value === 'auto' ? 'auto' : 'review' }) });
      if (stamp !== generation) return;
      $('codex-token').value = result.token; $('codex-token-box').hidden = false;
      busy = false; await refresh();
    });
  });
  $('codex-copy').addEventListener('click', async () => {
    const stamp = generation;
    try { await navigator.clipboard.writeText($('codex-token').value); if (stamp === generation) message('Токен скопирован. Сохраните его в локальный env-файл.'); }
    catch (_) { if (stamp === generation) message('Копирование недоступно. Выделите токен и скопируйте вручную.'); }
  });
  $('codex-hide-token').addEventListener('click', clearToken);
  $('codex-refresh').addEventListener('click', refresh);
  $('codex-panel').addEventListener('toggle', () => { if ($('codex-panel').open) refresh(); });
  window.NavigatorCodex = { setProject(next) {
    const changed = next.id !== project.id || next.ready !== project.ready;
    if (changed) { generation++; busy = false; refreshing = false; lastActivity = null; clearToken(); $('codex-connections').replaceChildren(); $('codex-runs').replaceChildren(); message(''); }
    project = { ...next };
    $('codex-panel').hidden = !project.ready;
  } };
  setInterval(refresh, 10000);
  window.addEventListener('focus', refresh);
  document.addEventListener?.('visibilitychange', refresh);
})();
