(() => {
    "use strict";
    if (window.NeoSharedAlerts) return;
    const updateAlertTrayCount = (tray) => {
        const count = tray.querySelectorAll("[data-alert-unread='true']").length;
        tray.dataset.alertCount = String(count);
        const badge = tray.querySelector(".motherbrain-alert-count");
        if (badge) {
            badge.textContent = String(count);
        }
    };

    const markAlertReadInDocument = (alertId) => {
        document.querySelectorAll(`[data-alert-id="${CSS.escape(String(alertId))}"]`)
            .forEach((item) => {
                item.dataset.alertUnread = "false";
                item.classList.remove("is-unread");
                item.classList.add("is-read");
            });
        document.querySelectorAll("[data-my-alerts-tray]").forEach(updateAlertTrayCount);
    };

    const markAlertRead = async (item) => {
        const endpoint = item.dataset.alertReadUrl;
        const alertId = item.dataset.alertId;
        if (!endpoint || !alertId || item.dataset.alertReadPending === "true") {
            return;
        }
        item.dataset.alertReadPending = "true";
        try {
            const response = await fetch(endpoint, {
                method: "POST",
                cache: "no-store",
                credentials: "same-origin",
                headers: {"Accept": "application/json"},
            });
            if (response.ok) {
                markAlertReadInDocument(alertId);
            }
        } catch (_error) {
            // Reading an alert is best-effort; the unread state remains visible.
        } finally {
            item.removeAttribute("data-alert-read-pending");
        }
    };

    const bindAlertTray = (tray) => {
        if (!tray || tray.dataset.alertReadBound === "true") {
            return;
        }
        tray.dataset.alertReadBound = "true";
        tray.addEventListener("toggle", () => {
            if (!tray.open) {
                return;
            }
            tray.querySelectorAll(
                "[data-alert-unread='true'][data-alert-read-url]"
            ).forEach((item) => markAlertRead(item));
        });
    };

    const bindAlertTrays = (root = document) => {
        root.querySelectorAll("[data-my-alerts-tray]").forEach(bindAlertTray);
    };

    const reconcileAlertTrays = (html) => {
        if (typeof html !== "string") {
            return;
        }
        const parsed = new DOMParser().parseFromString(html, "text/html");
        const incoming = parsed.querySelector("[data-my-alerts-tray]");
        if (!incoming) {
            return;
        }
        document.querySelectorAll("[data-my-alerts-tray]").forEach((current) => {
            const replacement = incoming.cloneNode(true);
            replacement.open = current.open;
            current.replaceWith(replacement);
            bindAlertTray(replacement);
        });
    };


    window.NeoSharedAlerts = Object.freeze({bindAlertTrays, reconcileAlertTrays});
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => {bindAlertTrays();});
    else {bindAlertTrays();}
})();
