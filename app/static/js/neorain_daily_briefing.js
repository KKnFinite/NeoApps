(() => {
  'use strict';
  const root = document.querySelector('[data-daily-briefing]');
  if (!root) return;
  const preview = root.querySelector('#briefing-text');
  root.querySelector('#copy-briefing').addEventListener('click', async () => {
    const status = root.querySelector('#copy-briefing-status');
    try { await navigator.clipboard.writeText(preview.textContent); status.textContent = 'Copied.'; }
    catch (_) { status.textContent = 'Copy failed. Select the text to copy manually.'; }
  });
  const form = root.querySelector('[data-briefing-inputs]');
  if (!form) return;
  const inputs = [...form.querySelectorAll('[data-briefing-field]')];
  const status = form.querySelector('[data-briefing-status]');
  const button = form.querySelector('[data-briefing-save]');
  const dirty = input => input.value !== input.dataset.savedValue;
  const versions = () => Object.fromEntries(inputs.map(input => [input.name, +input.dataset.version]));
  const syncVersions = () => { form.querySelector('[data-briefing-versions]').value = JSON.stringify(versions()); };
  let saving = false, polling = false, generation = 0;
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
      input.disabled = !button || !field.editable;
    }
    syncVersions();
  };
  form.addEventListener('input', () => {
    if (status && !saving) status.textContent = inputs.some(dirty) ? 'Unsaved changes.' : '';
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (saving || !button) return;
    const changed = inputs.filter(input => !input.disabled && dirty(input));
    if (!changed.length) { status.textContent = 'No changes to save.'; return; }
    const sent = Object.fromEntries(changed.map(input => [input.name, input.value]));
    const expected = Object.fromEntries(changed.map(input => [input.name, +input.dataset.version]));
    saving = true; generation++; button.disabled = true; status.textContent = 'Saving…';
    try {
      const response = await fetch(form.action, {method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({values:sent, versions:expected})});
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.ok) throw new Error(payload.error || 'Unable to save briefing inputs.');
      apply(payload, sent);
      status.textContent = inputs.some(dirty) ? 'Saved. New edits remain unsaved.' : 'Saved.';
    } catch (error) { status.textContent = error.message || 'Unable to save. Your edits are still here.'; }
    finally { saving = false; generation++; button.disabled = false; }
  });
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
