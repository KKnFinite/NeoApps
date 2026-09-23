/* A dated Sort is the navigation action; scope stays in the GET form. */
(() => {
  document.querySelectorAll('[data-node-selection]').forEach(form => {
    form.addEventListener('change', () => form.requestSubmit());
  });
})();
