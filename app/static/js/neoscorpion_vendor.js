/* Refresh live data and truck-specific controls from the canonical revision. */
(() => {'use strict';
 document.addEventListener('change',event=>{const select=event.target.closest('[data-vendor-driver-assignment] select[name=user_id]');if(select)select.form.elements.expected_driver_truck_id.value=select.selectedOptions[0]?.dataset.currentTruck||'';});
 document.addEventListener('click',event=>{const button=event.target.closest('[data-fuel-verification-skip]');if(button)button.closest('details').open=false;});
 const root=document.querySelector('[data-vendor-refresh]');if(!root)return;
 const access = document.querySelector('[data-vendor-access]');
 let revision = JSON.stringify(JSON.parse(root.dataset.revision));
 let busy = false;
 let denied = false;
 const options = {credentials: 'same-origin', cache: 'no-store'};

 function showDenied(message) {
    denied = true;
    access.setAttribute('role', 'alert');
    access.textContent = message;
 }

 async function refresh() {
    if (busy || denied || document.hidden) return;
    busy = true;
    try {
        const response = await fetch(root.dataset.revisionUrl, options);
        if (response.redirected || response.status === 401) {
            showDenied('Operational access ended. Open My Account to sign in again.');
            return;
        }
        if (response.status === 403) {
            const error = await response.json().catch(() => ({}));
            showDenied(error.error || 'Operational access revoked. Open My Account for your status.');
            return;
        }
        if (!response.ok) return;
        const next = JSON.stringify(await response.json());
        if (revision === next || document.activeElement?.closest('form')) return;

        const page = await fetch(location.href, options);
        if (page.redirected || page.status === 403) {
            showDenied('Operational access changed. Open My Account for your status.');
            return;
        }
        if (!page.ok) return;
        const doc = new DOMParser().parseFromString(await page.text(), 'text/html');
        if (document.activeElement?.closest('form')) return;
        const live = access.querySelector('[data-vendor-live]');
        const nextLive = doc.querySelector('[data-vendor-live]');
        if (!nextLive) return;
        live.replaceChildren(...nextLive.childNodes);
        const controls = access.querySelector('[data-vendor-truck-controls]');
        const nextControls = doc.querySelector('[data-vendor-truck-controls]');
        if (controls && nextControls && controls.dataset.vendorTruckControls !== nextControls.dataset.vendorTruckControls) {
            controls.replaceChildren(...nextControls.childNodes);
            controls.dataset.vendorTruckControls = nextControls.dataset.vendorTruckControls;
        }
        revision = next;
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
