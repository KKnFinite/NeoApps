/* Apply authorized server-rendered Rain content without navigating the document. */
(() => {
    "use strict";
    const mission = "[data-neorain-mission-id], [data-neorain-delay-mission-id]";
    const dirty = [
        "rain-override-dirty", "neorain-dirty", "neorain-saving", "neorain-no-return-saving",
        "neorain-late-inclusion-saving", "neorain-service-dirty", "neorain-service-saving",
        "neorain-delay-dirty", "neorain-delay-saving",
    ].map(name => `[data-${name}="true"]`).join(",");
    const missionId = el => el?.dataset.neorainMissionId || el?.dataset.neorainDelayMissionId;
    function editing(root) {
        const protectedIds = new Set();
        for (const el of root.querySelectorAll(dirty)) {
            const id = missionId(el.closest(mission));
            if (id) protectedIds.add(id);
        }
        return protectedIds;
    }
    function key(el) {
        if (el.nodeType !== 1) return "";
        if (el.id) return "#" + el.id;
        if (el.matches("[data-rain-reset-tools]")) return "reset-tools";
        if (el.matches(".neorain-board-content")) return "board-content";
        if (el.hasAttribute("data-rainrock-flight")) return "report:" + el.dataset.rainrockFlight;
        if (missionId(el)) return "flight:" + missionId(el);
        if (el.matches("input,textarea,select")) return el.tagName + ":" + el.name;
        if (el.matches(".neorain-delay-existing-row")) {
            return el.querySelector("[name=note_id], [name=delay_id]")?.value || "";
        }
        return "";
    }
    function localAttribute(el, name) {
        if (name === "open" && el.tagName === "DETAILS") return true;
        if (name === "aria-expanded") return true;
        return name === "hidden" && el.matches("[data-neorain-delay-panel], [data-neorain-mobile-inline-detail], [data-neorain-board-support]");
    }
    function sync(node, next, hold) {
        if (node.nodeType !== 1) {
            if (node.nodeValue !== next.nodeValue) node.nodeValue = next.nodeValue;
            return;
        }
        if (hold(node) || node.matches("[data-rain-reset-tools]")) return;
        const expanded = node.classList.contains("is-expanded");
        for (const name of new Set([...node.getAttributeNames(), ...next.getAttributeNames()])) {
            if (localAttribute(node, name)) continue;
            const value = next.getAttribute(name);
            if (node.getAttribute(name) === value) continue;
            if (value === null) node.removeAttribute(name);
            else node.setAttribute(name, value);
        }
        if (expanded) node.classList.add("is-expanded");
        if (node.firstChild || next.firstChild) children(node, next, hold);
        if (node.matches("input,textarea,select") && node.value !== next.value) node.value = next.value;
        if (node.matches("input[type=checkbox]")) node.checked = next.checked;
    }
    // Keys are local to each list. A reordered departure never inherits another flight's editor.
    function children(parent, incoming, hold) {
        const keyed = new Map(Array.from(parent.childNodes, el => [key(el), el]));
        let cursor = parent.firstChild;
        for (const next of Array.from(incoming.childNodes)) {
            const id = key(next);
            let node = id ? keyed.get(id) : (cursor && !key(cursor) ? cursor : null);
            if (id) keyed.delete(id);
            if (node?.nodeName !== next.nodeName) node = null;
            if (!node) {
                parent.insertBefore(next.cloneNode(true), cursor);
                continue;
            }
            if (node !== cursor) {
                if (node.contains(document.activeElement)) {
                    // Move its preceding siblings instead. Detaching a focused HHMM
                    // editor would fire blur/autosave on browsers without moveBefore.
                    const after = node.nextSibling;
                    while (cursor !== node) {
                        const prior = cursor;
                        cursor = prior.nextSibling;
                        parent.insertBefore(prior, after);
                    }
                } else parent.insertBefore(node, cursor);
            }
            sync(node, next, hold);
            cursor = node.nextSibling;
        }
        while (cursor) {
            const rest = cursor.nextSibling;
            if (!hold(cursor) && !(cursor.nodeType === 1 && Array.from(cursor.querySelectorAll(mission)).some(hold))) cursor.remove();
            cursor = rest;
        }
    }
    const parse = html => {
        const template = document.createElement("template");
        template.innerHTML = html;
        return template.content;
    };
    function start(root, initial) {
        const board = root.dataset.neorainBoardPage;
        const field = board + "Revision";
        const content = root.querySelector("[data-neorain-live-content]");
        const status = root.querySelector("[data-neorain-refresh-status]");
        let refresh = initial;
        let pendingHtml;
        let editGeneration = 0;
        const edited = () => { editGeneration++; };
        for (const type of ["input", "change", "submit", "click", "neorain:revision", "neorain:inbound-revision"]) {
            root.addEventListener(type, edited, true);
        }
        const withinSort = () => refresh.sort_start_ms <= Date.now() && Date.now() < refresh.sort_end_ms;
        const controller = window.NeoLiveUpdates.create({
            intervalMs: refresh.live_screen_refresh_interval_ms,
            continuousWhileVisible: true,
            poll: async () => {
                if (!withinSort()) { controller.setEnabled(false); return; }
                const url = new URL(root.dataset.revisionUrl, location.origin);
                url.searchParams.set("revision", root.dataset[field]);
                url.searchParams.set("content", "1");
                const before = editGeneration;
                const held = editing(root);
                try {
                    const response = await (window.NeoLiveRevision?.fetch || fetch)(url, {cache:"no-store", credentials:"same-origin"});
                    if (!response.ok || response.redirected) throw new Error("Refresh unavailable.");
                    const payload = await response.json();
                    if (!withinSort()) { controller.setEnabled(false); return; }
                    refresh = payload.refresh || {};
                    controller.setServerStatus({...refresh, auto_refresh_enabled: withinSort()});
                    if (!withinSort() || !refresh.auto_refresh_enabled || before !== editGeneration) return;
                    if (payload.changed && !payload.html) throw new Error("Board content unavailable.");
                    if (payload.changed) pendingHtml = payload.html;
                    if (pendingHtml) {
                        const fragment = parse(pendingHtml);
                        for (const id of editing(root)) held.add(id);
                        let deferred = false;
                        const hold = el => {
                            if (el.nodeType !== 1) return false;
                            const busy = held.has(missionId(el)) || el.matches("[data-neorain-crew-dirty=true]") ||
                                (el.matches(".neorain-mission-tools, [data-neorain-crew-collapse]") &&
                                 (el.contains(document.activeElement) || el.matches("[data-tools-edited]")));
                            if (busy) deferred = true;
                            return busy;
                        };
                        children(content, fragment, hold);
                        if (deferred) root.dataset.neorainRefreshDeferred = "true";
                        else delete root.dataset.neorainRefreshDeferred;
                        if (!deferred) pendingHtml = null;
                        root.dataset[field] = payload.revision;
                    }
                    status.hidden = true;
                } catch (error) {
                    status.textContent = "Live update unavailable. Showing the last received board.";
                    status.hidden = false;
                    throw error;
                }
            },
        });
        root.addEventListener("input", event => {
            event.target.closest(".neorain-mission-tools")?.setAttribute("data-tools-edited", "");
        });
        controller.setServerStatus({...refresh, auto_refresh_enabled: Date.now() < refresh.sort_end_ms});
    }
    window.NeoRainBoard = {start, updateReport: (root, html) => children(root, parse(html), () => false)};
})();
