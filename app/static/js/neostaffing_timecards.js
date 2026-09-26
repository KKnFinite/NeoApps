(() => {
  'use strict';
  const root = document.querySelector('[data-timecards]');
  if (!root) return;
  const dirty = new Set();
  const status = root.querySelector('[data-timecard-status]');
  let saving = false;
  const mark = target => { const row = target.closest('[data-timecard-id]'); if (row) dirty.add(row); };
  root.addEventListener('input', event => mark(event.target));
  root.addEventListener('click', async event => {
    if (event.target.closest('[data-bulk-times]')) {
      const clock = input => {
        let value = input.value.trim();
        if (/^\d{4}$/.test(value)) value = `${value.slice(0, 2)}:${value.slice(2)}`;
        return !value || /^([01]\d|2[0-3]):[0-5]\d$/.test(value) ? value : null;
      };
      const startInput = root.querySelector('[data-bulk-start]');
      const endInput = root.querySelector('[data-bulk-end]');
      const start = clock(startInput), end = clock(endInput);
      if (start === null || end === null) { status.textContent = 'Use HHMM (0000–2359).'; return; }
      startInput.value = start; endInput.value = end;
      for (const row of root.querySelectorAll('[data-timecard-id]')) {
        const host = row.querySelector('[data-time-segments]');
        if (!host || host.children.length) continue;
        row.querySelector('[data-add-segment]').click();
        host.querySelector('[data-start]').value = start;
        host.querySelector('[data-end]').value = end;
        dirty.add(row);
      }
    }
    const add = event.target.closest('[data-add-segment]');
    if (add) {
      const row = add.closest('[data-timecard-id]');
      const segment = document.createElement('div');
      segment.className = 'timecard-segment';
      segment.innerHTML = '<label>Start <input data-start placeholder="YYYY-MM-DDTHH:MM"></label><label>End <input data-end placeholder="YYYY-MM-DDTHH:MM"></label><button type="button" data-remove-segment>REMOVE</button>';
      if (root.dataset.nodeWorkspace) segment.querySelectorAll('input').forEach(input => {
        input.type = 'time'; input.step = '1'; input.removeAttribute('placeholder');
      });
      row.querySelector('[data-time-segments]').append(segment); mark(add);
    }
    const remove = event.target.closest('[data-remove-segment]');
    if (remove) { mark(remove); remove.closest('.timecard-segment').remove(); }
    if (!event.target.closest('[data-save-times]') || saving || !dirty.size) return;
    const timestamp = input => {
      const original = input.dataset.original;
      return original && input.value && input.value === original.slice(11, 11 + input.value.length) ? original : input.value;
    };
    const commands = [...dirty].map(row => ({id: Number(row.dataset.timecardId), version: Number(row.dataset.version),
      segments: [...row.querySelectorAll('.timecard-segment')].map(part => ({start:timestamp(part.querySelector('[data-start]')), end:timestamp(part.querySelector('[data-end]'))}))}));
    saving = true;
    // Prevent editing a submitted draft while the server checks its version.
    const controls = [...root.querySelectorAll('button,input')];
    controls.forEach(control => { control.disabled = true; });
    try {
      const token = document.querySelector('meta[name="csrf-token"]')?.content;
      const response = await fetch('/neostaffing/timecards/save', {method:'POST', headers:{'Content-Type':'application/json', ...(token ? {'X-CSRFToken':token} : {})}, body:JSON.stringify({commands, node_workspace:root.dataset.nodeWorkspace || null, node_area:root.dataset.nodeArea || null})});
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'Save rejected. Reload before retrying.');
      window.location.reload();
    } catch (error) { status.textContent = error.message; }
    finally { saving = false; controls.forEach(control => { control.disabled = false; }); }
  });
})();
