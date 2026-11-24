(() => {
  'use strict';
  const root = document.querySelector('[data-daily-briefing]');
  if (!root) return;
  const preview = root.querySelector('#briefing-text');
  const toggle = root.querySelector('#briefing-delay-details');
  const mode = root.querySelector('[data-briefing-delay-mode]');
  const initial = root.querySelector('[data-briefing-reports]');
  let reports = initial ? JSON.parse(initial.textContent) :
    {briefing:preview.textContent, briefing_detailed:preview.textContent};
  const renderReport = () => {
    const text = toggle?.checked ? reports.briefing_detailed : reports.briefing;
    if (preview.textContent !== text) {
      preview.textContent = text;
      root.querySelector('#copy-briefing-status').textContent = '';
    }
    if (mode) mode.textContent = toggle.checked ? 'ON' : 'OFF';
  };
  if (toggle) {
    toggle.checked = false;
    toggle.addEventListener('change', renderReport);
  }
  renderReport();
  const copyText = async text => {
    try { await navigator.clipboard.writeText(text); return; }
    catch (_) { /* Unsupported or denied clipboard API: try plain-text copy. */ }
    const active = document.activeElement;
    const selection = document.getSelection();
    const ranges = selection ? Array.from({length:selection.rangeCount}, (_, i) => selection.getRangeAt(i).cloneRange()) : [];
    const area = document.createElement('textarea');
    area.value = text; area.readOnly = true;
    area.style.position = 'fixed'; area.style.left = '-9999px';
    document.body.appendChild(area);
    try {
      area.select();
      if (!document.execCommand('copy')) throw new Error('Clipboard unavailable');
    } finally {
      area.remove(); active?.focus({preventScroll:true});
      if (selection) { selection.removeAllRanges(); ranges.forEach(range => selection.addRange(range)); }
    }
  };
  const workspace = root.closest('[data-neorain-reports]');
  workspace.addEventListener('neorain:reports-updated', event => {
    const {briefing, briefing_detailed} = event.detail;
    reports = {briefing: briefing ?? '', briefing_detailed: briefing_detailed ?? briefing ?? ''};
    renderReport();
  });
  root.querySelector('#copy-briefing').addEventListener('click', async () => {
    const status = root.querySelector('#copy-briefing-status');
    try {
      if (!await workspace.flushReportInputs()) { status.textContent = 'Inputs are not saved. Resolve the save error before copying.'; return; }
      await copyText(preview.textContent); status.textContent = 'Copied.';
    }
    catch (_) { status.textContent = 'Copy failed. Select the text to copy manually.'; }
  });
})();
