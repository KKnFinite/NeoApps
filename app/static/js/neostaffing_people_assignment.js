(() => {
  'use strict';
  const form = document.querySelector('[data-management-assignment-form]');
  if (!form) return;
  const person = form.querySelector('[data-management-person]');
  const unit = form.querySelector('[data-management-unit]');
  const level = form.querySelector('[data-management-level]');
  const guidance = form.querySelector('[data-management-assignment-guidance]');
  const submit = form.querySelector('[data-management-assignment-submit]');
  if (!person || !unit || !level || !submit) return;
  const allowedTypes = {
    part_time_supervisor: ['work_area'],
    full_time_supervisor: ['department'],
    twenty_c_full_time_supervisor: ['work_area', 'department'],
    full_time_specialist: ['department', 'operation'],
    manager: ['operation'],
    division_manager: ['sort'],
  };
  const currentScope = [...unit.options].find(option => option.dataset.currentScope === '1');
  if (!unit.value && currentScope) unit.value = currentScope.value;
  const sync = (preferScope = false) => {
    const classification = person.selectedOptions[0]?.dataset.classification || '';
    const allowed = allowedTypes[classification] || [];
    [...unit.options].forEach(option => {
      const valid = !option.value || !classification || allowed.includes(option.dataset.unitType);
      option.hidden = !valid; option.disabled = !valid;
    });
    if (unit.selectedOptions[0]?.disabled) unit.value = '';
    if (preferScope && !unit.value && currentScope && !currentScope.disabled) unit.value = currentScope.value;
    const selectedType = unit.selectedOptions[0]?.dataset.unitType || '';
    level.value = selectedType;
    submit.disabled = !person.value || !unit.value || !allowed.includes(selectedType);
    if (guidance) guidance.textContent = classification
      ? 'Valid scope: ' + allowed.map(value => value.replace('_', ' ')).join(' / ') + '.'
      : 'Select a management person. Assignment scopes will be checked for their classification.';
  };
  person.addEventListener('change', () => sync(true));
  unit.addEventListener('change', () => sync());
  sync();
})();
