(() => {
    "use strict";
    const save = document.querySelector("[data-master-save]");
    if (!save) return;
    const status = document.querySelector("[data-master-status]");
    const error = document.querySelector("[data-master-error]");
    const forms = document.querySelectorAll("[data-master-board-form]");
    const dirty = new Map();
    const baseline = new WeakMap();
    const quietMs = 5000;
    const maximumMs = 30000;
    const manual = new Set();
    const editedDuringSave = new Set();
    let saving = new Set();
    let timer, busy = false, activeCommand = null, action = null, failed = false, conflict = false;
    let actionControlsLocked = false;

    const indexOf = row => row.querySelector("[name=row_indexes]").value;
    const prefixOf = row => "row_" + indexOf(row) + "_";
    const field = (row, key) => row.querySelector('[name="' + prefixOf(row) + key + '"]');
    const isNew = row => !field(row, "id").value;
    const flight = row => field(row, "flight_number").value || "New flight";

    function snapshot(row) {
        const values = {};
        const prefix = prefixOf(row);
        for (const control of row.querySelectorAll("input,select")) {
            if (!control.name.startsWith(prefix)) continue;
            const key = control.name.slice(prefix.length);
            values[key] ??= [];
            if (control.type !== "checkbox" || control.checked) values[key].push(control.value);
        }
        return values;
    }

    const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
    function initRow(row) {
        window.NeoAppsTimeInputs.initialize(row);
        baseline.set(row, snapshot(row));
        for (const button of row.querySelectorAll("button[type=submit]")) button.disabled = actionControlsLocked;
    }
    function mark(row) {
        const now = performance.now();
        const newBatch = saving.has(row) && !editedDuringSave.has(row);
        if (saving.has(row)) editedDuringSave.add(row);
        dirty.set(row, {first: newBatch ? now : dirty.get(row)?.first ?? now, last: now});
    }
    function ready(row) {
        const airport = field(row, field(row, "mission_type").value === "arrival" ? "origin" : "destination");
        return field(row, "flight_number").value.trim() && airport.value.trim().length === 3
            && field(row, "planned_time_local_hour").value && field(row, "planned_time_local_minute").value
            && [...row.querySelectorAll("input,select")].every(input => input.validity.valid);
    }
    function arm() {
        clearTimeout(timer);
        status.textContent = busy ? "Saving" : failed ? "Save failed" : dirty.size || action ? "Unsaved changes" : "Saved";
        save.disabled = conflict || (!dirty.size && !action);
        const locked = Boolean(action || activeCommand || conflict);
        if (locked !== actionControlsLocked) {
            actionControlsLocked = locked;
            for (const form of forms) {
                for (const button of form.querySelectorAll("tbody button[type=submit]")) button.disabled = locked;
            }
        }
        if (busy || failed || conflict) return;
        if (action || manual.size) {
            timer = setTimeout(run, 0);
            return;
        }
        const pending = [...dirty].filter(([row]) => !isNew(row) && ready(row));
        if (!pending.length) return;
        const quiet = Math.max(...[...dirty.values()].map(x => x.last)) + quietMs;
        const oldest = Math.min(...pending.map(([,x]) => x.first)) + maximumMs;
        timer = setTimeout(run, Math.max(0, Math.min(quiet, oldest) - performance.now()));
    }

    function fromHTML(html) {
        const body = document.createElement("tbody");
        body.innerHTML = html;
        if (!body.firstElementChild?.matches(".master-board-row")) throw Error("Missing saved flight. Review the schedule.");
        return body.firstElementChild;
    }
    function replaceRow(row, replacement, original, keepEdits) {
        const local = snapshot(row);
        const focusName = row.contains(document.activeElement) ? document.activeElement.name : null;
        row.replaceWith(replacement);
        // Removing a focused text control can synchronously dispatch change.
        // Retire the old row after that event, before queuing its successor.
        dirty.delete(row);
        manual.delete(row);
        initRow(replacement);
        // Preserve changes made after submission. Each time is one logical field.
        const changed = key => !same(local[key], original[key]);
        for (const input of replacement.querySelectorAll("input,select")) {
            const key = input.name.slice(prefixOf(replacement).length);
            if (!keepEdits || !(key in local) || ["id","mission_type","sort_name"].includes(key)) continue;
            const time = key.match(/^(.*)_(hour|minute)$/);
            if (!(time ? changed(time[1] + "_hour") || changed(time[1] + "_minute") : changed(key))) continue;
            if (input.type === "checkbox") {
                const checked = local[key].includes(input.value);
                if (checked !== original[key].includes(input.value)) input.checked = checked;
            }
            else input.value = local[key][0] ?? "";
        }
        if (!same(snapshot(replacement), baseline.get(replacement))) mark(replacement);
        if (focusName) [...replacement.querySelectorAll("input,select")].find(i => i.name === focusName)?.focus();
    }
    function showConflict(row, result) {
        conflict = true;
        const replacement = fromHTML(result.current_row);
        const saved = snapshot(replacement);
        const original = baseline.get(row);
        const current = snapshot(row);
        const details = document.createElement("p");
        details.textContent = Object.keys(saved).filter(k => !same(saved[k], original[k]) || !same(current[k], saved[k]))
            .map(k => k.replaceAll("_", " ") + " (saved: " + (saved[k].join(", ") || "blank")
                + ", draft: " + (current[k]?.join(", ") || "blank") + ")").join(" · ");
        error.append(details);
        for (const [label, keep] of [["Keep my edited fields",true],["Use saved flight",false]]) {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = label;
            button.onclick = () => {
                replaceRow(row, replacement, original, keep);
                failed = conflict = false;
                error.replaceChildren();
                arm();
            };
            error.append(button);
        }
    }
    async function post(rows, command) {
        const body = new URLSearchParams();
        const sent = new Map();
        const form = rows[0].closest("form");
        const token = form.querySelector("[name=csrf_token]");
        if (token) body.set("csrf_token", token.value);
        body.set("queue_save", "1");
        body.set("board_mission_type", command ? form.dataset.masterBoardType : "mixed");
        for (const row of rows) {
            const values = snapshot(row);
            sent.set(row, values);
            body.append("row_indexes", indexOf(row));
            for (const [key, entries] of Object.entries(values)) {
                for (const entry of entries) body.append(prefixOf(row) + key, entry);
            }
            const version = row.querySelector("[name^=version_]");
            if (version) body.set(version.name, version.value);
        }
        if (command?.button.name) body.set(command.button.name, command.button.value);
        const url = command?.button.getAttribute("formaction") || form.action;
        const response = await fetch(url, {method:"POST", body,
            headers:{"Accept":"application/json", "X-Requested-With":"fetch"}});
        if (response.redirected || response.status === 403) throw Error("Check your sign-in and editing permission. Your edits remain here.");
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.ok) {
            const message = result.message || "Save could not be confirmed. Your edits remain here.";
            const failure = new Error(message);
            failure.result = result;
            throw failure;
        }
        for (const [row, values] of sent) {
            if (command?.button.matches(".table-row-delete-button")) {
                if (String(result.deleted) !== field(row, "id").value) throw Error("Deletion was not confirmed.");
                row.remove();
                dirty.delete(row);
            } else if (isNew(row)) {
                replaceRow(row, fromHTML(result.created_row), values, true);
            } else {
                const version = result.versions?.[field(row, "id").value];
                if (!version) throw Error("The server did not confirm the flight version. Your edits remain here.");
                row.querySelector("[name^=version_]").value = version;
                if (command) {
                    command.button.textContent = result.active ? "Active" : "Inactive";
                    command.button.value = result.active ? "0" : "1";
                    command.button.setAttribute("aria-pressed", String(result.active));
                } else {
                    baseline.set(row, values);
                    if (same(snapshot(row), values)) dirty.delete(row);
                }
            }
        }
        if (command) {
            const type = form.dataset.masterBoardType;
            document.querySelector('[href="#master-' + type + 's"] b').textContent =
                form.querySelectorAll("tbody .master-board-row:not(.master-board-add-row)").length;
        }
    }
    async function run() {
        if (busy || failed || conflict) return;
        for (const row of manual) if (!dirty.has(row)) manual.delete(row);
        const existing = [...dirty.keys()].filter(row => !isNew(row));
        let command = action;
        if (command?.button.matches(".master-active-control") && dirty.has(command.row)) command = null;
        if (!command && manual.size && !existing.length) {
            const row = manual.values().next().value;
            if (row) command = {row, button:row.querySelector(".master-row-save-button")};
        }
        let rows = command ? [command.row] : action ? [action.row] : existing;
        const invalid = rows.find(row => !ready(row));
        if (invalid && (manual.size || action) && !command?.button.matches(".table-row-delete-button")) {
            failed = true;
            action = null;
            manual.clear();
            error.textContent = "Complete the required fields for " + flight(invalid) + ". Your edits remain here.";
            arm();
            return;
        }
        if (!command) rows = rows.filter(ready);
        if (!rows.length) { manual.clear(); arm(); return; }
        busy = true;
        saving = new Set(rows);
        activeCommand = command;
        for (const row of rows) manual.delete(row);
        if (command?.button.matches(".table-row-delete-button")) command.row.inert = true;
        arm();
        try {
            await post(rows, command);
            if (command === action) action = null;
        } catch (failure) {
            failed = true;
            action = null;
            manual.clear();
            error.textContent = failure.message;
            const result = failure.result;
            const row = rows.find(r => field(r,"id").value === String(result?.conflict_id));
            if (row && result.current_row) showConflict(row, result);
            if (row && result.unavailable) {
                conflict = true;
                const discard = document.createElement("button");
                discard.type = "button";
                discard.textContent = "Discard unavailable flight draft";
                discard.onclick = () => {
                    dirty.delete(row); row.remove(); error.replaceChildren();
                    conflict = failed = false; arm();
                };
                error.append(discard);
            }
        } finally {
            if (command) command.row.inert = false;
            activeCommand = null;
            saving.clear();
            editedDuringSave.clear();
            busy = false;
            arm();
        }
    }
    function requestSave(command = null) {
        if (conflict || (command && (action || command.row === activeCommand?.row))) return;
        failed = false;
        error.replaceChildren();
        if (command) action = command;
        else for (const row of dirty.keys()) manual.add(row);
        arm();
    }
    save.addEventListener("click", () => requestSave());
    window.addEventListener("beforeunload", event => {
        if (dirty.size || busy || action) { event.preventDefault(); event.returnValue = ""; }
    });
    for (const form of forms) {
        form.querySelectorAll("tbody tr").forEach(initRow);
        let nextIndex = 0;
        form.querySelector("[data-master-add-row]").onclick = () => {
            const row = fromHTML(form.querySelector("template").innerHTML.replaceAll("__INDEX__", String(nextIndex++)));
            form.querySelector("tbody").append(row);
            initRow(row);
            row.querySelector("input[type=text]").focus();
        };
        function edit(event) {
            if (!event.target.matches("input:not([type=hidden]),select")) return;
            const row = event.target.closest("tr");
            if (event.target.matches(".airport-code-input,.flight-number-input"))
                event.target.value = event.target.value.toUpperCase();
            if (!saving.has(row) && same(snapshot(row), baseline.get(row))) dirty.delete(row);
            else mark(row);
            arm();
        }
        form.addEventListener("input", edit);
        form.addEventListener("change", edit);
        form.addEventListener("keydown", event => {
            if (event.key === "Enter" && event.target.matches("input[type=text]")) {
                event.preventDefault();
                event.target.blur();
                requestSave();
            }
        });
        form.addEventListener("submit", event => {
            event.preventDefault();
            const button = event.submitter;
            if (button) requestSave({row:button.closest("tr"), button});
        });
    }
})();
