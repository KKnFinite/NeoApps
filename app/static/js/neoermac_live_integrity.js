(function (global) {
    'use strict';
    // Request-local ordering, never a cache or a new poller. A revision is only
    // accepted after its state is applied, and no pre-mutation poll can win.
    function createOrder() {
        let epoch = 0, pending = 0, polls = 0, mutations = 0, lastOverlap = 0;
        return {
            beginSave() {
                mutations += 1;
                if (pending) lastOverlap = mutations;
                pending += 1; epoch += 1; return mutations;
            },
            // Response arrival order cannot prove database commit order for
            // overlapping writes. ACK their fields, but wait for the next poll
            // for a whole-board snapshot, even if replies arrive in send order.
            endSave(ticket) { pending -= 1; epoch += 1; return pending === 0 && ticket === mutations && ticket > lastOverlap; },
            beginPoll() { return {epoch, id: ++polls, idle: pending === 0}; },
            latestPoll(ticket) { return ticket.id === polls; },
            accepts(ticket) { return ticket.id === polls && ticket.epoch === epoch && ticket.idle && pending === 0; },
        };
    }
    function reconcileMembership(list, incoming, key, protectedRow, update) {
        const existing = new Map(Array.from(list.children).map(row => [key(row), row]));
        let complete = true;
        const ordered = [];
        for (const fresh of incoming) {
            const old = existing.get(key(fresh));
            existing.delete(key(fresh));
            if (old) {
                if (protectedRow(old)) complete = false;
                update(old, fresh);
            }
            ordered.push(old || fresh);
        }
        for (const old of existing.values()) {
            if (protectedRow(old)) { complete = false; ordered.push(old); }
            else old.remove();
        }
        // Moving an existing DOM subtree can blur its input. Reconcile clean
        // membership now, but defer physical reordering while local work exists.
        if (Array.from(list.children).some(protectedRow)) {
            ordered.filter(row => row.parentNode !== list && !Array.from(list.children).includes(row))
                .forEach(row => list.insertBefore(row, null));
            return false;
        }
        ordered.forEach((row, index) => {
            if (list.children[index] !== row) list.insertBefore(row, list.children[index] || null);
        });
        return complete;
    }
    const api = {createOrder, reconcileMembership};
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else global.NeoErmacLiveIntegrity = api;
})(typeof window === 'undefined' ? globalThis : window);
