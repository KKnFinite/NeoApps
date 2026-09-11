/* Conductor controls and desktop Back Pickup use the existing page transport.
 * No poller, retry, or client-side routing calculation. */
window.NeoSektorDischargeControls = {
    create(root, {state, canEdit, send, onError}) {
        let current = state;
        const pending = new Set();
        const queued = new Map();
        const orderHost = root.querySelector('[data-discharge-order]');
        let dragged = null;
        const labels = ['Empty', 'Light', 'Moderate', 'Full', 'Overflowing'];
        const order = () => Array.from(orderHost.querySelectorAll('[data-priority-bay]'), b => b.dataset.priorityBay);
        const render = () => {
            const cut = root.querySelector('[data-discharge-cut]');
            if (cut && !pending.has('cut')) cut.checked = current.routing.cut_discharge;
            if (cut) cut.disabled = !canEdit || pending.has('cut');
            if (orderHost && !dragged && !pending.has('priority')) {
                const focused = document.activeElement;
                const restoreFocus = Array.from(orderHost.children).includes(focused);
                current.routing.bay_priority_order.forEach((name, index) => {
                    const button = orderHost.querySelector(`[data-priority-bay="${name}"]`);
                    orderHost.append(button);
                    button.disabled = !canEdit;
                    button.setAttribute('aria-label', `${name}, position ${index + 1}. Drag or use arrow keys to reorder.`);
                });
                if (restoreFocus) focused.focus();
            }
            for (const side of Object.values(current.sides)) for (const bay of side.bays) {
                const status = root.querySelector(`[data-discharge-status="${bay.bay_name}"]`);
                if (status && !pending.has(bay.bay_name)) status.value = labels.indexOf(bay.status);
                const statusLabel = status?.closest('[data-tunnel-bay]')?.querySelector('strong');
                if (statusLabel) statusLabel.textContent = labels[Number(status.value)];
                const back = root.querySelector(`[data-discharge-back="${bay.bay_name}"]`);
                if (back && !pending.has(bay.bay_name)) back.checked = Boolean(bay.back_pickup);
                if (back) back.disabled = !canEdit || bay.status !== 'Overflowing' || pending.has(bay.bay_name);
            }
        };
        const save = async (key, command) => {
            if (!canEdit) return;
            if (pending.has(key)) { queued.set(key, command); return; }
            pending.add(key);
            render();
            try {
                do {
                    try { await send(command); }
                    catch (error) { onError(error.message || 'Discharge update failed.'); }
                    command = queued.get(key);
                    queued.delete(key);
                } while (command);
            }
            finally { pending.delete(key); render(); }
        };
        root.addEventListener('input', event => {
            const input = event.target;
            if (input.matches('[data-discharge-status]')) {
                const card = input.closest('[data-tunnel-bay]');
                if (card) card.querySelector('strong').textContent = labels[Number(input.value)];
            }
        });
        root.addEventListener('change', event => {
            const input = event.target;
            if (!canEdit || input.disabled) return;
            if (input.matches('[data-discharge-cut]')) void save('cut', {
                action: 'cut', enabled: input.checked, expected_cut: current.routing.cut_discharge,
            });
            if (input.matches('[data-discharge-back]')) void save(input.dataset.dischargeBack, {
                side: input.dataset.side, back_pickups: {[input.dataset.dischargeBack]: input.checked},
            });
            if (input.matches('[data-discharge-status]')) void save(input.dataset.dischargeStatus, {
                side: input.dataset.side, bay_statuses: {[input.dataset.dischargeStatus]: labels[Number(input.value)]},
            });
        });
        const move = (button, target) => {
            if (!canEdit || pending.has('priority') || !target || button === target) return;
            const buttons = Array.from(orderHost.children);
            orderHost.insertBefore(button, buttons.indexOf(button) < buttons.indexOf(target) ? target.nextSibling : target);
        };
        const saveOrder = () => {
            const next = order();
            if (JSON.stringify(next) !== JSON.stringify(current.routing.bay_priority_order))
                void save('priority', {action: 'priority', order: next, expected_order: current.routing.bay_priority_order});
        };
        if (orderHost) {
            orderHost.addEventListener('dragstart', event => {
                if (!canEdit || pending.has('priority')) { event.preventDefault(); return; }
                dragged = event.target.closest('[data-priority-bay]');
                event.dataTransfer?.setData('text/plain', dragged?.dataset.priorityBay || '');
            });
            orderHost.addEventListener('dragover', event => { event.preventDefault(); });
            orderHost.addEventListener('drop', event => {
                event.preventDefault();
                if (dragged) { move(dragged, event.target.closest('[data-priority-bay]')); saveOrder(); }
                dragged = null;
            });
            orderHost.addEventListener('dragend', () => { dragged = null; render(); });
            // Touch drag uses the same order transaction, with no page drag/scroll.
            orderHost.addEventListener('pointerdown', event => {
                if (event.pointerType === 'mouse' || !canEdit || pending.has('priority')) return;
                dragged = event.target.closest('[data-priority-bay]');
                dragged?.setPointerCapture(event.pointerId);
            });
            orderHost.addEventListener('pointermove', event => {
                if (event.pointerType === 'mouse' || !dragged) return;
                move(dragged, document.elementFromPoint(event.clientX, event.clientY)?.closest('[data-priority-bay]'));
            });
            orderHost.addEventListener('pointerup', event => {
                if (event.pointerType === 'mouse') return;
                if (dragged) saveOrder();
                dragged = null;
            });
            orderHost.addEventListener('pointercancel', event => {
                // Native desktop drag fires pointercancel when drag starts.
                if (event.pointerType === 'mouse') return;
                dragged = null; render();
            });
            orderHost.addEventListener('keydown', event => {
                if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)) return;
                const button = event.target.closest('[data-priority-bay]');
                if (!button || !canEdit || pending.has('priority')) return;
                event.preventDefault();
                const step = ['ArrowLeft','ArrowUp'].includes(event.key) ? -1 : 1;
                const buttons = Array.from(orderHost.children);
                move(button, buttons[buttons.indexOf(button) + step]);
                button.focus(); saveOrder();
            });
        }
        const apply = next => { current = next; render(); };
        render();
        return {apply};
    },
};
