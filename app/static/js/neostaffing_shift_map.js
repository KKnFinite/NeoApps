(() => {
    "use strict";
    const root = document.querySelector("[data-shift-map]");
    if (!root) return;
    const picker = root.querySelector("[data-route-picker]");
    const select = root.querySelector("[data-route-employee]");
    const feedback = root.querySelector("[data-route-feedback]");
    const preview = root.querySelector("[data-route-preview]");
    const setup = root.querySelector("[data-route-setup]");
    let saving = false;
    const apply = async (target) => {
        if (saving || !select?.value) return;
        saving = true;
        feedback.textContent = "Saving route…";
        try {
            const response = await fetch(root.dataset.routeUrl.replace("/0/", `/${select.value}/`), {
                method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content || ""},
                body: JSON.stringify({final_door_id: target.dataset.door, band: target.dataset.band,
                    expected_version: select.selectedOptions[0].dataset.version, complete_route: true,
                    setup_mode: setup.value}),
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.conflict?.message || payload.error || "Route was not saved.");
            // A route can change every phase and Home: render the canonical
            // server projection once after the transaction, never infer a flow.
            window.location.reload();
        } catch (error) { feedback.textContent = error.message; }
        finally { saving = false; }
    };
    root.querySelectorAll("[data-route-person]").forEach(card => {
        card.addEventListener("dragstart", event => {
            select.value = card.dataset.routePerson;
            picker.open = true;
            event.dataTransfer.setData("text/plain", select.value);
            event.dataTransfer.effectAllowed = "move";
        });
    });
    root.querySelectorAll("[data-route-target]").forEach(target => {
        const show = () => { preview.textContent = target.dataset.preview.replace("NO SETUP", `SETUP: ${setup.selectedOptions[0].textContent}`); };
        target.addEventListener("focus", show);
        target.addEventListener("mouseenter", show);
        target.addEventListener("dragover", event => { event.preventDefault(); show(); target.classList.add("is-target"); });
        target.addEventListener("dragleave", () => target.classList.remove("is-target"));
        target.addEventListener("drop", event => { event.preventDefault(); target.classList.remove("is-target"); apply(target); });
        target.addEventListener("click", () => apply(target));
    });
    const draw = () => {
        const svg = root.querySelector("[data-flow-lines]");
        const origin = root.getBoundingClientRect();
        svg.replaceChildren();
        if (window.innerWidth <= 900) return;
        const ids = new Set([...root.querySelectorAll('[data-map-custom="true"]')].map(node => node.dataset.mapPerson));
        ids.forEach(id => {
            const points = [...root.querySelectorAll(`[data-map-person="${id}"]`)].map(node => {
                const box = node.getBoundingClientRect();
                return `${box.left-origin.left+box.width/2},${box.top-origin.top+box.height/2}`;
            });
            const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
            path.setAttribute("d", `M ${points.join(" L ")}`); svg.append(path);
        });
    };
    window.addEventListener("resize", draw);
    document.fonts?.ready.then(draw);
    draw();
})();
