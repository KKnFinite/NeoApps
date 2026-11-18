/* Native, accessible inline validation. No client-side authorization decisions. */
(() => {
  "use strict";
  document.querySelectorAll("[data-account-registration]").forEach((form) => {
    const password = form.elements.password;
    const confirmation = form.elements.confirm_password;
    const apps = Array.from(form.querySelectorAll('[name="app_codes"]'));
    const validate = () => {
      confirmation.setCustomValidity(confirmation.value && confirmation.value !== password.value ? "Passwords must match." : "");
      if (apps.length) apps[0].setCustomValidity(apps.some((input) => input.checked) ? "" : "Select at least one application.");
    };
    form.addEventListener("input", validate);
    validate();
  });
})();
