/* Blank keeps the existing unset contract. Density units remain lb/gal. */
(() => {
    'use strict';
    const density = document.querySelector('[name="fuel_density_lbs_per_gallon"]');
    if (!density || density.readOnly) return;
    const validate = () => {
        const text = density.value.trim();
        const decimal = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(text);
        const valid = !text || (decimal && Number.isFinite(Number(text)) && Number(text) > 0);
        density.setCustomValidity(valid ? '' : 'Fuel density must be a finite number greater than zero (lb/gal), or blank to unset.');
    };
    density.addEventListener('input', validate);
    validate();
})();
