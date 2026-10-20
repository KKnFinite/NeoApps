(() => {
  'use strict';
  const root = document.querySelector('[data-daily-briefing]');
  if (!root) return;
  const preview = root.querySelector('#briefing-text');
  let flushPending = async () => true;
  root.querySelector('#copy-briefing').addEventListener('click', async () => {
    const status = root.querySelector('#copy-briefing-status');
    try {
      if (!await flushPending()) { status.textContent = 'Inputs are not saved. Resolve the save error before copying.'; return; }
      await navigator.clipboard.writeText(preview.textContent); status.textContent = 'Copied.';
    }
    catch (_) { status.textContent = 'Copy failed. Select the text to copy manually.'; }
  });
  const form = root.querySelector('[data-briefing-inputs]');
  if (!form) return;
  const inputs = [...form.querySelectorAll('[data-briefing-field]')];
  const fitNote = input => {
    if (input.tagName !== 'TEXTAREA' || !input.hasAttribute('data-auto-grow')) return;
    input.style.height = 'auto';
    input.style.height = `${input.scrollHeight + 2}px`;
  };
  inputs.forEach(fitNote);
  const status = form.querySelector('[data-briefing-status]');
  const canEdit = form.dataset.canEdit === 'true';
  const dirty = input => input.value !== input.dataset.savedValue;
  const versions = () => Object.fromEntries(inputs.map(input => [input.name, +input.dataset.version]));
  const syncVersions = () => { form.querySelector('[data-briefing-versions]').value = JSON.stringify(versions()); };
  let saving = false, polling = false, generation = 0;
  const pending = new Set(), timers = new Map(), errors = new Map();
  let activeSave = null;
  const apply = (payload, sent = null) => {
    preview.textContent = payload.briefing;
    root.querySelector('#copy-briefing-status').textContent = '';
    for (const input of inputs) {
      const field = payload.fields[input.name];
      if (!field) continue;
      const wasSent = sent && Object.hasOwn(sent, input.name);
      // Polls never replace unfinished drafts or silently advance their base
      // version. A save may acknowledge an older value while typing continues.
      if (!wasSent && dirty(input)) continue;
      const unchanged = wasSent ? input.value === sent[input.name] : true;
      const value = field.editable ? field.manual_value : field.value;
      input.dataset.savedValue = value ?? '';
      input.dataset.version = field.version;
      if (unchanged) input.value = value ?? '';
      fitNote(input);
      input.disabled = !canEdit || !field.editable;
    }
    syncVersions();
  };
  const showStatus = () => {
    status.textContent = errors.size ? [...errors.values()][0] :
      saving ? 'Saving…' : inputs.some(dirty) ? 'Unsaved changes.' : 'Saved.';
  };
  const drain = () => {
    if (activeSave) return activeSave;
    activeSave = (async () => {
      saving = true; generation++;
      while (pending.size) {
        const changed = [...pending].filter(input => !input.disabled && dirty(input));
        pending.clear();
        if (!changed.length) continue;
        const sent = Object.fromEntries(changed.map(input => [input.name, input.value]));
        const expected = Object.fromEntries(changed.map(input => [input.name, +input.dataset.version]));
        showStatus();
        try {
          const response = await fetch(form.action, {method:'POST', credentials:'same-origin', keepalive:true,
            headers:{'Content-Type':'application/json'}, body:JSON.stringify({values:sent, versions:expected})});
          const payload = await response.json().catch(() => ({}));
          if (!response.ok || !payload.ok) throw new Error(payload.error || 'Unable to save briefing inputs.');
          apply(payload, sent);
          changed.forEach(input => {
            errors.delete(input);
            // An edit may have returned to the old saved value while this
            // request was in flight. Compare against the newly acknowledged
            // value so that intentional revert is saved too.
            if (dirty(input) && !timers.has(input)) pending.add(input);
          });
        } catch (error) {
          // Keep the original version and draft on failure. Do not repeatedly
          // retry a conflict or let a failed field block unrelated edits.
          changed.forEach(input => { errors.set(input, error.message || 'Unable to save. Your edits are still here.'); pending.delete(input); });
        }
      }
      saving = false; generation++; activeSave = null; showStatus();
    })();
    return activeSave;
  };
  const cancelTimer = input => { clearTimeout(timers.get(input)); timers.delete(input); };
  const enqueue = input => {
    cancelTimer(input);
    if (!canEdit || input.disabled || !dirty(input)) return activeSave;
    pending.add(input);
    return drain();
  };
  form.addEventListener('input', event => {
    const input = event.target;
    if (!inputs.includes(input) || input.disabled || !canEdit) return;
    fitNote(input); errors.delete(input); cancelTimer(input);
    if (dirty(input)) timers.set(input, setTimeout(() => enqueue(input), 650));
    showStatus();
  });
  form.addEventListener('focusout', event => { if (inputs.includes(event.target)) return enqueue(event.target); });
  flushPending = async () => {
    for (const input of inputs) {
      cancelTimer(input);
      if (canEdit && !input.disabled && dirty(input) && !errors.has(input)) pending.add(input);
    }
    if (pending.size || activeSave) await drain();
    return !inputs.some(input => !input.disabled && dirty(input));
  };
  form.addEventListener('submit', event => { event.preventDefault(); return flushPending(); });
  const refresh = async () => {
    if (document.hidden || saving || polling) return;
    const before = generation; polling = true;
    try {
      const response = await fetch(root.dataset.stateUrl, {credentials:'same-origin', cache:'no-store'});
      const payload = await response.json();
      if (response.ok && payload.ok && before === generation && !saving) apply(payload);
    } catch (_) { /* Retain the last confirmed report and all local drafts. */ }
    finally { polling = false; }
  };
  const interval = +root.dataset.refreshMs;
  if (root.dataset.stateUrl && interval > 0) setInterval(refresh, Math.max(1000, interval));
})();
