(() => {
    'use strict';
    const roster = document.querySelector('[data-attendance-roster]');
    if (!roster) return;

    const search = roster.querySelector('[data-attendance-search]');
    const clear = roster.querySelector('[data-attendance-clear]');
    const matches = roster.querySelector('[data-attendance-matches]');
    const empty = roster.querySelector('[data-attendance-empty]');
    const allHere = roster.querySelector('[data-attendance-all]');
    const bulkResult = roster.querySelector('[data-attendance-bulk-result]');
    const scope = new URLSearchParams(Array.from(roster.querySelectorAll(':scope > input[type="hidden"]'),
        input => [input.name, input.value]));
    const counts = document.querySelectorAll('[data-attendance-count]');
    const rows = new Map();
    let bulkBusy = false;

    for (const element of roster.querySelectorAll('[data-attendance-person]')) {
        const id = element.dataset.attendancePerson;
        const status = element.querySelector('select');
        const note = element.querySelector('input[name^="note_"]');
        const original = element.querySelector('input[name^="original_"]');
        rows.set(id, {
            id, element, status, note, original,
            editable: Boolean(status && !status.disabled),
            saved: {status: element.dataset.attendanceStatus, note: note.value, original: original.value},
            searchText: `${element.dataset.attendanceName} ${element.dataset.attendanceEmployeeId}`.toLowerCase(),
            output: element.querySelector('[data-attendance-state]'),
            retry: element.querySelector('[data-attendance-retry]'),
            timer: null, request: null, blocked: false,
        });
    }

    function filterRows() {
        const tokens = search.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
        let count = 0;
        for (const row of rows.values()) {
            const visible = tokens.every(token => row.searchText.includes(token));
            row.element.hidden = !visible;
            if (visible) count++;
        }
        clear.hidden = !search.value;
        matches.textContent = `${count} ${count === 1 ? 'match' : 'matches'}`;
        empty.hidden = count !== 0;
        empty.cells[0].textContent = tokens.length
            ? 'No employees match this search in the selected scope.'
            : 'No assigned employees match this attendance scope.';
    }

    function updateCounts() {
        const totals = new Map();
        for (const {saved} of rows.values()) totals.set(saved.status, (totals.get(saved.status) || 0) + 1);
        for (const count of counts) count.textContent = totals.get(count.dataset.attendanceCount) || 0;
    }

    function state(row, kind, message) {
        row.output.dataset.state = kind;
        row.output.textContent = message;
        row.retry.hidden = kind !== 'error';
    }

    const draft = row => ({status: row.status.value, note: row.note.value.trim()});
    const differs = (a, b) => a.status !== b.status || a.note !== b.note;

    function reconcile(row, authoritative, compared) {
        const current = draft(row);
        if (current.status === compared.status && current.status !== authoritative.status) {
            row.status.value = authoritative.status;
        }
        if (current.note === compared.note && current.note !== authoritative.note) {
            row.note.value = authoritative.note;
        }
        row.saved = authoritative;
        row.original.value = authoritative.original;
    }

    async function post(body) {
        const response = await fetch(roster.dataset.saveUrl, {
            method: 'POST', credentials: 'same-origin', headers: {'Accept': 'application/json'}, body,
        });
        const result = await response.json().catch(() => null);
        if (!response.ok || !Array.isArray(result?.rows)) {
            throw Object.assign(new Error(result?.error || 'Unable to save attendance. Check your connection or sign-in.'),
                {current: response.status === 409 ? result?.row : null});
        }
        return result;
    }

    function saveRow(row) {
        if (bulkBusy || row.request || row.blocked || row.timer) return;
        const sent = draft(row);
        if (!differs(sent, row.saved)) {
            if (row.output.dataset.state === 'saving') state(row, 'saved', 'Saved');
            return;
        }
        state(row, 'saving', 'Saving…');
        const body = new URLSearchParams(scope);
        body.set(`status_${row.id}`, sent.status);
        body.set(`note_${row.id}`, sent.note);
        body.set(`original_${row.id}`, row.saved.original);
        row.request = post(body).then(({rows: [authoritative]}) => {
            reconcile(row, authoritative, sent);
            updateCounts();
            state(row, 'saved', 'Saved');
        }).catch(error => {
            row.blocked = true;
            if (error.current) {
                reconcile(row, error.current, row.saved);
                updateCounts();
                const currentStatus = Array.from(row.status.options).find(option => option.value === error.current.status);
                state(row, 'error', `Error: Changed by another supervisor. Current: ${currentStatus?.text || error.current.status}${error.current.note ? ` · ${error.current.note}` : ''}. Retry to apply your edit.`);
            } else {
                state(row, 'error', `Error: ${error.message}`);
            }
        }).finally(() => {
            row.request = null;
            if (differs(draft(row), row.saved)) saveRow(row);
        });
    }

    function queue(row, delay = 0) {
        clearTimeout(row.timer);
        row.timer = null;
        if (row.blocked) return;
        if (differs(draft(row), row.saved)) state(row, 'saving', 'Saving…');
        if (delay) {
            row.timer = setTimeout(() => {
                row.timer = null;
                saveRow(row);
            }, delay);
        } else {
            saveRow(row);
        }
    }

    for (const row of rows.values()) {
        if (!row.editable) continue;
        row.status.addEventListener('change', () => queue(row));
        row.note.addEventListener('input', () => queue(row, 500));
        row.note.addEventListener('blur', () => queue(row));
        row.retry.addEventListener('click', () => {
            row.blocked = false;
            state(row, 'saving', 'Saving…');
            queue(row);
        });
    }

    search.addEventListener('input', filterRows);
    clear.addEventListener('click', () => {
        search.value = '';
        filterRows();
        search.focus();
    });
    filterRows();

    allHere?.addEventListener('click', async () => {
        if (bulkBusy) return;
        bulkBusy = true;
        allHere.disabled = true;
        bulkResult.hidden = false;
        bulkResult.textContent = 'Saving…';
        try {
            await Promise.all(Array.from(rows.values(), row => row.request));
            // Search only controls visibility. Signed candidates cover the entire authorized scope.
            const candidates = Array.from(rows.values()).filter(row => row.editable && !row.saved.status);
            if (!candidates.length) {
                bulkResult.textContent = 'No unmarked employees in this scope.';
                return;
            }
            const body = new URLSearchParams(scope);
            body.set('bulk_status', 'here');
            for (const row of candidates) {
                body.set(`status_${row.id}`, '');
                body.set(`original_${row.id}`, row.saved.original);
            }
            const result = await post(body);
            for (const authoritative of result.rows) {
                const row = rows.get(String(authoritative.person_id));
                reconcile(row, authoritative, row.saved);
            }
            updateCounts();
            bulkResult.textContent = `${result.saved} marked Here.`;
        } catch (error) {
            bulkResult.textContent = `Error: ${error.message}`;
        } finally {
            allHere.disabled = false;
            bulkBusy = false;
            for (const row of rows.values()) if (row.editable) saveRow(row);
        }
    });
})();
