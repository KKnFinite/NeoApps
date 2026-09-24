/* Shared progressive enhancement for explicitly opted-in Staffing GET controls.
 * Move live elements, never clone forms or change mutation handlers/authority. */
(() => {
  'use strict';
  const bar = document.querySelector('[data-staffing-secondary]');
  if (!bar) return;
  const panel = bar.querySelector('[data-staffing-filter-panel]');
  const controls = bar.querySelector('[data-staffing-filter-controls]');
  const summary = bar.querySelector('[data-staffing-filter-summary]');
  const forms = [...document.querySelectorAll('form[data-staffing-filter]')];
  const scopes = [...document.querySelectorAll('[data-staffing-scope]')];
  for (const form of forms) {
    if (form.method.toLowerCase() !== 'get') continue;
    controls.append(form);
    for (const button of form.querySelectorAll('button[type="submit"], button:not([type])')) button.hidden = true;
    let timer;
    const apply = () => {
      clearTimeout(timer);
      if (!form.reportValidity()) return;
      for (const field of form.querySelectorAll('[name="page"], [name="person_id"]')) field.disabled = true;
      form.requestSubmit();
    };
    form.addEventListener('change', apply);
    form.addEventListener('input', event => {
      if (event.isComposing || !event.target.matches('input[type="search"], input:not([type]), input[type="text"]')) return;
      clearTimeout(timer); timer = setTimeout(apply, 450);
    });
    form.addEventListener('submit', () => clearTimeout(timer));
  }
  for (const scope of scopes) {
    const disclosure = document.createElement('details'), label = document.createElement('summary');
    label.textContent = 'Organization scope'; disclosure.append(label, scope); controls.append(disclosure);
  }
  for (const source of document.querySelectorAll('[data-staffing-secondary-source]')) bar.querySelector('[data-staffing-secondary-nav]').append(source);
  for (const source of document.querySelectorAll('[data-staffing-configure]')) bar.querySelector('[data-staffing-secondary-actions]').append(source);
  const labels = [];
  for (const form of forms) for (const field of form.querySelectorAll('select, input:not([type="hidden"])')) {
    const value = field.tagName === 'SELECT' ? field.selectedOptions[0]?.textContent.trim() : field.value;
    const name = field.getAttribute('aria-label') || field.labels?.[0]?.querySelector('span')?.textContent.trim() || field.labels?.[0]?.firstChild?.textContent.trim() || field.name.replace(/_id$/, '').replaceAll('_',' ');
    if (!field.labels?.length && !field.getAttribute('aria-label')) field.setAttribute('aria-label', name);
    if (!field.value || field.disabled) continue;
    if (value) labels.push(`${name}: ${value}`);
  }
  for (const scope of scopes) {
    const selected = scope.querySelector('.is-selected'); if (selected) labels.push([...selected.children].map(child => child.textContent.trim()).join(' ') || selected.textContent.trim());
  }
  for (const label of [...new Set(labels)]) { const chip = document.createElement('span'); chip.className = 'staffing-filter-chip'; chip.textContent = label; summary.append(chip); }
  if (!labels.length) summary.textContent = forms.length || scopes.length ? 'Current scope' : '';
  panel.hidden = !forms.length && !scopes.length;
  const mobile = window.matchMedia('(max-width: 900px)');
  const resize = () => { panel.open = !mobile.matches; }; resize(); mobile.addEventListener('change', resize);
  panel.addEventListener('keydown', event => { if (event.key === 'Escape' && mobile.matches) { panel.open = false; panel.querySelector('summary').focus(); } });
  document.body.classList.add('staffing-controls-ready');

  // Per-tab URL state only. No automatic redirects, cross-user storage, or DB writes.
  // Org-unit IDs mean the same thing on People and Attendance; other filters stay page-local.
  const scopeKeys = ['sort_id','operation_id','department_id','work_area_id','work_area_ids'];
  const allowed = {
    '/neostaffing/people': [...scopeKeys,'search','classification','employee_status','active','assignment_status'],
    '/neostaffing/attendance': scopeKeys,
    '/neostaffing/timecards': ['date','period','view','search'],
    '/neostaffing/employee-records': ['search'],
    '/neostaffing/org-chart': ['view','unit_id'],
    '/neostaffing/shift-flow': ['phase','side'],
    '/neostaffing/vacation-selection': ['year'],
    '/neostaffing/vacation-selection/management': ['year','area_id'],
    '/neostaffing/vacation-selection/union': ['year'],
    '/neostaffing/reports': [...scopeKeys,'report_type','classification','employee_status','assignment_status','attendance_date','attendance_status','year']
  };
  const current = new URL(window.location.href), key = 'neostaffing.filters.v1.' + bar.dataset.filterUser;
  try {
    const stored = JSON.parse(sessionStorage.getItem(key) || '{}');
    const pick = (url, names) => [...url.searchParams].filter(([name]) => names.includes(name));
    if (allowed[current.pathname]) stored[current.pathname] = pick(current, allowed[current.pathname]);
    sessionStorage.setItem(key, JSON.stringify(stored));
    const shared = ['/neostaffing/people','/neostaffing/attendance'].includes(current.pathname) ? pick(current, scopeKeys) : [];
    for (const link of document.querySelectorAll('a[data-staffing-nav]')) {
      const target = new URL(link.href, current);
      if (target.origin !== current.origin || !allowed[target.pathname] || target.search) continue;
      for (const [name,value] of stored[target.pathname] || []) if (allowed[target.pathname].includes(name)) target.searchParams.append(name,value);
      if (shared.length && ['/neostaffing/people','/neostaffing/attendance'].includes(target.pathname)) {
        scopeKeys.forEach(name => target.searchParams.delete(name)); shared.forEach(([name,value]) => target.searchParams.append(name,value));
      }
      link.href = target.pathname + target.search;
    }
  } catch (_) { /* Storage denied or stale: ordinary server-rendered links still work. */ }
})();
