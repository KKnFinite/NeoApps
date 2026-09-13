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
      const start = root.querySelector('[data-bulk-start]').value;
      const end = root.querySelector('[data-bulk-end]').value;
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
      row.querySelector('[data-time-segments]').append(segment); mark(add);
    }
    const remove = event.target.closest('[data-remove-segment]');
    if (remove) { mark(remove); remove.closest('.timecard-segment').remove(); }
    if (!event.target.closest('[data-save-times]') || saving || !dirty.size) return;
    const commands = [...dirty].map(row => ({id: Number(row.dataset.timecardId), version: Number(row.dataset.version),
      segments: [...row.querySelectorAll('.timecard-segment')].map(part => ({start:part.querySelector('[data-start]').value, end:part.querySelector('[data-end]').value}))}));
    saving = true;
    // Prevent editing a submitted draft while the server checks its version.
    const controls = [...root.querySelectorAll('button,input')];
    controls.forEach(control => { control.disabled = true; });
    try {
      const token = document.querySelector('meta[name="csrf-token"]')?.content;
      const response = await fetch('/neostaffing/timecards/save', {method:'POST', headers:{'Content-Type':'application/json', ...(token ? {'X-CSRFToken':token} : {})}, body:JSON.stringify({commands})});
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'Save rejected. Reload before retrying.');
      window.location.reload();
    } catch (error) { status.textContent = error.message; }
    finally { saving = false; controls.forEach(control => { control.disabled = false; }); }
  });
})();
