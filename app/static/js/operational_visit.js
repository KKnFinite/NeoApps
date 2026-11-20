(() => {
    const { visit, visitUrl } = document.currentScript.dataset;
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
    if (visit && csrf && window.top === window.self) {
        fetch(visitUrl, { method: "POST", credentials: "same-origin",
            body: new URLSearchParams({ visit, csrf_token: csrf }), keepalive: true })
            .catch(() => {}); // Navigation preferences never interrupt operational screens.
    }
})();
