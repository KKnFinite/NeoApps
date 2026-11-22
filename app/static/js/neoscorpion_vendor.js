/* Refresh live data and truck-specific controls from the canonical revision. */
(() => {'use strict';
 document.addEventListener('change',event=>{const select=event.target.closest('[data-vendor-driver-assignment] select[name=user_id]');if(select)select.form.elements.expected_driver_truck_id.value=select.selectedOptions[0]?.dataset.currentTruck||'';});
 document.addEventListener('click',event=>{const button=event.target.closest('[data-fuel-verification-skip]');if(button)button.closest('details').open=false;});
 const root=document.querySelector('[data-vendor-refresh]');if(!root)return;
 const access = document.querySelector('[data-vendor-access]');
 let revision = JSON.stringify(JSON.parse(root.dataset.revision));
 let pendingPage = null;
 let busy = false;
 let denied = false;
 const options = {credentials: 'same-origin', cache: 'no-store'};
 const missionsSelector = '[data-vendor-sort-missions]';
 // The server publishes physical Sort windows from the canonical weekly
 // schedule. Missing/malformed windows fail closed: no automatic HTTP.
 let windows = [];
 try {
    const declared = JSON.parse(root.dataset.sortWindows || '[]');
    if (Array.isArray(declared)) {
        windows = declared.filter(pair => Array.isArray(pair) && pair.length === 2)
            .map(([first, last]) => [Date.parse(first), Date.parse(last)])
            .filter(([first, last]) => Number.isFinite(first) && Number.isFinite(last) && last > first);
    }
 } catch (_) { /* No implicit off-hours fallback. */ }
 function withinSort() {
    const now = Date.now();
    return windows.some(([start, end]) => start <= now && now < end);
 }

 function showDenied(message) {
    denied = true;
    pendingPage = null;
    access.setAttribute('role', 'alert');
    access.textContent = message;
 }

 function updateTruckRegions() {
    if (!pendingPage || document.activeElement?.closest('form')) return;
    const live = access.querySelector('[data-vendor-live]');
    live.replaceChildren(...pendingPage.querySelector('[data-vendor-live]').childNodes);
    const controls = access.querySelector('[data-vendor-truck-controls]');
    const nextControls = pendingPage.querySelector('[data-vendor-truck-controls]');
    if (controls && nextControls && controls.dataset.vendorTruckControls !== nextControls.dataset.vendorTruckControls) {
        controls.replaceChildren(...nextControls.childNodes);
        controls.dataset.vendorTruckControls = nextControls.dataset.vendorTruckControls;
    }
    pendingPage = null;
 }

 async function refresh() {
    if (busy || denied || document.hidden || !withinSort()) return;
    busy = true;
    try {
        const response = await fetch(root.dataset.revisionUrl, options);
        if (response.status === 204) return; // Server closed the Sort gate.
        if (response.redirected || response.status === 401) {
            showDenied('Your session ended. Sign in again.');
            window.location.replace(root.dataset.loginUrl);
            return;
        }
        if (response.status === 403) {
            const error = await response.json().catch(() => ({}));
            showDenied(error.error || 'Operational access revoked.');
            if (root.dataset.previewExit || root.dataset.workspaceUrl) {
                window.location.replace(root.dataset.previewExit || root.dataset.workspaceUrl);
            }
            return;
        }
        if (!response.ok) return;
        const state = await response.json();
        const next = JSON.stringify(state);
        if (state.sort_missions_revision === null && access.querySelector(missionsSelector)) {
            showDenied('Your truck connection ended.');
            window.location.replace(root.dataset.workspaceUrl);
            return;
        }
        if (revision === next) {
            updateTruckRegions();
            return;
        }
        pendingPage = null;
        if (!state.sort_missions_revision && document.activeElement?.closest('form')) return;

        if (root.dataset.waiting === 'true') {
            if (!withinSort()) return;
            window.location.reload();
            return;
        }
        if (!withinSort()) return;
        const page = await fetch(location.href, options);
        if (page.redirected || page.status === 403) {
            showDenied('Operational access changed. Return to Fuel Truck Driver for your status.');
            if (page.redirected) window.location.replace(root.dataset.loginUrl);
            else if (root.dataset.previewExit || root.dataset.workspaceUrl) window.location.replace(root.dataset.previewExit || root.dataset.workspaceUrl);
            return;
        }
        if (!page.ok) return;
        const doc = new DOMParser().parseFromString(await page.text(), 'text/html');
        if (!doc.querySelector('[data-vendor-live]')) {
            if (root.dataset.workspaceUrl) {
                showDenied('Operational access changed. Return to Fuel Truck Driver for your status.');
                window.location.replace(root.dataset.workspaceUrl);
            }
            return;
        }
        const missions = access.querySelector(missionsSelector);
        const nextMissions = doc.querySelector(missionsSelector);
        if (missions && !nextMissions) {
            showDenied('Operational access changed. Return to Fuel Truck Driver for your status.');
            window.location.replace(root.dataset.workspaceUrl);
            return;
        }
        if (missions) missions.replaceWith(nextMissions);
        else if (nextMissions) access.append(nextMissions);

        revision = next;
        pendingPage = doc;
        updateTruckRegions();
    } catch (_) {
        // Keep the current form during a transient network failure.
    } finally {
        busy = false;
    }
 }

 document.addEventListener('visibilitychange', refresh);
 window.addEventListener('pageshow', refresh);
 refresh();
 setInterval(refresh, 10000);
})();
