/* Presentation only: fit the complete aircraft/flight/gateway/destination line. */
(() => {
    'use strict';
    const selector = '[data-fuel-header]';
    const headers = new Set();
    const previous = new WeakMap();
    const pending = new Set();
    let frame = 0;

    function fit(header) {
        const line = header.querySelector('[data-fuel-header-line]');
        const width = header.clientWidth;
        if (!line || width <= 0) return; // A closed dialog will be sized when opened.
        const signature = `${width}:${line.textContent}`;
        if (previous.get(header) === signature) return;
        previous.set(header, signature);
        const maximum = parseFloat(getComputedStyle(header).getPropertyValue('--fuel-header-max-size')) || 36;
        line.style.fontSize = `${maximum}px`;
        const natural = line.getBoundingClientRect().width;
        if (natural > 0 && window.CSS && window.CSS.supports('font-size', '1cqw')) {
            // Container units resize synchronously with the card, including
            // between a viewport change and the next animation frame.
            line.style.fontSize = `min(${maximum}px, calc(100cqw * ${maximum / (natural + 1)}))`;
        } else if (natural > width) {
            line.style.fontSize = `${maximum * Math.max(0, width - 1) / natural}px`;
        }
    }

    function schedule(header) {
        pending.add(header);
        if (frame) return;
        frame = requestAnimationFrame(() => {
            frame = 0;
            for (const item of pending) if (item.isConnected) fit(item);
            pending.clear();
        });
    }

    const resize = typeof ResizeObserver === 'function'
        ? new ResizeObserver(entries => entries.forEach(entry => schedule(entry.target))) : null;

    function discover(root) {
        if (root.nodeType !== 1) return;
        const found = root.matches(selector) ? [root] : root.querySelectorAll(selector);
        for (const header of found) {
            if (!headers.has(header)) {
                headers.add(header);
                if (resize) resize.observe(header);
            }
            schedule(header);
        }
    }

    discover(document.documentElement);
    new MutationObserver(records => {
        for (const record of records) {
            for (const node of record.addedNodes) discover(node);
            const owner = record.target.nodeType === 1 ? record.target : record.target.parentElement;
            const header = owner && owner.closest(selector);
            if (header) schedule(header);
        }
        for (const header of headers) if (!header.isConnected) {
            if (resize) resize.unobserve(header);
            headers.delete(header);
            pending.delete(header);
        }
    }).observe(document.documentElement, {childList:true, characterData:true, subtree:true});

    // No timer, network calls, form handlers, or operational state changes.
    window.addEventListener('resize', () => headers.forEach(schedule));
    function fontsReady() {
        for (const header of headers) {
            previous.delete(header);
            schedule(header);
        }
    }
    if (document.fonts) {
        document.fonts.ready.then(fontsReady);
        document.fonts.addEventListener('loadingdone', fontsReady);
    }
})();
