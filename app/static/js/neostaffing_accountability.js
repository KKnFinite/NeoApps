/* Presentation only: dated history inputs. All authority/calculation is server-side. */
(() => {
    document.querySelectorAll('[data-accountability-entries]').forEach(section => {
        const host = section.querySelector('[data-entry-host]');
        const template = section.querySelector('template');
        const button = section.querySelector('[data-add-entry]');
        const limit = Number(section.dataset.entryLimit);
        button.addEventListener('click', () => {
            if (host.children.length >= limit) return;
            host.appendChild(template.content.cloneNode(true));
            button.disabled = host.children.length >= limit;
            host.lastElementChild.querySelector('input').focus();
        });
    });
})();
