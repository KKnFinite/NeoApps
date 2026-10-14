(() => {
    "use strict";
    const root=document.querySelector("[data-truck-driver]");
    if (!root) return;
    root.addEventListener("click",event => {
        const reset=event.target.closest("[data-driver-reset]");
        if (!reset) return;
        const form=reset.closest("[data-driver-form]");
        form.reset(); form.dataset.driverDirty="false";
        document.dispatchEvent(new Event("neoscorpion:driver-selection-saved"));
    });
    root.addEventListener("input",event => {
        const form=event.target.closest("[data-driver-form]");
        if (form) form.dataset.driverDirty="true";
    });
    root.addEventListener("change",event => {
        const form=event.target.closest("[data-driver-form]");
        if (!form) return;
        form.dataset.driverDirty="true";
        const truck=form.querySelector("[data-driver-truck]");
        const person=form.querySelector("[data-driver-person]");
        form.elements.expected_driver_id.value=truck.selectedOptions[0]?.dataset.driverId || "";
        if (form.hasAttribute("data-driver-management")) {
            if (event.target===truck) person.value=form.elements.expected_driver_id.value;
            form.elements.expected_driver_truck_id.value=person.selectedOptions[0]?.dataset.currentTruckId || "";
        }
    });
    root.addEventListener("submit",async event => {
        const form=event.target.closest("[data-driver-form]");
        if (!form) return;
        event.preventDefault();
        if (form.dataset.driverSaving==="true") return;
        form.dataset.driverSaving="true";
        const status=form.querySelector("[data-driver-save-status]");
        const button=form.querySelector("button[type=submit]");
        button.disabled=true; status.textContent="Saving…";
        try {
            const response=await fetch(form.action,{method:"POST",body:new FormData(form),credentials:"same-origin",
                headers:{Accept:"application/json"},cache:"no-store"});
            const payload=await response.json();
            if (!response.ok || payload.ok!==true) throw new Error(payload.error || "Driver selection failed.");
            form.dataset.driverDirty="false"; status.textContent="Saved";
            document.dispatchEvent(new Event("neoscorpion:driver-selection-saved"));
        } catch(error) { status.textContent=error.message || "Driver selection failed. Refresh and try again."; }
        finally { form.dataset.driverSaving="false"; button.disabled=false; }
    });
})();
