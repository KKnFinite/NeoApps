(function (global) {
    'use strict';
    const layouts = [
        ['[data-neoermac-outbound-table-body]', '[data-neoermac-outbound-row]', 'desktop'],
        ['[data-neoermac-outbound-mobile-list]', '[data-neoermac-outbound-mobile-row]', 'mobile'],
    ];
    function applyDelta(content, delta, previous, manifest) {
        if (!previous || !manifest || previous.scope !== manifest.scope || !delta
            || !Array.isArray(delta.order) || !delta.rows || !manifest.rows) return false;
        const order = delta.order;
        const sameKeys = (a, b) => JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());
        if (new Set(order).size !== order.length || !sameKeys(order, Object.keys(manifest.rows))
            || Object.keys(delta.rows).some(key => !order.includes(key))) return false;
        const prepared = [];
        for (const [hostSelector, rowSelector, layout] of layouts) {
            const host = content.querySelector(hostSelector);
            if (!host) return false;
            const existing = Array.from(host.querySelectorAll(rowSelector));
            const nodes = new Map(existing.map(node => [node.dataset.outboundKey, node]));
            if (nodes.size !== existing.length || !sameKeys(nodes.keys(), Object.keys(previous.rows))) return false;
            const replacements = new Map();
            for (const key of order) {
                const change = delta.rows[key];
                if (!change && (!nodes.has(key) || previous.rows[key] !== manifest.rows[key])) return false;
                if (!change) continue;
                if (typeof change[layout] !== 'string') return false;
                const template = content.ownerDocument.createElement('template');
                template.innerHTML = layout === 'desktop'
                    ? `<table><tbody>${change[layout]}</tbody></table>` : change[layout];
                const matches = template.content.querySelectorAll(rowSelector);
                if (matches.length !== 1 || matches[0].dataset.outboundKey !== key) return false;
                replacements.set(key, matches[0]);
            }
            prepared.push({host, rowSelector, nodes, replacements});
        }
        // Validate BOTH representations before changing either. Preserve the
        // table/scroller/header and every unchanged mission's existing nodes.
        for (const {host, rowSelector, nodes, replacements} of prepared) {
            for (const [key, node] of nodes) if (!order.includes(key)) node.remove();
            for (const [key, node] of replacements) {
                if (nodes.has(key)) nodes.get(key).replaceWith(node);
                nodes.set(key, node);
            }
            let cursor = host.querySelector(rowSelector);
            for (const key of order) {
                const node = nodes.get(key);
                if (node !== cursor) host.insertBefore(node, cursor);
                cursor = node.nextElementSibling;
            }
        }
        return true;
    }

    function create(root, config) {
        const content = root.querySelector('[data-neoermac-outbound-content]');
        let currentRevision = root.dataset.outboundRevision || '';
        let manifest = config.manifest || null;
        let issued = 0;
        const applyRefreshStatus = status => {
            const refresh = status || {};
            const enabled = refresh.auto_refresh_enabled !== false;
            const banner = root.querySelector('[data-operation-refresh-banner]');
            if (banner) {
                if (!enabled && refresh.message) {
                    banner.replaceChildren(document.createTextNode(refresh.message));
                    if (refresh.operation_label && refresh.window_label) {
                        const detail = document.createElement('span');
                        detail.textContent = `${refresh.operation_label} \u00b7 ${refresh.window_label}`;
                        banner.appendChild(detail);
                    }
                }
                banner.hidden = enabled;
            }
            root.dataset.refreshActive = enabled ? 'true' : 'false';
            root.dataset.refreshReason = refresh.reason || 'outside_sort_window';
            root.dataset.refreshLabel = refresh.live_status_label || 'Live updates off - outside Sort window';
            controller.setServerStatus(refresh);
        };
        const poll = async () => {
            const ticket = ++issued;
            const url = new URL(root.dataset.refreshUrl || global.location.href, global.location.origin);
            if (currentRevision) url.searchParams.set('revision', currentRevision);
            const encoded = manifest && JSON.stringify(manifest);
            // Stay below the production request-line limit. Very large boards
            // use the full snapshot fallback rather than failing with HTTP 414.
            if (encoded && encodeURIComponent(encoded).length + url.href.length < 3500) {
                url.searchParams.set('rows', encoded);
            }
            const response = await fetch(url, {cache: 'no-store', credentials: 'same-origin',
                headers: {'X-Requested-With': 'XMLHttpRequest'}});
            if (ticket !== issued) return;
            if (response.status === 428) { global.location.reload(); return; }
            if (!response.ok) throw new Error('Outbound refresh failed.');
            const payload = await response.json();
            if (ticket !== issued) return;
            if (!payload.ok) throw new Error('Outbound refresh failed.');
            applyRefreshStatus(payload.refresh);
            if (payload.changed === true) {
                if (typeof payload.content_html === 'string') content.innerHTML = payload.content_html;
                else if (!applyDelta(content, payload.row_delta, manifest, payload.row_manifest)) {
                    // Next normal poll requests a snapshot; no extra fetch loop.
                    manifest = null;
                    currentRevision = 'snapshot-required';
                    return;
                }
                manifest = payload.row_manifest || null;
            }
            currentRevision = payload.revision || currentRevision;
        };
        const controller = global.NeoLiveUpdates.create({
            intervalMs: config.refresh.live_screen_refresh_interval_ms,
            statusElement: document.querySelector('[data-live-update-status]'),
            poll, continuousWhileVisible: true,
        });
        controller.setServerStatus(config.refresh);
        return {poll};
    }
    if (typeof module !== 'undefined' && module.exports) module.exports = {create, applyDelta};
    else global.NeoErmacOutboundLive = {create, applyDelta};
})(typeof window === 'undefined' ? globalThis : window);
