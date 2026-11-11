(() => {
    const root = document.querySelector('[data-deicer-management]');
    const refresh = JSON.parse(root.dataset.refresh);
    const roster = root.querySelector('[data-attendance-roster]');
    const panel = root.querySelector('[data-deicer-panel]');
    let revision = '';
    if (!window.NeoLiveUpdates || refresh.live_screen_refresh_interval_ms < 1000) return;

    const controller = window.NeoLiveUpdates.create({
        intervalMs: refresh.live_screen_refresh_interval_ms,
        continuousWhileVisible: true,
        poll: async () => {
            const mutation = roster?.dataset.attendanceMutation;
            const url = new URL(root.dataset.stateUrl, window.location.href);
            if (revision) url.searchParams.set('revision', revision);
            const response = await (window.NeoLiveRevision?.fetch || fetch)(url, {
                cache: 'no-store', credentials: 'same-origin',
            });
            if (!response.ok) throw new Error('Unable to refresh Deicer Management.');
            const data = await response.json();
            if (String(data.operation_id ?? '') !== root.dataset.operationId) {
                root.querySelector('[data-deicer-sort-change]').hidden = false;
                panel.inert = true;
                controller.setEnabled(false);
                return;
            }
            controller.setServerStatus(data.refresh);
            if (roster && mutation === roster.dataset.attendanceMutation) {
                roster.dispatchEvent(new CustomEvent('attendance:refresh', {detail: data.rows}));
            } else if (!roster && typeof data.html === 'string'
                       && !panel.contains(document.activeElement)) {
                panel.innerHTML = data.html;
                revision = data.revision;
            }
        },
    });
    controller.setServerStatus(refresh);
})();
