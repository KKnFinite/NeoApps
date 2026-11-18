/* Native, accessible inline validation. No client-side authorization decisions. */
(() => {
  "use strict";
  document.querySelectorAll("[data-account-registration]").forEach((form) => {
    const password = form.elements.password;
    const confirmation = form.elements.confirm_password;
    const apps = Array.from(form.querySelectorAll('[name="app_codes"]'));
    const type = form.elements.account_type;
    const identity = form.elements.employee_id;
    const syncType = () => {
      if (!type || !identity) return;
      const driver = type.value === "fuel_driver";
      identity.required = driver;
      apps.forEach((input) => {
        if (driver) input.checked = input.value === "neogateway";
        input.disabled = driver && input.value !== "neogateway";
      });
    };
    type?.addEventListener("change", syncType);
    syncType();
    const validate = () => {
      confirmation.setCustomValidity(confirmation.value && confirmation.value !== password.value ? "Passwords must match." : "");
      if (apps.length) apps[0].setCustomValidity(apps.some((input) => input.checked) ? "" : "Select at least one application.");
    };
    form.addEventListener("input", validate);
    validate();
  });
})();
