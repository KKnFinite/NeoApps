(() => {
    "use strict";

    const card = document.querySelector("[data-quick-fuel]");
    if (!card) return;
    const form = card.querySelector("[data-quick-fuel-form]");
    const tailInput = form.querySelector("[data-quick-tail]");
    const tankGrid = form.querySelector("[data-quick-tanks]");
    const totalRow = form.querySelector("[data-quick-total-row]");
    const controls = form.querySelector("[data-quick-controls]");
    const totals = form.querySelector("[data-quick-totals]");
    const transfer = form.querySelector("[data-quick-transfer]");
    const transferOutput = form.querySelector("[data-quick-transfer-output]");
    const sourceWrap = form.querySelector("[data-quick-apu-source]");
    const sourceSelect = sourceWrap.querySelector("select");
    const manualWrap = form.querySelector("[data-quick-manual-toggle]");
    const manualValueWrap = form.querySelector("[data-quick-manual-value]");
    const apuRunning = form.elements.apu_running;
    const manual = form.elements.apu_override_enabled;
    let requestedTail = "";
    let displayedTail = "";
    let sequence = 0;
    let timer;

    const clearOutputs = () => {
        tankGrid.querySelectorAll("[data-quick-tank-code]").forEach((row) => row.remove());
        card.querySelectorAll("[data-quick-total]").forEach((node) => { node.textContent = "—"; });
        sourceSelect.replaceChildren(new Option("Select tank", ""));
        tankGrid.hidden = true;
        controls.hidden = true;
        totals.hidden = true;
        transfer.hidden = true;
        transferOutput.hidden = true;
        form.querySelector("[data-quick-apu-reference]").hidden = true;
        form.querySelector("[data-quick-aircraft]").textContent = "Enter a supported tail number to load its tank layout.";
        displayedTail = "";
    };

    const resetForTail = (tail) => {
        sequence += 1;
        clearTimeout(timer);
        for (const input of form.querySelectorAll("input")) {
            if (input !== tailInput && input.name !== "csrf_token") input.value = "";
        }
        for (const select of form.querySelectorAll("select")) select.selectedIndex = 0;
        clearOutputs();
        tailInput.value = tail;
        requestedTail = tail;
    };

    const makeTankRow = ({code, label}) => {
        const row = document.createElement("div");
        row.className = "neoscorpion-fuel-tank-row";
        row.setAttribute("role", "row");
        row.dataset.quickTankCode = code;
        const title = document.createElement("strong");
        title.setAttribute("role", "rowheader");
        title.textContent = label;
        row.append(title);
        for (const kind of ["remaining", "planned", "actual"]) {
            if (kind === "planned") {
                const planned = document.createElement("span");
                planned.className = "neoscorpion-planned-value";
                planned.setAttribute("role", "cell");
                planned.dataset.quickPlanned = code;
                planned.textContent = "—";
                row.append(planned);
            } else {
                const cell = document.createElement("label");
                cell.setAttribute("role", "cell");
                const input = document.createElement("input");
                input.name = `${kind}_${code}`;
                input.inputMode = "decimal";
                input.autocomplete = "off";
                input.setAttribute("aria-label", `${label} ${kind}`);
                cell.append(input);
                row.append(cell);
            }
        }
        return row;
    };

    const show = (data) => {
        const issues = form.querySelector("[data-quick-issues]");
        if (!data.ok) {
            clearOutputs();
            issues.textContent = data.issues.join(" ");
            return;
        }
        if (displayedTail !== data.tail) {
            tankGrid.querySelectorAll("[data-quick-tank-code]").forEach((row) => row.remove());
            sourceSelect.replaceChildren(new Option("Select tank", ""));
            for (const tank of data.tank_rows) {
                tankGrid.insertBefore(makeTankRow(tank), totalRow);
                sourceSelect.add(new Option(tank.label, tank.code));
            }
            displayedTail = data.tail;
            window.NeoScorpionFuelPlanning?.initialize(tankGrid);
        }
        form.querySelector("[data-quick-aircraft]").textContent = `${data.tail} · ${data.aircraft_type}`;
        controls.hidden = false;
        tankGrid.hidden = false;
        totals.hidden = false;
        transfer.hidden = false;
        transferOutput.hidden = false;
        const apuYes = apuRunning.value === "yes";
        sourceWrap.hidden = !apuYes;
        manualWrap.hidden = !apuYes;
        manualValueWrap.hidden = !apuYes || manual.value !== "1";
        const reference = form.querySelector("[data-quick-apu-reference]");
        reference.hidden = !apuYes;
        reference.textContent = data.apu.automatic === null
            ? "Automatic APU needs a departure time; enter a manual allowance if timing is unavailable."
            : `RECOMMENDED APU ${data.apu.automatic} K LBS · RATE ${data.apu.rate} K LBS/HR`;
        for (const tank of data.tank_rows) {
            tankGrid.querySelector(`[data-quick-planned="${tank.code}"]`).textContent = tank.planned ?? "—";
        }
        const values = {...data.totals, apu: data.apu.allowance};
        card.querySelectorAll("[data-quick-total]").forEach((node) => {
            const value = values[node.dataset.quickTotal];
            node.textContent = value === null || value === undefined ? "—" : String(value);
        });
        transferOutput.textContent = data.totals.transfer_gallons === null
            ? "TRANSFER FUEL —"
            : `TRANSFER FUEL ${data.totals.transfer_gallons.toLocaleString()} GAL`;
        issues.textContent = data.issues.length ? data.issues.join(" ") : "Calculations current. No operational data saved.";
    };

    const calculate = async () => {
        const current = ++sequence;
        if (!tailInput.value.trim()) {
            clearOutputs();
            form.querySelector("[data-quick-issues]").textContent = "Enter a tail number to begin.";
            return;
        }
        try {
            const response = await fetch(card.dataset.calculateUrl, {
                method: "POST",
                body: new FormData(form),
                headers: {"X-Requested-With": "XMLHttpRequest"},
                credentials: "same-origin",
                cache: "no-store",
            });
            if (!response.ok) throw new Error("Calculation unavailable. Check your access and retry.");
            const data = await response.json();
            if (current === sequence) show(data);
        } catch (error) {
            if (current === sequence) {
                clearOutputs();
                form.querySelector("[data-quick-issues]").textContent = error.message;
            }
        }
    };

    form.addEventListener("input", (event) => {
        if (event.target === tailInput) {
            const tail = tailInput.value.trim().toUpperCase();
            if (tail !== requestedTail) resetForTail(tail);
        } else sequence += 1;
        clearTimeout(timer);
        timer = setTimeout(calculate, 120);
    });
    form.addEventListener("change", () => {
        sequence += 1;
        clearTimeout(timer);
        timer = setTimeout(calculate, 0);
    });
    form.addEventListener("submit", (event) => event.preventDefault());
    form.querySelector("[data-quick-clear]").addEventListener("click", () => {
        resetForTail("");
        form.querySelector("[data-quick-issues]").textContent = "Enter a tail number to begin.";
        tailInput.focus();
    });
})();
