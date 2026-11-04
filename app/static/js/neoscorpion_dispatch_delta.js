/* Canonical Dispatch fragment transport. No polling, storage or mutations. */
(function () {
    'use strict';
    const start = /^neo-dispatch:([\w-]+):([a-f0-9]{32}):(-|[a-f0-9]{64})$/;
    function scan(root) {
        const result = new Map();
        const missionRows = new Map();
        root.querySelectorAll('tbody > tr[data-dispatch-sort-group]').forEach(row => {
            const id = row.dataset.dispatchSortGroup;
            if (!missionRows.has(id)) missionRows.set(id, []);
            missionRows.get(id).push(row);
        });
        const walker = document.createTreeWalker(root, 128);
        let comment;
        while ((comment = walker.nextNode())) {
            const match = start.exec(comment.nodeValue.trim());
            if (!match) continue;
            const [, key, digest, critical] = match;
            if (result.has(key)) throw new Error('Duplicate Dispatch fragment.');
            const nodes = [comment];
            let next = comment.nextSibling;
            while (next && !(next.nodeType === 8 && next.nodeValue.trim() === '/neo-dispatch:' + key)) {
                nodes.push(next); next = next.nextSibling;
            }
            if (!next) throw new Error('Incomplete Dispatch fragment.');
            nodes.push(next);
            if (key.startsWith('mission-')) {
                // Existing personal sorting moves physical row groups without
                // moving comment markers. Bind the marker to those same live
                // rows by permanent mission ID, never to its old sibling slot.
                const rows = missionRows.get(key.slice(8));
                if (!rows?.length) continue; // A critical update removed it.
                nodes.splice(1, nodes.length - 2, ...rows);
            }
            result.set(key, {nodes, digest, critical});
        }
        return result;
    }
    const bounded = values => Object.fromEntries(Object.entries(values).slice(0, 80));
    function fingerprints(root) {
        const full = {}, critical = {};
        scan(root).forEach((part, key) => {
            full[key] = part.digest;
            if (part.critical !== '-') critical[key.startsWith('mission-') ? key.slice(8) : key] = part.critical;
        });
        return {full: bounded(full), critical: bounded(critical)};
    }
    function prepare(nextPanel, root) {
        const current = scan(root), transfers = [], seen = new Set();
        nextPanel.querySelectorAll('template[data-dispatch-reuse]').forEach(placeholder => {
            const key = placeholder.dataset.dispatchReuse, part = current.get(key);
            if (seen.has(key) || !part || part.digest !== placeholder.dataset.partDigest) {
                throw new Error('Dispatch fragment unavailable; retrying a complete snapshot.');
            }
            seen.add(key);
            const clones = part.nodes.map(node => node.cloneNode(true));
            clones.forEach(node => placeholder.parentNode.insertBefore(node, placeholder));
            placeholder.remove();
            transfers.push({part, clones});
        });
        // Validate the entire candidate before touching the working document.
        scan(nextPanel);
        return () => {
            transfers.forEach(({part, clones}) => {
                if (!part.nodes.every(node => node.parentNode === part.nodes[0].parentNode)
                        || !clones.every(node => node.parentNode === clones[0].parentNode)) {
                    throw new Error('Dispatch fragment changed during refresh.');
                }
            });
            transfers.forEach(({part, clones}) => {
                const parent = clones[0].parentNode;
                part.nodes.forEach(node => parent.insertBefore(node, clones[0]));
                clones.forEach(node => node.remove());
            });
        };
    }
    function keyFor(node) {
        const row = node?.closest?.('[data-dispatch-sort-group]');
        if (row) return 'mission-' + row.dataset.dispatchSortGroup;
        const truck = node?.closest?.('[data-dispatch-truck-id]');
        if (truck) return 'truck-' + truck.dataset.dispatchTruckId;
        if (node?.closest?.('[data-assets-workspace]')) return 'assets';
        return null;
    }
    function applyRanks(panel, manifest) {
        if (!manifest) return;
        const rows = panel.querySelectorAll('.neoscorpion-dispatch-primary-row[data-dispatch-mission-id]');
        if (rows.length !== Object.keys(manifest).length) throw new Error('Incomplete Dispatch mission manifest.');
        rows.forEach(row => {
            const ranks = manifest[row.dataset.dispatchMissionId];
            if (!ranks || !Number.isInteger(ranks.arrival) || !Number.isInteger(ranks.etd)) {
                throw new Error('Invalid Dispatch mission order.');
            }
            row.dataset.dispatchArrivalRank = String(ranks.arrival);
            row.dataset.dispatchEtdRank = String(ranks.etd);
        });
    }
    function saveFocus(panel) {
        const node = document.activeElement;
        if (!panel.contains(node)) return null;
        return {node, key:keyFor(node), name:node.getAttribute('name'), tag:node.tagName,
            selection:typeof node.selectionStart === 'number' ? [node.selectionStart,node.selectionEnd] : null};
    }
    function restoreFocus(saved, root) {
        if (!saved) return;
        let node = saved.node;
        if (!node.isConnected && saved.key && saved.name) {
            const part = scan(root).get(saved.key);
            node = part?.nodes.flatMap(item => Array.from(item.querySelectorAll?.('[name]') || []))
                .find(item => item.tagName === saved.tag && item.getAttribute('name') === saved.name);
        }
        if (!node?.isConnected || node.disabled) return;
        node.focus({preventScroll:true});
        if (saved.selection && node.setSelectionRange) node.setSelectionRange(...saved.selection);
    }
    window.NeoScorpionDispatchDelta = Object.freeze({fingerprints, prepare, keyFor, applyRanks, saveFocus, restoreFocus, bounded});
})();
