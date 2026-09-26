/* Node presentation only: canonical Staffing writes and optimistic versions. */
(() => {
  'use strict';
  const root = document.querySelector('[data-timecards][data-node-workspace]');
  if (!root) return;
  let pending = 0;
  const dirty = new Set();
  window.addEventListener('beforeunload', event => {
    if (pending || dirty.size) { event.preventDefault(); event.returnValue = ''; }
  });
  function clock(value) {
    let text = value.trim();
    if (/^\d{3,4}$/.test(text)) { text = text.padStart(4, '0'); text = `${text.slice(0, 2)}:${text.slice(2)}`; }
    const match = /^(\d{2}):(\d{2})$/.exec(text);
    return match && +match[1] < 24 && +match[2] < 60 ? text : null;
  }
  for (const row of root.querySelectorAll('[data-timecard-id]')) {
    const container = row.querySelector('[data-time-segments]');
    if (!container) continue;
    const status = row.querySelector('[data-row-status]');
    let busy = false, blocked = false, timer;
    function notice(message, reload = false, label = ' Reload') {
      status.textContent = message;
      if (reload) {
        const link = document.createElement('a'); link.href = window.location.href; link.textContent = label;
        status.append(link);
      }
    }
    function controls(disabled) { row.querySelectorAll('input, button').forEach(control => { control.disabled = disabled; }); }
    function segments() {
      const result = [];
      for (const part of container.querySelectorAll('.timecard-segment')) {
        const inputs = [part.querySelector('[data-start]'), part.querySelector('[data-end]')];
        if (inputs.every(input => !input.value.trim())) continue;
        const values = inputs.map(input => clock(input.value));
        if (values.some(value => !value)) return null;
        const unchanged = inputs.map((input, index) => input.dataset.original && input.dataset.original.slice(11, 16) === values[index]);
        inputs.forEach((input, index) => { input.value = values[index]; });
        // Retain an unchanged start date/offset, including next-day combo segments.
        // A changed pair derives its end from that start through the shared service.
        result.push({start: unchanged[0] ? inputs[0].dataset.original : values[0],
          end: unchanged[0] && unchanged[1] ? inputs[1].dataset.original : values[1]});
      }
      return result;
    }
    async function save() {
      clearTimeout(timer);
      if (busy || blocked) return;
      const parts = segments();
      if (!parts) { notice('Enter valid Start and End times (HHMM).'); return; }
      busy = true; pending++; controls(true); notice('Saving…');
      try {
        const response = await fetch('/neostaffing/timecards/save', {method:'POST', headers:{
          'Content-Type':'application/json', 'Accept':'application/json',
          'X-CSRFToken':document.querySelector('meta[name="csrf-token"]')?.content || ''
        }, body:JSON.stringify({node_workspace:root.dataset.nodeWorkspace, node_area:root.dataset.nodeArea || null,
          commands:[{id:Number(row.dataset.timecardId), version:Number(row.dataset.version), segments:parts}]})});
        const payload = await response.json();
        const saved = payload.rows?.find(item => item.id === Number(row.dataset.timecardId));
        if (!response.ok || !payload.saved || !saved) throw new Error(payload.error || 'Save failed. Reload before trying again.');
        row.dataset.version = String(saved.version);
        let index = 0;
        container.querySelectorAll('.timecard-segment').forEach(part => {
          const start = part.querySelector('[data-start]'), end = part.querySelector('[data-end]');
          if (!start.value.trim() && !end.value.trim()) return;
          const value = saved.segments[index++];
          for (const [input, key] of [[start, 'start'], [end, 'end']]) { input.dataset.original = value[key]; input.value = value[key].slice(11, 16); }
        });
        const hours = row.querySelector('[data-row-hours]'); if (hours) hours.textContent = `${saved.hours} hrs`;
        row.querySelectorAll('.timecard-exception').forEach(item => { item.hidden = true; });
        dirty.delete(row); notice('Saved', true, ' Refresh checks');
      } catch (error) {
        blocked = true; notice(error.message || 'Save failed. Reload before trying again.', true);
      } finally { busy = false; pending--; controls(blocked); }
    }
    row.addEventListener('input', () => {
      dirty.add(row); notice('Not saved'); clearTimeout(timer);
      timer = setTimeout(save, 400);
    });
    row.addEventListener('change', event => { if (event.target.matches('[data-start], [data-end]')) { dirty.add(row); save(); } });
    row.addEventListener('keydown', event => { if (event.key === 'Enter' && event.target.matches('input')) { event.preventDefault(); event.target.blur(); } });
    row.addEventListener('click', event => {
      if (busy || blocked) return;
      if (event.target.closest('[data-add-segment]')) {
        const part = document.createElement('div'); part.className = 'timecard-segment';
        // Constant markup only; employee/server values never enter innerHTML.
        part.innerHTML = '<label>Start <input data-start type="text" inputmode="numeric" maxlength="5" placeholder="HHMM" autocomplete="off"></label><label>End <input data-end type="text" inputmode="numeric" maxlength="5" placeholder="HHMM" autocomplete="off"></label><button type="button" data-remove-segment>REMOVE</button>';
        container.append(part); part.querySelector('input').focus();
      }
      if (event.target.closest('[data-remove-segment]')) {
        const part = event.target.closest('.timecard-segment');
        const retained = !!part.querySelector('[data-start]').dataset.original;
        part.remove();
        if (retained || dirty.has(row)) { dirty.add(row); save(); }
      }
    });
  }
})();
