(() => {
    "use strict";

    const root = document.querySelector("[data-subzero-departure-board]");
    if (!root) return;

    const timezone = root.dataset.timezone || "America/Chicago";
    const operationalHHMM = () => {
        const parts = Object.fromEntries(
            new Intl.DateTimeFormat("en-US", {
                timeZone: timezone,
                hour: "2-digit",
                minute: "2-digit",
                hourCycle: "h23",
            }).formatToParts(new Date()).map((part) => [part.type, part.value])
        );
        return parts.hour + parts.minute;
    };

    const markDirty = (form) => {
        form.dataset.dirty = "true";
        root.dataset.dirty = "true";
    };

    const syncForm = (form) => {
        const plan = form.elements.treatment_plan?.value || "";
        const unlocked = form.dataset.unlocked === "true";
        const twoPass = ["two_type_i", "type_i_type_iv"].includes(plan);
        const pass1 = form.querySelector('[data-subzero-pass="1"]');
        const pass2 = form.querySelector('[data-subzero-pass="2"]');
        if (pass1) pass1.hidden = !plan;
        if (pass2) {
            pass2.hidden = !twoPass;
            pass2.classList.toggle("type-iv", plan === "type_i_type_iv");
            pass2.classList.toggle("type-i", plan !== "type_i_type_iv");
            const legend = pass2.querySelector("[data-subzero-pass2-type]");
            if (legend) {
                legend.textContent = plan === "type_i_type_iv"
                    ? "PASS 2 · TYPE IV"
                    : "PASS 2 · TYPE I";
            }
        }

        const enabled = {
            pass1_start: unlocked && Boolean(plan),
            pass1_end: unlocked && Boolean(plan) && Boolean(form.elements.pass1_start?.value),
            pass2_start: unlocked && twoPass && Boolean(form.elements.pass1_end?.value),
            pass2_end: unlocked && twoPass && Boolean(form.elements.pass2_start?.value),
        };
        Object.entries(enabled).forEach(([name, value]) => {
            const input = form.elements[name];
            if (input) input.readOnly = !value;
            const button = form.querySelector(`[data-subzero-deice-stamp="${name}"]`);
            if (button) button.hidden = !value;
        });
        form.querySelectorAll("select").forEach((select) => {
            select.disabled = !unlocked;
        });
    };

    root.querySelectorAll("[data-subzero-deice-form]").forEach((form) => {
        form.dataset.unlocked = form.dataset.locked === "true" ? "false" : "true";
        form.addEventListener("input", () => {
            markDirty(form);
            syncForm(form);
        });
        form.addEventListener("change", () => {
            markDirty(form);
            syncForm(form);
        });
        syncForm(form);
    });

    root.querySelectorAll("[data-subzero-operational-form]").forEach((form) => {
        form.addEventListener("input", () => {
            form.dataset.dirty = "true";
            root.dataset.dirty = "true";
        });
    });

    root.addEventListener("click", (event) => {
        const edit = event.target.closest("[data-subzero-edit]");
        if (edit) {
            const form = edit.closest("form");
            form.dataset.unlocked = "true";
            edit.hidden = true;
            form.querySelector('button[type="submit"]').hidden = false;
            syncForm(form);
            const mission = edit.closest("[data-subzero-mission]");
            mission?.querySelectorAll("[data-subzero-spray-control]").forEach((input) => {
                input.readOnly = input.dataset.sprayReady === "false";
            });
            mission?.querySelectorAll("[data-subzero-spray-save]").forEach((button) => {
                button.hidden = false;
            });
            return;
        }
        const stamp = event.target.closest("[data-subzero-deice-stamp]");
        if (stamp) {
            const form = stamp.closest("form");
            const input = form.elements[stamp.dataset.subzeroDeiceStamp];
            input.value = operationalHHMM();
            input.dispatchEvent(new Event("input", {bubbles: true}));
            return;
        }
        const reopen = event.target.closest("[data-subzero-reopen]");
        if (reopen) {
            const mission = reopen.closest("[data-subzero-mission]");
            mission.querySelector("[data-subzero-compact]").hidden = true;
            mission.querySelector("[data-subzero-workspace]").hidden = false;
            mission.dataset.reopened = "true";
            return;
        }
        const script = event.target.closest("[data-subzero-script]");
        if (script && script.dataset.subzeroScript) {
            root.querySelectorAll("[data-subzero-script-output]").forEach((output) => {
                output.textContent = script.dataset.subzeroScript;
            });
            root.querySelectorAll("[data-subzero-script]").forEach((button) => {
                button.setAttribute("aria-pressed", button === script ? "true" : "false");
            });
            return;
        }
        const toggle = event.target.closest("[data-subzero-script-toggle]");
        if (toggle) {
            const panel = root.querySelector("[data-subzero-script-panel]");
            panel.hidden = !panel.hidden;
            toggle.setAttribute("aria-expanded", panel.hidden ? "false" : "true");
        }
    });

    root.addEventListener("focusout", (event) => {
        const mission = event.target.closest("[data-subzero-mission][data-terminal='true']");
        if (!mission || mission.dataset.reopened !== "true") return;
        window.setTimeout(() => {
            if (mission.contains(document.activeElement) || mission.querySelector("[data-dirty='true']")) return;
            mission.querySelector("[data-subzero-compact]").hidden = false;
            mission.querySelector("[data-subzero-workspace]").hidden = true;
            mission.dataset.reopened = "false";
        }, 0);
    });

    root.querySelectorAll("[data-subzero-confirm]").forEach((form) => {
        form.addEventListener("submit", (event) => {
            if (!window.confirm(form.dataset.subzeroConfirm)) event.preventDefault();
        });
    });

    const refreshIntervalMs = Number(root.dataset.refreshIntervalMs);
    if (
        !window.NeoLiveUpdates
        || !root.dataset.revisionUrl
        || !Number.isFinite(refreshIntervalMs)
        || refreshIntervalMs < 1000
    ) return;
    let revision = root.dataset.revision;
    const controller = window.NeoLiveUpdates.create({
        intervalMs: refreshIntervalMs,
        continuousWhileVisible: true,
        poll: async () => {
            const url = new URL(root.dataset.revisionUrl, window.location.origin);
            url.searchParams.set("revision", revision);
            const response = await fetch(url, {cache: "no-store"});
            const payload = await response.json();
            controller.setServerStatus(payload.refresh || {});
            revision = payload.revision || revision;
            if (
                payload.changed
                && root.dataset.dirty !== "true"
                && !root.querySelector(":focus")
            ) {
                window.location.reload();
            }
        },
    });
    controller.setServerStatus(JSON.parse(root.dataset.refreshStatus || "{}"));
})();
