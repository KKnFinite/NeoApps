(() => {
    "use strict";
    const editor = document.querySelector("[data-vacation-union-editor]");
    if (editor) {
    const operationSelect = editor.querySelector("[data-vacation-operation-select]");
    const trees = [...editor.querySelectorAll("[data-vacation-operation-tree]")];
    const childChecks = (node) => [...node.querySelectorAll(":scope > ul [data-vacation-scope-check]")];
    const directChildNodes = (node) => [...node.querySelectorAll(":scope > ul > [data-vacation-scope-node]")];
    const refreshNode = (node) => {
        directChildNodes(node).forEach(refreshNode);
        const input = node.querySelector(":scope > label [data-vacation-scope-check]");
        const children = childChecks(node);
        if (!input || !children.length) return;
        const checkedCount = children.filter((child) => child.checked).length;
        input.indeterminate = checkedCount > 0 && checkedCount < children.length;
        if (checkedCount === children.length) input.checked = true;
        if (checkedCount === 0) input.checked = false;
    };
    const activeTree = () => trees.find((tree) => !tree.hidden);
    const refreshTree = (tree) => {
        const root = tree?.querySelector(":scope > .neostaffing-vacation-scope-tree > [data-vacation-scope-node]");
        if (root) refreshNode(root);
    };
    const selectOperation = () => {
        trees.forEach((tree) => {
            const selected = tree.dataset.vacationOperationTree === operationSelect.value;
            tree.hidden = !selected;
            tree.querySelectorAll("[data-vacation-scope-check]").forEach((input) => { input.disabled = !selected; });
            if (selected) refreshTree(tree);
        });
    };
    editor.addEventListener("change", (event) => {
        if (event.target === operationSelect) { selectOperation(); return; }
        const input = event.target.closest("[data-vacation-scope-check]");
        if (!input) return;
        const node = input.closest("[data-vacation-scope-node]");
        childChecks(node).forEach((child) => { child.checked = input.checked; child.indeterminate = false; });
        refreshTree(activeTree());
    });
    editor.querySelectorAll('[data-indeterminate="1"]').forEach((input) => { input.indeterminate = true; });
    selectOperation();
    }

    const sharing = document.querySelector("[data-vacation-sharing]");
    if (!sharing) return;
    const search = sharing.querySelector("[data-vacation-share-search]");
    const results = sharing.querySelector("[data-vacation-share-results]");
    const selected = sharing.querySelector("[data-vacation-share-selected]");
    let searchTimer = null;
    const selectedIds = () => new Set(
        [...selected.querySelectorAll('input[name="recipient_user_ids"]')].map((input) => input.value)
    );
    const addRecipient = (row) => {
        if (selectedIds().has(String(row.user_id))) return;
        selected.querySelector("small")?.remove();
        const label = document.createElement("label");
        const input = document.createElement("input");
        const text = document.createElement("span");
        input.type = "checkbox";
        input.name = "recipient_user_ids";
        input.value = row.user_id;
        input.checked = true;
        text.textContent = `${row.name} · ${row.employee_id}`;
        label.append(input, text);
        selected.append(label);
    };
    const renderResults = (rows) => {
        results.replaceChildren();
        rows.filter((row) => !selectedIds().has(String(row.user_id))).forEach((row) => {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = `${row.name} · ${row.employee_id}`;
            button.addEventListener("click", () => { addRecipient(row); button.remove(); });
            results.append(button);
        });
    };
    search.addEventListener("input", () => {
        clearTimeout(searchTimer);
        const query = search.value.trim();
        if (query.length < 2) { results.replaceChildren(); return; }
        searchTimer = setTimeout(async () => {
            try {
                const response = await fetch(`${sharing.dataset.searchUrl}?q=${encodeURIComponent(query)}`, { headers: { Accept: "application/json" } });
                if (!response.ok) throw new Error("search failed");
                renderResults((await response.json()).results || []);
            } catch (_error) {
                results.textContent = "Search unavailable.";
            }
        }, 180);
    });
})();
