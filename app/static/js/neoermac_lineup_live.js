(function (global) {
    'use strict';
    function create(root, config) {
        const form = root.querySelector('[data-lineup-autosave-url]');
        const selects = Array.from(root.querySelectorAll('[data-lineup-destination-select]'));
        const order = global.NeoErmacLiveIntegrity.createOrder();
        let appliedRevision = root.dataset.lineupRevision || '', observedRevision = appliedRevision;
        let times = config.pullTimes || {};
        const empty = {pure: '--', mix: '--'};
        const work = new WeakMap();
        const message = (selector, text, state = '') => {
            const el = form.querySelector(selector);
            if (el) { el.textContent = text; el.hidden = !text; el.dataset.state = state; }
        };
        const paintTimes = (select) => select.closest('[data-lineup-assignment-slot]')
            ?.querySelectorAll('[data-pull-time-key]').forEach(el => {
                el.textContent = (times[select.value] || empty)[el.dataset.pullTimeKey] || '--';
            });
        const originalInput = select => form.elements.namedItem(`original_${select.name}`);
        const protectedSelect = select => select.dataset.lineupSaving === 'true'
            || select.dataset.localDirty === 'true' || document.activeElement === select;
        const reconcile = state => {
            let complete = true;
            times = state.pull_times || {};
            for (const select of selects) {
                const slot = state.slots?.[select.name];
                if (!slot) continue;
                if (protectedSelect(select)) { complete = false; continue; }
                const placeholder = select.options[0].textContent;
                const options = [new Option(placeholder, ''), ...(state.destination_choices || []).map(v => new Option(v, v))];
                // Preserve a persisted value even if its master was retired.
                if (slot.destination && !(state.destination_choices || []).includes(slot.destination)) options.push(new Option(slot.destination, slot.destination));
                select.replaceChildren(...options);
                select.value = slot.destination;
                select.dataset.lastSavedValue = slot.destination;
                originalInput(select).value = slot.original;
                paintTimes(select);
            }
            const emptyNote = root.querySelector('[data-lineup-empty-choices]');
            if (emptyNote) emptyNote.hidden = Boolean(state.destination_choices?.length);
            return complete;
        };
        const drain = async select => {
            const state = work.get(select);
            if (state.saving || state.desired === null) return;
            const destination = state.desired;
            state.desired = null;
            state.saving = true;
            select.dataset.lineupSaving = 'true';
            const ticket = order.beginSave();
            appliedRevision = ''; // A successful slot ACK is not a board snapshot.
            const body = new FormData();
            body.set('field', select.name); body.set('destination', destination);
            body.set('original', originalInput(select).value);
            message('[data-lineup-autosave-error]', '');
            message('[data-lineup-autosave-status]', 'SAVING', 'saving');
            let success = false;
            try {
                const response = await fetch(form.dataset.lineupAutosaveUrl, {
                    method: 'POST', body, credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'},
                });
                const payload = await response.json();
                if (!response.ok || !payload.ok) throw new Error(payload.error || 'Destination save failed.');
                // One in-flight request per slot. Other slots remain independent.
                originalInput(select).value = payload.original;
                select.dataset.lastSavedValue = payload.destination;
                times[payload.destination] = payload.pull_times || empty;
                if (state.desired === null && select.value === destination) {
                    select.value = payload.destination;
                    delete select.dataset.localDirty;
                    paintTimes(select);
                }
                success = true;
                message('[data-lineup-autosave-status]', 'SAVED', 'saved');
            } catch (error) {
                // Retain the unsaved choice; never replay a conflict or roll it
                // back with an older response. The normal error remains visible.
                state.desired = null;
                message('[data-lineup-autosave-status]', '');
                message('[data-lineup-autosave-error]', error.message, 'error');
            } finally {
                state.saving = false;
                delete select.dataset.lineupSaving;
                order.endSave(ticket);
            }
            if (success && state.desired !== null) await drain(select);
        };
        for (const select of selects) {
            work.set(select, {saving: false, desired: null});
            select.dataset.lastSavedValue = select.value;
            select.addEventListener('change', () => {
                if (select.disabled) return;
                select.dataset.localDirty = 'true';
                work.get(select).desired = select.value;
                paintTimes(select);
                void drain(select);
            });
        }
        const poll = async () => {
            const ticket = order.beginPoll();
            const url = new URL(root.dataset.stateUrl, global.location.origin);
            url.searchParams.set('revision', appliedRevision);
            const response = await fetch(url, {cache: 'no-store', credentials: 'same-origin'});
            const payload = await response.json();
            if (!response.ok || !payload.ok) throw new Error('Building Lineup refresh failed.');
            if (order.latestPoll(ticket)) controller.setServerStatus(payload.refresh);
            if (!order.accepts(ticket)) return;
            observedRevision = payload.revision || observedRevision;
            if (payload.changed && payload.state && reconcile(payload.state)) appliedRevision = observedRevision;
        };
        const controller = global.NeoLiveUpdates.create({intervalMs: config.refresh.live_screen_refresh_interval_ms, poll, continuousWhileVisible: true});
        controller.setServerStatus(config.refresh);
        return {poll, reconcile, get appliedRevision() { return appliedRevision; }, get observedRevision() { return observedRevision; }};
    }
    if (typeof module !== 'undefined' && module.exports) module.exports = {create};
    else global.NeoErmacLineupLive = {create};
})(typeof window === 'undefined' ? globalThis : window);
