/* Apply before styles load so the first paint follows the saved preference. */
(function () {
  'use strict';
  const key = 'project-navigator-theme';
  const valid = value => ['system', 'light', 'dark'].includes(value);
  let media;
  try { media = window.matchMedia('(prefers-color-scheme: dark)'); } catch (_) { media = null; }
  let preference = 'system';
  try { const saved = window.localStorage.getItem(key); if (valid(saved)) preference = saved; } catch (_) { /* Private/storage-restricted browsing. */ }
  function apply() {
    const theme = preference === 'system' ? (media && media.matches ? 'dark' : 'light') : preference;
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    const themeColor = document.querySelector && document.querySelector('meta[name="theme-color"]');
    if (themeColor) themeColor.setAttribute('content', theme === 'dark' ? '#101722' : '#f5f7fb');
    const select = document.getElementById('theme-select');
    if (select) select.value = preference;
  }
  function setPreference(value) {
    if (!valid(value)) return;
    preference = value;
    try { window.localStorage.setItem(key, value); } catch (_) { /* The current page still changes. */ }
    apply();
  }
  apply();
  if (media) {
    if (media.addEventListener) media.addEventListener('change', apply);
    else if (media.addListener) media.addListener(apply);
  }
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    preference = valid(event.newValue) ? event.newValue : 'system';
    apply();
  });
  function bind() {
    const select = document.getElementById('theme-select');
    if (select) { select.value = preference; select.addEventListener('change', () => setPreference(select.value)); }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind, { once: true });
  else bind();
  window.NavigatorTheme = Object.freeze({ setPreference, getPreference: () => preference });
}());
