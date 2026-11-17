(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('codex-panel')) return;
  let project = {}, generation = 0, busy = false;
  const message = text => { $('codex-status').textContent = text; };
  const clearToken = () => { $('codex-token').value = ''; $('codex-token-box').hidden = true; };
  const active = () => project.id && project.ready && !$('planning').hidden && document.visibilityState !== 'hidden' && document.hasFocus();
  const node = (tag, text) => { const el = document.createElement(tag); el.textContent = text; return el; };
  const button = (text, fn) => { const el = node('button', text); el.type = 'button'; el.className = 'button button-secondary'; el.addEventListener('click', fn); return el; };
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
    if (!active() || busy) return;
    const stamp = generation, id = project.id;
    try {
      const [connections, result] = await Promise.all([api('connections', {}, id), api('runs', {}, id)]);
      if (stamp !== generation) return;
      $('codex-connections').replaceChildren();
      for (const item of connections.connections || []) {
        const row = node('li', `${item.label} · до ${item.expires_at}${item.revoked_at ? ' · отозвано' : ''} `);
        if (!item.revoked_at) row.append(button('Отозвать', () => action(async stamp => {
          await api(`connections/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
          if (stamp === generation) { clearToken(); busy = false; await refresh(); }
        })));
        $('codex-connections').append(row);
      }
      if (!(connections.connections || []).length) $('codex-connections').append(node('li', 'Подключений пока нет.'));
      $('codex-runs').replaceChildren();
      const statuses = { in_progress: 'В работе', awaiting_review: 'Ожидает проверки', blocked: 'Нужна помощь', accepted: 'Принято', dismissed: 'Отклонено' };
      for (const run of result.runs || []) {
        const row = node('li', '');
        row.append(node('strong', `${run.task_title} · ${statuses[run.status] || run.status}`), node('p', run.summary || 'Результат ещё не описан.'));
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
    } catch (error) { if (stamp === generation) message(error.message); }
  }
  $('codex-create').addEventListener('click', () => {
    if (!$('codex-label').reportValidity() || !$('codex-days').reportValidity()) return;
    action(async stamp => {
      clearToken();
      const result = await api('connections', { method: 'POST', body: JSON.stringify({ label: $('codex-label').value.trim() || 'Мой Codex', expires_days: Number($('codex-days').value) }) });
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
    if (changed) { generation++; busy = false; clearToken(); $('codex-connections').replaceChildren(); $('codex-runs').replaceChildren(); message(''); }
    project = { ...next };
    $('codex-panel').hidden = !project.ready;
    if (changed && $('codex-panel').open) refresh();
  } };
  setInterval(() => { if ($('codex-panel').open) refresh(); }, 10000);
  window.addEventListener('focus', () => { if ($('codex-panel').open) refresh(); });
})();
