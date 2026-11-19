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
      const driver = type.value !== "full";
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
  document.querySelectorAll("[data-account-approval]").forEach((form) => {
    const type = form.elements.account_type, identity = form.elements.employee_id;
    const sync = () => {
      const restricted = type.value !== "full";
      const apps = form.querySelector("[data-account-applications]");
      apps.hidden = restricted;
      apps.querySelectorAll("input,select").forEach((input) => { input.disabled = restricted; });
      identity.value = identity.value.trim();
      identity.required = restricted;
      if (restricted || identity.value !== identity.dataset.original) identity.pattern = "(?:[0-9]{7}|[0-9]{10})";
      else identity.removeAttribute("pattern");
    };
    form.addEventListener("input", sync);
    form.addEventListener("change", sync);
    sync();
  });
})();
