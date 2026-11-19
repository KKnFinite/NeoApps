/* Keep More Tools collapsed unless opened or containing the current page.
 * Preference is UI-only, per-tab; no permissions or domain data are persisted. */
(() => {
  'use strict';
  const panels = [...document.querySelectorAll('[data-neostaffing-nav-more]')];
  if (!panels.length) return;
  const key = 'neostaffing.more-tools.v1';
  let stored = null;
  try { stored = sessionStorage.getItem(key); } catch (_) { /* storage optional */ }
  const activeSecondary = panels.some(panel => panel.querySelector('a[aria-current="page"]'));
  for (const panel of panels) {
    if (activeSecondary) panel.open = true;
    else if (stored !== null) panel.open = stored === 'open';
    panel.querySelector(':scope > summary')?.addEventListener('click', () => {
      const open = !panel.open;
      try { sessionStorage.setItem(key, open ? 'open' : 'closed'); } catch (_) {}
      for (const other of panels) if (other !== panel) other.open = open;
    });
  }
})();
