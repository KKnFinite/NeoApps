/* Native, accessible inline validation. No client-side authorization decisions. */
(() => {
  "use strict";
  document.querySelectorAll("[data-account-registration]").forEach((form) => {
    const password = form.elements.password;
    const confirmation = form.elements.confirm_password;
    const apps = Array.from(form.querySelectorAll('[name="app_codes"]'));
    const type = form.elements.account_type;
    const identity = form.elements.employee_id;
    const validateIdentity = () => {
      if (!identity) return;
      const normalized = identity.value.trim();
      if (identity.value !== normalized) identity.value = normalized;
      identity.required = true;
      identity.setCustomValidity(/^(?:[0-9]{7}|[0-9]{10})$/.test(normalized)
        ? "" : "Enter exactly 7 or 10 digits (1234567 or 1234567890), numbers only.");
    };
    const syncType = () => {
      if (!type || !identity) return;
      const driver = type.value === "fuel_driver";
      identity.required = true;
      apps.forEach((input) => {
        if (driver) input.checked = input.value === "neogateway";
        input.disabled = driver && input.value !== "neogateway";
      });
    };
    type?.addEventListener("change", syncType);
    syncType();
    const validate = () => {
      validateIdentity();
      confirmation.setCustomValidity(confirmation.value && confirmation.value !== password.value ? "Passwords must match." : "");
      if (apps.length) apps[0].setCustomValidity(apps.some((input) => input.checked) ? "" : "Select at least one application.");
    };
    form.addEventListener("input", validate);
    validate();
  });
})();
