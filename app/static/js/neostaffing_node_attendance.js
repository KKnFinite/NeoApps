/* One signed row per save. Failed/stale writes are never replayed. */
(() => {
  'use strict';
  const form = document.querySelector('[data-attendance-autosave]');
  if (!form) return;
  const notice = form.querySelector('[data-attendance-save-status]');
  const saved = new Map();
  for (const select of form.querySelectorAll('select[name^="status_"]')) saved.set(select, select.value);
  form.addEventListener('submit', event => event.preventDefault());
  form.addEventListener('change', async event => {
    const select = event.target;
    if (!saved.has(select) || select.disabled) return;
    const id = select.name.slice('status_'.length);
    const original = form.querySelector(`[name="original_${id}"]`);
    const data = new FormData();
    data.set('sort_date_operation_id', form.querySelector('[name="sort_date_operation_id"]').value);
    data.set(select.name, select.value);
    data.set(original.name, original.value);
    for (const control of saved.keys()) control.disabled = true;
    notice.textContent = 'Saving…';
    try {
      const token = document.querySelector('meta[name="csrf-token"]')?.content;
      const response = await fetch(form.action || window.location.href, {method:'POST', body:data,
        headers:{Accept:'application/json', ...(token ? {'X-CSRFToken':token} : {})}});
      const payload = await response.json();
      if (!response.ok || !payload.ok || !payload.rows?.[id]) throw new Error(payload.error || 'Attendance was not saved. Reload before trying again.');
      select.value = payload.rows[id].status;
      original.value = payload.rows[id].original;
      saved.set(select, select.value);
      for (const counter of document.querySelectorAll('[data-attendance-count]')) {
        if (!payload.counts) break;
        const value = payload.counts[counter.dataset.attendanceCount] || 0;
        counter.textContent = value;
        const item = counter.closest('[data-attendance-summary-item]');
        if (item) item.hidden = !value;
      }
      notice.textContent = 'Saved.';
    } catch (error) {
      select.value = saved.get(select);
      notice.textContent = `${error.message || 'Attendance was not saved.'} Reload to verify current attendance.`;
    } finally {
      for (const control of saved.keys()) control.disabled = false;
    }
  });
})();
