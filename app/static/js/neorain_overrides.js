(() => {
  'use strict';
  const queues = new Map();
  const clockDigits = value => /^[0-9]{2}:[0-9]{2}$/.test(value) ? value.replace(':', '') : value;
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
            if (input) input.value = String(payload.value || '');
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
          if (input) input.value = String(payload.value || '');
          delete root.dataset.rainOverrideDirty;
          note(root, action === 'reset' ? 'Reset saved.' : 'Saved.');
        }
      } catch (error) {
        if (sequence !== root._rainSequence) return;
        note(root, error.message, true);
        const checkbox = root.querySelector('[data-rain-override-checkbox]');
        if (checkbox) { checkbox.checked = checkbox.dataset.saved === 'true'; delete root.dataset.rainOverrideDirty; }
        // Invalid/stale time remains an explicit unsaved draft, never falsely saved.
      }
    };
    const next = (queues.get(id) || Promise.resolve()).then(work, work);
    queues.set(id, next);
  };
  // One reset action for the selected field; no controls repeated in each row.
  let selected = null;
  const tools = document.querySelector('[data-rain-reset-tools]');
  const choose = root => {
    selected = root;
    if (!tools) return;
    const button = tools.querySelector('[data-rain-reset-selected]');
    button.disabled = !root;
    const label = root ? root.getAttribute('aria-label') : '';
    tools.querySelector('[data-rain-reset-selection]').textContent = label;
    button.title = root ? 'Reset ' + label + ' to Google' : 'Select a time or checkbox to reset it to Google';
  };
  document.addEventListener('focusin', event => {
    if (event.target.matches('[data-rain-override-time], [data-rain-override-checkbox]') && !event.target.disabled) {
      choose(event.target.closest('[data-rain-override-mission]'));
      // Replace a saved clock with four numeric keystrokes, while partial drafts
      // retain their selection/caret and can still be edited in place.
      if (event.target.matches('[data-rain-override-time]') && event.target.value.includes(':') && validClock(clockDigits(event.target.value))) {
        event.target.select();
        event.target._rainReplaceClock = true;
      }
    } else if (!event.target.closest('[data-rain-reset-tools]')) choose(null);
  });
  document.addEventListener('click', event => {
    if (!event.target.closest('[data-rain-reset-selected]') || !selected || !selected.isConnected) return;
    submit(selected, null, 'reset');
  });
  document.addEventListener('keydown', event => {
    // WebKit can restore the tap caret after focus. Select before its first
    // numeric insertion/maxlength check, while navigation/deletion keeps caret editing.
    if (!event.target.matches('[data-rain-override-time]') || !event.target._rainReplaceClock) return;
    if (/^[0-9]$/.test(event.key)) event.target.select();
    if (!['Shift', 'Control', 'Alt', 'Meta'].includes(event.key)) delete event.target._rainReplaceClock;
  });
  document.addEventListener('input', event => {
    if (!event.target.matches('[data-rain-override-time]')) return;
    delete event.target._rainReplaceClock;
    const root = event.target.closest('[data-rain-override-mission]');
    root.dataset.rainOverrideDirty = 'true';
    // Every keystroke supersedes older responses, including blank/partial drafts.
    root._rainSequence = (root._rainSequence || 0) + 1;
    const raw = event.target.value;
    const digits = clockDigits(raw);
    if (validClock(digits)) submit(root, digits);
    else note(root, raw.length >= 4 ? 'Invalid time.' : 'Enter four digits.', raw.length >= 4);
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
