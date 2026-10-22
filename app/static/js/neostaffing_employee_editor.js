(() => {
  'use strict';
  const dialog = document.querySelector('[data-employee-editor]');
  if (!dialog) return;
  const content = dialog.querySelector('[data-employee-editor-content]');
  const confirmation = document.querySelector('[data-employee-delete-confirm]');
  let confirmedForm = null;
  const stateKey = 'neostaffing.employee-editor.return';
  let busy = false, opener = null;
  const error = message => {
    const node = content.querySelector('[data-employee-error]');
    if (node) { node.textContent = message; node.hidden = false; }
  };
  const close = () => { if (!busy) { dialog.close(); opener?.focus({preventScroll:true}); } };
  const remember = () => {
    const scroll = document.querySelector('[data-roster-scroll]');
    const first = [...document.querySelectorAll('[data-final-door-target]')].find(el => !el.hidden);
    sessionStorage.setItem(stateKey, JSON.stringify({url:location.pathname + location.search,
      y:window.scrollY, x:scroll?.scrollLeft || 0, page:Number(first?.dataset.rosterColumn || 0),
      search:document.querySelector('[data-roster-search]')?.value || ''}));
  };
  const reload = () => {
    remember(); dialog.close();
    // Keep the originating filters; a person_id deep link must not reopen a
    // successfully saved/deleted employee when the roster refreshes.
    const url = new URL(location.href); url.searchParams.delete('person_id');
    const saved = JSON.parse(sessionStorage.getItem(stateKey)); saved.url = url.pathname + url.search;
    sessionStorage.setItem(stateKey, JSON.stringify(saved));
    location.replace(saved.url);
  };
  const post = async (url, form, extra = {}) => {
    const body = new FormData(form);
    Object.entries(extra).forEach(([key,value]) => body.set(key,value));
    const controller = extra.confirm_delete === 'yes' ? new AbortController() : null;
    const timer = controller ? setTimeout(() => controller.abort(),30000) : null;
    const unknown = 'Deletion could not be confirmed. Check the roster or Employee Recovery before retrying; your entries remain here.';
    try {
      let response;
      try {
        response = await fetch(url, {method:'POST', body,signal:controller?.signal,
          headers:{'X-CSRFToken':document.querySelector('meta[name="csrf-token"]')?.content || ''}});
      } catch (cause) { if (controller) throw new Error(unknown); throw cause; }
      const payload = await response.json().catch(() => ({error:controller ? unknown : 'Unable to save. Please try again.'}));
      if (!response.ok || payload.ok !== true) throw new Error(payload.error || (controller ? unknown : 'Unable to save. Please try again.'));
      return payload;
    } finally { if (timer !== null) clearTimeout(timer); }
  };
  const open = async (id, origin = 'people') => {
    if (busy) return;
    opener = document.activeElement;
    const url = new URL(dialog.dataset.editorUrl, location.href);
    if (id) url.searchParams.set('person_id', id);
    else url.searchParams.set('origin', origin);
    try {
      const response = await fetch(url);
      if (!response.ok) throw new Error('Unable to open employee. Refresh and try again.');
      content.innerHTML = await response.text();
      if (!dialog.open) dialog.showModal();
      const form = content.querySelector('form');
      const start = form.querySelector('[data-employee-start]');
      const final = form.querySelector('[data-employee-final]');
      let manualFinal = !!final?.value && final.value !== start?.value;
      final?.addEventListener('change', () => { manualFinal = !!final.value; });
      const syncStart = () => {
        if (!start) return;
        const type = start.selectedOptions[0]?.dataset.areaType;
        if (type === 'Door' && !manualFinal) final.value = start.value;
        const initial = form.querySelector('[data-employee-initial-area]');
        if (initial) initial.value = start.value;
      };
      start?.addEventListener('change', syncStart); syncStart();
      const phone = form.elements.phone_number;
      phone.addEventListener('input', () => {
        const digits = phone.value.replace(/\D/g,'').slice(0,10);
        phone.value = digits.length > 6 ? `${digits.slice(0,3)}-${digits.slice(3,6)}-${digits.slice(6)}` :
          digits.length > 3 ? `${digits.slice(0,3)}-${digits.slice(3)}` : digits;
      });
      const date = form.elements.seniority_date;
      date.addEventListener('input', () => {
        const digits = date.value.replace(/\D/g,'').slice(0,8);
        date.value = digits.length > 4 ? `${digits.slice(0,2)}/${digits.slice(2,4)}/${digits.slice(4)}` :
          digits.length > 2 ? `${digits.slice(0,2)}/${digits.slice(2)}` : digits;
      });
      const mutate = async (url, extra = {}) => {
        if (busy) return;
        busy = true; form.setAttribute('aria-busy','true');
        form.querySelectorAll('button').forEach(button => { button.disabled = true; });
        try { await post(url, form, extra); reload(); }
        catch (cause) { error(cause.message); }
        finally { busy = false; form.removeAttribute('aria-busy'); form.querySelectorAll('button').forEach(button => { button.disabled = false; }); }
      };
      form.addEventListener('submit', event => { event.preventDefault(); syncStart(); void mutate(form.action); });
      content.querySelector('[data-employee-close]').addEventListener('click', close);
      form.querySelector('[data-employee-delete]')?.addEventListener('click', () => {
        if (busy || !confirmation) return;
        confirmedForm = form;
        confirmation.querySelector('[data-delete-employee-name]').textContent = form.dataset.deleteName;
        confirmation.querySelector('[data-delete-employee-id]').textContent = form.dataset.deleteId;
        confirmation.showModal();
      });
      form.employeeDelete = () => mutate(form.dataset.deleteUrl,
        {confirm_delete:'yes',confirmed_employee_id:form.dataset.deleteId});
    } catch (cause) { content.textContent = cause.message; if (!dialog.open) dialog.showModal(); }
  };
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  confirmation?.querySelector('[data-employee-delete-cancel]').addEventListener('click', () => {
    confirmation.close(); confirmedForm = null;
    content.querySelector('[data-employee-delete]')?.focus({preventScroll:true});
  });
  confirmation?.querySelector('[data-employee-delete-confirmed]').addEventListener('click', () => {
    if (busy || !confirmedForm || !content.contains(confirmedForm)) return;
    const form = confirmedForm; confirmedForm = null; confirmation.close();
    void form.employeeDelete();
  });
  confirmation?.addEventListener('cancel', () => { confirmedForm = null; });
  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const add = event.target.closest('[data-employee-add]');
    if (add) { event.preventDefault(); void open(null, add.dataset.employeeAdd); return; }
    const anchor = event.target.closest('a[href]');
    if (!anchor || !anchor.closest('.neostaffing-people-console,[data-shift-roster]')) return;
    const url = new URL(anchor.href, location.href);
    const id = url.searchParams.get('person_id');
    if (id && ['/neostaffing/people','/neostaffing/shift-flow'].includes(url.pathname)) {
      event.preventDefault(); void open(id);
    }
  });
  document.addEventListener('neostaffing:edit-employee', event => { void open(event.detail.personId); });
  try {
    const saved = JSON.parse(sessionStorage.getItem(stateKey) || 'null');
    if (saved?.url === location.pathname + location.search) {
      sessionStorage.removeItem(stateKey);
      const search = document.querySelector('[data-roster-search]');
      const next = document.querySelector('[data-roster-next]');
      for (let n = 0; next && n < 12; n++) {
        const first = [...document.querySelectorAll('[data-final-door-target]')].find(el => !el.hidden);
        if (Number(first?.dataset.rosterColumn || 0) >= saved.page || next.disabled) break;
        next.click();
      }
      if (search) { search.value = saved.search; search.dispatchEvent(new Event('input')); }
      requestAnimationFrame(() => { const scroll = document.querySelector('[data-roster-scroll]'); if (scroll) scroll.scrollLeft = saved.x; window.scrollTo(0,saved.y); });
    }
  } catch (_) { /* A stale return snapshot must not block editing. */ }
  if (dialog.dataset.initialPerson) void open(dialog.dataset.initialPerson);
})();
