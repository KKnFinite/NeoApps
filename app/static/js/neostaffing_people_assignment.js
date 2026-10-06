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
  const sync = () => {
    const classification = person.selectedOptions[0]?.dataset.classification || '';
    const allowed = allowedTypes[classification] || [];
    let firstValid = null, currentScope = null;
    [...unit.options].forEach(option => {
      if (!option.value) { option.hidden = false; option.disabled = false; return; }
      const valid = allowed.includes(option.dataset.unitType);
      option.hidden = !valid; option.disabled = !valid;
      if (valid && !firstValid) firstValid = option;
      if (valid && option.dataset.currentScope === '1') currentScope = option;
    });
    if (!unit.value || unit.selectedOptions[0]?.disabled) unit.value = (currentScope || firstValid)?.value || '';
    const selectedType = unit.selectedOptions[0]?.dataset.unitType || '';
    level.value = selectedType;
    submit.disabled = !person.value || !unit.value || !selectedType;
    if (guidance) guidance.textContent = allowed.length
      ? 'Valid scope: ' + allowed.map(value => value.replace('_', ' ')).join(' / ') + '.'
      : 'Select a management person to see valid assignment scopes.';
  };
  person.addEventListener('change', sync);
  unit.addEventListener('change', sync);
  sync();
})();
