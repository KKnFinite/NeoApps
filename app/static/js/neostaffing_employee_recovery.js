(() => {
  'use strict';
  document.querySelectorAll('[data-employee-restore]').forEach(form => {
    let saving = false;
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (saving) return;
      saving = true;
      const button = form.querySelector('button');
      const status = form.querySelector('[data-restore-status]');
      button.disabled = true; status.setAttribute('role','status'); status.textContent = 'Restoring…';
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(),30000);
      try {
        const response = await fetch(form.action,{method:'POST',body:new FormData(form),signal:controller.signal,headers:{Accept:'application/json'}});
        const result = await response.json().catch(() => ({}));
        if (!response.ok || result.ok !== true) throw new Error(result.error || 'Restore could not be confirmed. Refresh recovery status before retrying.');
        status.textContent = 'Employee restored.';
        button.hidden = true;
      } catch (error) {
        status.setAttribute('role','alert'); status.textContent = error.name === 'AbortError' || error.name === 'TypeError'
          ? 'Restore could not be confirmed. Refresh recovery status before retrying.' : error.message || 'Connection failed. Refresh before retrying.';
      } finally { clearTimeout(timer); saving = false; button.disabled = false; }
    });
  });
})();
