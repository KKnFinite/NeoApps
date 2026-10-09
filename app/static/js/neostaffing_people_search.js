(() => {
  'use strict';
  const form = document.querySelector('[data-people-search-form]');
  if (!form) return;
  const root = form.querySelector('[data-people-search-root]');
  const input = form.querySelector('[data-people-search-input]');
  const results = form.querySelector('[data-people-search-results]');
  const clear = form.querySelector('[data-people-search-clear]');
  if (!root || !input || !results) return;

  let timer = 0, controller = null, items = [], active = -1;

  const hide = () => {
    results.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    active = -1;
  };
  const params = () => {
    const out = new URLSearchParams(new FormData(form));
    out.delete('page'); out.delete('person_id');
    out.set('search', input.value.trim());
    return out;
  };
  const go = item => {
    if (document.querySelector('[data-employee-editor]')) {
      hide(); document.dispatchEvent(new CustomEvent('neostaffing:edit-employee', {detail:{personId:item.id}})); return;
    }
    const query = params();
    query.set('person_id', item.id);
    const target = new URL(form.action || window.location.pathname, window.location.href);
    target.search = query.toString();
    window.location.assign(target.pathname + target.search);
  };
  const render = rows => {
    items = rows || []; active = -1; results.replaceChildren();
    if (!items.length) {
      const empty = document.createElement('div');
      empty.className = 'neostaffing-people-search-empty';
      empty.textContent = 'No matching people';
      results.append(empty);
    } else {
      items.forEach((item, index) => {
        const button = document.createElement('button');
        button.type = 'button'; button.role = 'option';
        button.className = 'neostaffing-people-search-result';
        button.dataset.peopleSearchIndex = String(index);
        const name = document.createElement('strong');
        const meta = document.createElement('span');
        const area = document.createElement('small');
        name.textContent = item.name;
        meta.textContent = [item.employee_id, item.classification].filter(Boolean).join(' · ');
        area.textContent = item.work_area || 'Unassigned';
        button.append(name, meta, area);
        button.addEventListener('mousedown', event => event.preventDefault());
        button.addEventListener('click', () => go(item));
        results.append(button);
      });
    }
    results.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  };
  const select = index => {
    if (!items.length) return;
    active = (index + items.length) % items.length;
    results.querySelectorAll('[data-people-search-index]').forEach((node, i) => {
      node.classList.toggle('is-active', i === active);
      node.setAttribute('aria-selected', String(i === active));
    });
  };
  const load = async () => {
    const term = input.value.trim();
    if (term.length < 2) { hide(); return; }
    controller?.abort(); controller = new AbortController();
    try {
      const response = await fetch(root.dataset.searchUrl + '?' + params().toString(), {
        signal: controller.signal,
        headers: {'Accept': 'application/json'},
      });
      if (!response.ok) throw new Error('search failed');
      render((await response.json()).results || []);
    } catch (error) {
      if (error.name !== 'AbortError') hide();
    }
  };

  input.addEventListener('input', event => {
    if (event.isComposing) return;
    window.clearTimeout(timer);
    timer = window.setTimeout(load, 160);
  });
  input.addEventListener('keydown', event => {
    if (event.key === 'ArrowDown' && !results.hidden) { event.preventDefault(); select(active + 1); }
    else if (event.key === 'ArrowUp' && !results.hidden) { event.preventDefault(); select(active - 1); }
    else if (event.key === 'Enter' && active >= 0 && items[active]) { event.preventDefault(); go(items[active]); }
    else if (event.key === 'Escape') { hide(); }
  });
  form.querySelectorAll('[data-people-auto-submit]').forEach(selectBox =>
    selectBox.addEventListener('change', () => form.requestSubmit())
  );
  form.addEventListener('submit', () => {
    form.querySelectorAll('[name="page"],[name="person_id"]').forEach(field => field.disabled = true);
  });
  clear?.addEventListener('click', () => {
    input.value = ''; hide();
    form.querySelectorAll('[name="page"],[name="person_id"]').forEach(field => field.disabled = true);
    form.requestSubmit();
  });
  document.addEventListener('click', event => {
    if (!root.contains(event.target)) hide();
  });
})();
