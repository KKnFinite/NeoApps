(() => {
    "use strict";
    const editor = document.querySelector("[data-vacation-union-editor]");
    if (!editor) return;
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
})();
