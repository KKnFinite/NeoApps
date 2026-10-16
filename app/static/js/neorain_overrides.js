(() => {
  'use strict';
  const queues = new Map();
  const validClock = value => /^[0-9]{4}$/.test(value) && +value.slice(0, 2) < 24 && +value.slice(2) < 60;
  const roots = id => [...document.querySelectorAll('[data-rain-override-mission]')].filter(el => el.dataset.rainOverrideMission === id);
  const note = (root, text, error = false) => {
    root.querySelector('[data-rain-override-message]').textContent = text;
    root.classList.toggle('is-error', error);
  };
  const submit = (root, value, action = 'edit', confirmed = false) => {
    const id = root.dataset.rainOverrideMission, field = root.dataset.rainOverrideField;
    const sequence = root._rainSequence = (root._rainSequence || 0) + 1;
    root.dataset.rainOverrideDirty = 'true';
    note(root, 'SAVING');
    const work = async () => {
      if (sequence !== root._rainSequence) return;
      try {
        const response = await fetch('/neorain/override', {
          method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({mission_id: +id, field, value, action, confirmed,
            expected_version: root.dataset.rainOverrideVersion})
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.error || 'Save failed. Reload before retrying.');
        for (const other of roots(id)) {
          other.dataset.rainOverrideVersion = payload.version;
          if (other.dataset.rainOverrideField !== field) continue;
          const readout = other.querySelector('[data-rain-override-readout]');
          if (readout) readout.textContent = payload.value || '\u2014';
          const checkbox = other.querySelector('[data-rain-override-checkbox]');
          if (checkbox) { checkbox.checked = payload.value === true; checkbox.dataset.saved = String(checkbox.checked); }
          other.dataset.rainOverrideAuthority = payload.source;
          other.dataset.rainOverrideDetail = payload.sync_detail || '';
          if (other !== root && other.dataset.rainOverrideDirty !== 'true') {
            const input = other.querySelector('[data-rain-override-time]');
            if (input) input.value = String(payload.value || '').replace(':', '');
          }
        }
        document.querySelectorAll('[data-neorain-mission-id]').forEach(row => {
          if (row.dataset.neorainMissionId !== id) return;
          row.dataset.missionVersion = payload.version;
          const variance = row.querySelector('[data-neorain-display="arrival_variance"], [data-neorain-display="departure_variance"]');
          if (variance) variance.textContent = payload.row.arrival_variance || payload.row.departure_variance || '-';
        });
        if (sequence === root._rainSequence) {
          const input = root.querySelector('[data-rain-override-time]');
          if (input) input.value = String(payload.value || '').replace(':', '');
          delete root.dataset.rainOverrideDirty;
          note(root, action === 'reset' ? 'Reset saved.' : 'Saved.');
        }
      } catch (error) {
        note(root, error.message, true);
        const checkbox = root.querySelector('[data-rain-override-checkbox]');
        if (checkbox) { checkbox.checked = checkbox.dataset.saved === 'true'; delete root.dataset.rainOverrideDirty; }
        // Invalid/stale time remains an explicit unsaved draft, never falsely saved.
      }
    };
    const next = (queues.get(id) || Promise.resolve()).then(work, work);
    queues.set(id, next);
  };
  document.addEventListener('click', event => {
    const root = event.target.closest('[data-rain-override-mission]');
    if (!root) return;
    if (event.target.closest('[data-rain-override-open]')) {
      const editor = root.querySelector('[data-rain-override-editor]');
      editor.hidden = !editor.hidden;
      root.querySelector('[data-rain-override-open]').setAttribute('aria-expanded', String(!editor.hidden));
      if (!editor.hidden) {
        const input = editor.querySelector('input');
        const focus = input || editor.querySelector('button');
        focus.focus();
        if (input) input.select();
      }
    }
    if (event.target.closest('[data-rain-override-reset]')) submit(root, null, 'reset');
  });
  document.addEventListener('input', event => {
    if (!event.target.matches('[data-rain-override-time]')) return;
    const root = event.target.closest('[data-rain-override-mission]');
    root.dataset.rainOverrideDirty = 'true';
    const raw = event.target.value;
    if (validClock(raw)) submit(root, raw);
    else note(root, raw.length === 4 ? 'Invalid time.' : 'Enter four digits.', raw.length === 4);
  });
  document.addEventListener('change', event => {
    if (!event.target.matches('[data-rain-override-checkbox]')) return;
    const root = event.target.closest('[data-rain-override-mission]'), input = event.target;
    let confirmed = false;
    if (root.dataset.rainOverrideField === 'no_return' && !input.checked && input.dataset.saved === 'true') {
      confirmed = window.confirm('Correct No Return? Recorded Block-Out and completed milestones will not be erased.');
      if (!confirmed) { input.checked = true; return; }
    }
    submit(root, input.checked, 'edit', confirmed);
  });
})();
