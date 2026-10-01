/* Reports reuses GET filters and URL state, but never relocates its scope rail. */
(() => {
  'use strict';
  const scopeKeys = ['sort','operation','department','work_area'];
  const mobile = window.matchMedia('(max-width: 900px)');
  for (const form of document.querySelectorAll('form[data-report-auto],form[data-report-download]')) {
    const fields = scopeKeys.map(key => form.querySelector(`[data-report-scope="${key}"]`));
    const panel = form.querySelector('[data-report-filter-panel]');
    const toggle = form.querySelector('[data-report-filter-toggle]');
    const more = form.querySelector('[data-report-more]');
    const syncOptions = () => fields.forEach((field, index) => {
      if (!field) return;
      for (const option of field.options) {
        const allowed = fields.slice(0,index).every((parent, i) => !parent?.value || option.dataset[scopeKeys[i]] === parent.value);
        option.hidden = option.disabled = Boolean(option.value) && !allowed;
      }
    });
    syncOptions();
    form.addEventListener('change', event => {
      const index = fields.indexOf(event.target);
      if (index >= 0) {
        for (const child of fields.slice(index + 1)) if (child) child.value = '';
        syncOptions();
      }
      if (form.hasAttribute('data-report-auto') && form.reportValidity()) form.requestSubmit();
    });
    if (!panel || !toggle) continue;
    const close = () => { panel.classList.remove('is-open'); toggle.setAttribute('aria-expanded', 'false'); toggle.focus(); };
    toggle.addEventListener('click', () => {
      panel.classList.add('is-open'); toggle.setAttribute('aria-expanded', 'true');
      panel.querySelector('select,input,button')?.focus();
    });
    form.querySelector('[data-report-filter-close]').addEventListener('click', close);
    form.addEventListener('keydown', event => {
      if (event.key === 'Escape') { if (mobile.matches) close(); else if (more) more.open = false; }
      if (event.key === 'Tab' && mobile.matches && panel.classList.contains('is-open')) {
        const focusable = [...panel.querySelectorAll('button,select,input,summary')].filter(field => field.getClientRects().length);
        const first = focusable[0], last = focusable[focusable.length - 1];
        if (event.shiftKey && event.target === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && event.target === last) { event.preventDefault(); first.focus(); }
      }
    });
    const resize = () => { if (more) more.open = mobile.matches; panel.classList.remove('is-open'); toggle.setAttribute('aria-expanded','false'); };
    resize(); mobile.addEventListener('change', resize);
  }
})();
