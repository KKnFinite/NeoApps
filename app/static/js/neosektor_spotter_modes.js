/* Conductor authority only; consumes the existing page refresh, no poller. */
window.NeoSektorSpotterModes = {
    create(root, {state, canEdit, send, confirm = message => window.confirm(message), onError}) {
        let modes = state.ballmat_modes || {};
        const pending = new Set();
        const render = () => root.querySelectorAll('[data-conductor-mode]').forEach(panel => {
            const side = panel.dataset.conductorMode, detail = modes[side];
            if (!detail) return;
            panel.querySelector('[data-mode-current]').textContent = `${detail.mode} SPOTTER${detail.mode === 2 ? 'S' : ''}`;
            panel.querySelector('[data-mode-pending]').textContent = detail.pending_mode ? `REQUEST ${detail.pending_mode} PENDING` : '';
            panel.querySelectorAll('[data-mode-action]').forEach(button => {
                const decision = button.dataset.modeAction !== 'set';
                button.hidden = decision && !detail.pending_mode;
                button.disabled = !canEdit || !detail.available || pending.has(side)
                    || (!decision && Number(button.dataset.modeTarget) === detail.mode);
            });
        });
        const apply = next => { if (next.ballmat_modes) modes = next.ballmat_modes; render(); };
        root.addEventListener('click', async event => {
            const button = event.target.closest('[data-mode-action]');
            if (!button || button.disabled || !canEdit) return;
            const side = button.closest('[data-conductor-mode]').dataset.conductorMode;
            if (pending.has(side)) return;
            const detail = modes[side], action = button.dataset.modeAction;
            const target = action === 'approve' ? detail.pending_mode : Number(button.dataset.modeTarget);
            const clearing = action !== 'deny' && target === 1 && detail.right_nonzero;
            if (clearing && !confirm('Switch to 1 Spotter and clear RIGHT allocations? Published totals stay unchanged.')) return;
            const command = {side, action, mode: target, expected_mode: detail.mode,
                expected_mode_version: detail.mode_version, request_version: detail.request_version,
                confirm_clear_right: clearing};
            pending.add(side); render();
            try { await send(command); }
            catch (error) { onError(error.message); } // No automatic write retry.
            finally { pending.delete(side); render(); }
        });
        render();
        return {apply};
    },
};
