(() => {
    'use strict';
    const root = document.querySelector('[data-ops-workspace]');
    if (!root) return;
    const shared = window.NeoSubZeroDepartureForms;
    const feedback = root.querySelector('[data-ops-feedback]');
    const media = window.matchMedia('(max-width:900px)');
    const primaryRamps = ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo'];
    let selected = new URL(location.href).searchParams.get('mission');
    const isPretreat = root.dataset.mode === 'pretreat';
    const desktopInline = () => !isPretreat && !media.matches && root.dataset.layout !== 'visual';
    const needsReload = message => /changed|conflict|stale|session|current sort|identity|network|timed out|could not be confirmed|expired|restored.*draft|another.*(?:inspection|coordinator|saved|user)/i.test(message);
    const accordion = row => row.querySelector('[data-ops-accordion]');
    const dirtyForm = 'form[data-dirty="true"]';
    let epoch = 0, busy = false, preferenceBusy = false;
    let controller = null;
    const navigationAbort = new AbortController();
    const stopLive = () => { navigationAbort.abort(); controller?.destroy(); };
    let revision = root.dataset.revision;
    const searchInput = root.querySelector('[data-ops-search]');
    const searchClear = root.querySelector('[data-ops-search-clear]');
    const searchEmpty = root.querySelector('[data-ops-search-empty]');
    const normalizeSearch = value => value.trim().toLowerCase().replace(/\s+/g,' ');
    const filterPretreat = () => {
        if (!searchInput) return;
        const terms = normalizeSearch(searchInput.value).split(' ').filter(Boolean);
        const ramp = media.matches ? root.dataset.rampOverride || root.dataset.ramp : 'All';
        let matches = 0;
        for (const row of root.querySelectorAll('[data-ops-row]')) {
            const text = normalizeSearch(row.dataset.opsSearchText || '');
            row.hidden = (ramp !== 'All' && row.dataset.ramp !== ramp) || !terms.every(term => text.includes(term));
            if (!row.hidden) matches++;
        }
        searchClear.hidden = !searchInput.value;
        searchEmpty.hidden = !terms.length || matches > 0;
    };
    searchInput?.addEventListener('input', filterPretreat);
    searchClear?.addEventListener('click', () => {
        searchInput.value = '';
        searchInput.focus({preventScroll:true});
        filterPretreat();
    });
    const autosaveSelector = ':is([data-ops-pretreat-autosave],[data-ops-outbound-autosave])';
    const pendingPretreat = new Set();
    let reconcilingPretreat = false;
    const autosaveValues = form => new URLSearchParams([...new FormData(form)].filter(([name]) =>
        !['csrf_token', 'expected_version', 'record_id', 'record_version', 'correction_reason'].includes(name))).toString();
    const rememberAutosave = form => {
        if (form.matches(autosaveSelector) && form.dataset.savedValues === undefined) {
            form.dataset.savedValues = autosaveValues(form);
        }
    };
    const queuePretreat = form => {
        const correction = form.elements.correction_reason;
        if (form.dataset.opsCompleted === 'true' || correction?.value.trim() ||
            (form.matches('[data-ops-new-participant]') && !form.elements.truck_number.value)) return;
        pendingPretreat.add(form);
        setTimeout(drainPretreat,0);
    };
    const drainPretreat = () => {
        if (busy || preferenceBusy) return;
        for (const form of pendingPretreat) {
            pendingPretreat.delete(form);
            if (!form.isConnected || form.dataset.autosaveConflict === 'true') continue;
            if (form.matches('[data-ops-planned], [data-ops-pretreat-config]')) {
                const toggle = form.querySelector('[data-ops-quick-toggle]');
                if (toggle && toggle.checked === toggle.defaultChecked) continue;
                const button = form.querySelector('button[value="toggle_configured"]');
                if (button) form.requestSubmit(button);
                else form.requestSubmit();
                if (busy) break;
                continue;
            }
            const invalid = [...form.querySelectorAll('[data-clock-time]:not([disabled])')]
                .some(field => window.NeoAppsClockTime.normalizeInput(field) === null);
            if (invalid) {
                inlineMessage(inlineFor(form), 'Enter a valid time from 00:00 to 23:59. Your draft is kept.', true);
                continue;
            }
            if (autosaveValues(form) === form.dataset.savedValues) {
                if (form.dataset.dirty === 'true') {
                    delete form.dataset.dirty;
                    saveDraft();
                    inlineMessage(inlineFor(form), '');
                }
                continue;
            }
            const endedPassChanged = [1, 2].some(number => {
                const start = form.elements['pass' + number + '_start'];
                const end = form.elements['pass' + number + '_end'];
                return end?.defaultValue && (start.value !== start.defaultValue || end.value !== end.defaultValue);
            });
            if (endedPassChanged) {
                form.querySelector('[data-ops-disclosure="correction"]')?.setAttribute('open','');
                inlineMessage(inlineFor(form), 'Enter a correction reason and apply the audited correction. Your draft is kept.', true);
                continue;
            }
            form.requestSubmit();
            if (busy) break;
        }
    };
    const notify = (text, error = false) => {
        feedback.textContent = text; feedback.hidden = !text;
        feedback.classList.toggle('is-error', error);
    };
    const formAction = form => new URL(form.getAttribute('action'), location.origin).pathname;
    const formKey = form => [formAction(form), form.elements.pass_number?.value || '',
        form.elements.position_number?.value || '', form.elements.record_id?.value || '',
        form.matches('[data-ops-quick-clock]') ? 'quick-clock' :
            form.querySelector('input[name="action"]')?.value || form.querySelector('button[name="action"]')?.value || form.elements.inspection_action?.value || ''].join(':');
    const draftKey = detail => ['neoapps.subzero.operations.draft', root.dataset.userId,
        root.dataset.operationId, root.dataset.mode, detail.dataset.missionId, detail.dataset.tail].join(':');
    const draftContents = () => [...root.querySelectorAll('[data-ops-inline]')];
    const inlineFor = element => element.closest('[data-ops-inline]') ||
        element.closest('[data-ops-row]')?.querySelector('[data-ops-inline]') || null;
    const inlineMessage = (content, text, error = false) => {
        const line = content.querySelector('[data-ops-inline-feedback]');
        line.textContent = text;
        line.hidden = !text;
        line.classList.toggle('is-error', error);
        content.querySelector('[data-ops-reload-inline]').hidden = !error || (!isPretreat && !needsReload(text));
        const retry = content.querySelector('[data-ops-pretreat-retry]');
        if (retry) retry.hidden = true;
    };
    const dirty = () => !!root.querySelector('form[data-dirty="true"]');
    const saveDraft = () => {
        for (const content of draftContents()) {
            const forms = [...content.querySelectorAll('form[data-dirty="true"]')].map(form => ({
                key: formKey(form), unlocked: form.dataset.unlocked,
                fields: [...form.elements].filter(e => e.name && e.name !== 'csrf_token' && !['submit', 'button'].includes(e.type)).map(e => ({name:e.name, value:e.value, checked:e.checked}))
            }));
            try {
                if (forms.length) sessionStorage.setItem(draftKey(content), JSON.stringify(forms));
                else sessionStorage.removeItem(draftKey(content));
            } catch (_) {
                const message = 'Draft remains open. Browser storage is unavailable; do not reload until saved.';
                inlineMessage(content, message, true);
            }
        }
    };
    const sync = form => {
        if (form.matches('[data-subzero-deice-form]')) {
            if (!form.dataset.unlocked) form.dataset.unlocked = form.dataset.locked === 'true' ? 'false' : 'true';
            shared.syncForm(form);
            const correction = form.querySelector('[data-ops-correction-field]');
            if (correction) {
                const unlocked = form.dataset.unlocked === 'true';
                correction.hidden = !unlocked;
                form.querySelector('[data-subzero-edit]').hidden = unlocked;
                form.querySelector('button[type="submit"]').hidden = !unlocked;
            }
        } else if (form.matches('[data-ops-inline-treatment]')) {
            if (!form.dataset.unlocked) form.dataset.unlocked = form.matches(autosaveSelector) && form.dataset.opsCompleted !== 'true' ? 'true' : 'false';
            const unlocked = form.dataset.unlocked === 'true';
            const names = ['pass1_start', 'pass1_end', 'pass2_start', 'pass2_end'];
            names.forEach((name, index) => {
                form.elements[name].readOnly = !unlocked || (index > 0 && !form.elements[names[index-1]].value);
            });

            const edit = form.querySelector('[data-subzero-edit]');
            const save = form.querySelector('[data-ops-apply-correction]');
            if (edit) edit.hidden = unlocked;
            if (save) save.hidden = !unlocked;
            if (form.elements.correction_reason) {
                form.elements.correction_reason.disabled = !unlocked;
            }
        }
    };
    const initialize = (restore = false, onlyFresh = false) => {
        for (const content of draftContents()) {
            const extra = content.querySelector('.ops-outbound-extra');
            if (extra && !desktopInline()) extra.open = true;
            content.querySelectorAll('form').forEach(form => { sync(form); rememberAutosave(form); });
            if (!restore) continue;
            let drafts = [];
            try { drafts = JSON.parse(sessionStorage.getItem(draftKey(content)) || '[]');
                drafts.forEach(draft => { draft.fields = draft.fields.filter(field => field.name !== 'csrf_token'); });
                if (drafts.length) sessionStorage.setItem(draftKey(content),JSON.stringify(drafts));
            } catch (_) { /* current DOM remains usable */ }
            const notesForm = isPretreat && content.querySelector('.ops-pretreat-notes form[data-ops-pretreat-autosave]');
            if (notesForm && !drafts.some(draft => draft.key === formKey(notesForm))) {
                const combinedDraft = drafts.find(draft => draft.key.endsWith(':save_treatment'));
                const savedNotes = combinedDraft?.fields.find(field => field.name === 'notes');
                if (savedNotes && savedNotes.value !== notesForm.elements.notes.value) {
                    drafts.push({key: formKey(notesForm), fields: combinedDraft.fields.filter(field =>
                        ['notes', 'expected_version'].includes(field.name))});
                }
            }
            let restored = false;
            for (const draft of drafts) {
                const form = [...content.querySelectorAll('form')].find(e => formKey(e) === draft.key);
                if (!form || (!form.matches(autosaveSelector) && !form.querySelector('button[type="submit"],button[name="action"]')) ||
                    (onlyFresh && form.dataset.dirty === 'true')) continue;
                restored = true;
                form.dataset.unlocked = draft.unlocked || form.dataset.unlocked;
                draft.fields.forEach(saved => {
                    const field = form.elements[saved.name];
                    if (saved.name === 'csrf_token' || !field || !('value' in field)) return;
                    if (field.tagName === 'SELECT' && saved.value && ![...field.options].some(option=>option.value===saved.value)) {
                        const option=document.createElement('option');option.value=saved.value;option.textContent='PREVIOUS SELECTION ('+saved.value+') — REVIEW';field.append(option);
                    }
                    field.value = saved.value;
                    if (['checkbox', 'radio'].includes(field.type)) field.checked = saved.checked;
                });
                form.dataset.dirty = 'true'; sync(form);
                if (form.matches('[data-ops-new-participant]')) {
                    form.hidden = false;
                    form.parentElement.querySelector('[data-ops-add-participant]').hidden = true;
                }
                if (form.matches('.ops-pretreat-participant') && form.elements.correction_reason?.value) {
                    form.querySelector('[data-ops-participant-correction]').hidden = false;
                    form.elements.correction_reason.disabled = false;
                }
                const disclosure = form.closest('[data-ops-disclosure]');
                if (disclosure) disclosure.open = true;
                if (draft.fields.some(field => field.name === 'notes' || field.name === 'correction_reason')) {
                    const notes = form.querySelector('[data-ops-disclosure="notes"]');
                    if (notes) notes.open = true;
                }
            }
            if (restored) {
                const message = 'RESTORED UNSAVED DRAFT · Original version retained. Resolve any server conflict before saving.';
                content.closest('[data-ops-accordion]').open = true;
                inlineMessage(content, message, true);
            }
        }
    };
    const applyLayout = () => {
        const wasInline = desktopInline();
        const board = root.querySelector('[data-ops-board]');
        const rows = [...board.querySelectorAll('[data-ops-row]')];
        // Preserve global server ordering while moving rows into/out of columns.
        rows.sort((a,b) => Number(a.dataset.order) - Number(b.dataset.order));
        rows.forEach(row => board.append(row));
        board.querySelectorAll('.ops-ramp-column').forEach(e => e.remove());
        root.dataset.layout = (isPretreat || media.matches) ? 'list' : (root.dataset.desktopLayout || 'list');
        const visual = root.dataset.layout === 'visual';
        if (visual && wasInline) rows.forEach(row => {
            const card = accordion(row);
            card.open = card.contains(document.activeElement) || !!card.querySelector(dirtyForm) || row.dataset.missionId === selected;
        });
        if (desktopInline()) rows.forEach(row => { accordion(row).open = true; });
        if (!isPretreat) rows.forEach(row => {
            const extra = row.querySelector('.ops-outbound-extra');
            if (extra) {
                extra.open = !desktopInline() || row.dataset.missionId === selected || !!extra.querySelector(dirtyForm);
                row.querySelector('[data-ops-expand-details]')?.setAttribute('aria-expanded',String(extra.open));
                const home = row.querySelector('[data-ops-reason-home]');
                const reason = row.querySelector('[data-ops-row-reason]');
                const reasonSlot = !media.matches ? home : extra.querySelector('[data-ops-reason-detail]') || home;
                if (reason && reason.parentElement !== reasonSlot) reasonSlot.append(reason);
                const participation = extra.querySelector('.ops-compact-participation');
                const participationSlot = extra.querySelector(media.matches ? '[data-ops-participation-home]' : '[data-ops-participation-inline]');
                if (participation && participationSlot && participation.parentElement !== participationSlot) {
                    participationSlot.append(participation);
                    if (media.matches && participation.querySelector(dirtyForm)) {
                        participationSlot.closest('details').open = true;
                    }
                }
                for (const form of extra.querySelectorAll('[data-ops-remove-form]')) {
                    const slot = form.closest('[data-participant-id]')?.querySelector(media.matches ? '[data-ops-remove-home]' : '[data-ops-remove-inline]');
                    if (!slot || form.parentElement === slot) continue;
                    slot.append(form);
                    const disclosure = slot.closest('details');
                    if (disclosure && form.matches(dirtyForm)) disclosure.open = true;
                }
                const card = accordion(row);
                const summary = card.querySelector(':scope > summary');
                if (visual) summary.tabIndex = -1;
                else summary.removeAttribute('tabindex');
                summary.querySelector('[data-ops-ramp-chevron]')?.setAttribute('aria-expanded', String(card.open));
            }
        });
        const override = root.dataset.rampOverride;
        const ramp = (!isPretreat && override) || (media.matches ? override || root.dataset.ramp : 'All');
        const selector = root.querySelector('[data-ops-ramp]');
        if (![...selector.options].some(option => option.value === ramp)) selector.add(new Option(ramp.toUpperCase(), ramp));
        selector.value = ramp;
        if (visual) {
            const ramps = [...primaryRamps, ...['Remote','Location Review'].filter(r => r === ramp || rows.some(row => row.dataset.ramp === r))];
            ramps.forEach(name => {
                const column = document.createElement('section'); column.className = 'ops-ramp-column'; column.dataset.ramp = name;
                if (!primaryRamps.includes(name)) column.classList.add('is-extra');
                column.hidden = ramp !== 'All' && name !== ramp;
                const heading = document.createElement('h2'); heading.textContent = name.toUpperCase(); column.append(heading);
                rows.filter(row => row.dataset.ramp === name).forEach(row => { row.hidden = false; column.append(row); });
                if (!column.querySelector('[data-ops-row]:not([hidden])')) { const empty = document.createElement('p'); empty.textContent = 'No aircraft'; column.append(empty); }
                board.append(column);
            });
        } else rows.forEach(row => { row.hidden = ramp !== 'All' && row.dataset.ramp !== ramp; });
        filterPretreat();
        root.querySelectorAll('[data-ops-layout]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.opsLayout === root.dataset.layout)));
    };
    const tagOrder = () => root.querySelectorAll('[data-ops-row]').forEach((row,index) => { row.dataset.order = index; });
    const currentUrl = mission => {
        const url = new URL(root.dataset.workspaceUrl, location.origin);
        url.searchParams.set('mode',root.dataset.mode);
        if (mission) url.searchParams.set('mission',mission);
        if (root.dataset.rampOverride) url.searchParams.set('ramp',root.dataset.rampOverride);
        return url;
    };
    const requestedDrafts = () => {
        const prefix = draftKey({dataset:{missionId:'',tail:''}}).slice(0,-1);
        try {
            return [...Object.keys(sessionStorage)].filter(key => key.startsWith(prefix))
                .map(key => key.slice(prefix.length).split(':')[0]).filter(id => /^\d+$/.test(id));
        } catch (_) { return []; }
    };
    const inlineRequestUrl = mission => {
        const url = currentUrl(mission);
        if (media.matches) url.searchParams.set('presentation','inline');
        if (!isPretreat) new Set(requestedDrafts().slice(0,20)).forEach(id => url.searchParams.append('draft',id));
        return url;
    };
    const reconcileInline = (board, incoming, active, savedForm) => {
        const nextRows = new Map([...incoming.querySelectorAll('[data-ops-row]')].map(row => [row.dataset.missionId, row]));
        if (isPretreat && !media.matches) nextRows.forEach(row => { accordion(row).open = true; });
        for (const row of board.querySelectorAll('[data-ops-row]')) {
            const next = nextRows.get(row.dataset.missionId);
            const editor = accordion(row);
            const preserve = (editor.contains(active) && !editor.querySelector(':scope > summary').contains(active)) ||
                editor.querySelector(dirtyForm) || (isPretreat && (editor.querySelector('[data-ops-new-participant]:not([hidden])') ||
                    [...pendingPretreat].some(form => editor.contains(form))));
            if (next?.dataset.tail !== editor.querySelector('[data-ops-inline]').dataset.tail) {
                if (preserve) {
                    editor.open = true;
                    const retained = row.querySelector('[data-ops-inline]');
                    inlineMessage(retained,
                        'Aircraft identity or eligibility changed. Original-tail draft retained for review.', true);
                    if (next) {
                        const nextEditor = accordion(next);
                        nextEditor.open = true;
                        for (const selector of ['.ops-outbound-identity','.ops-outbound-location','.ops-outbound-status']) {
                            const fresh = nextEditor.querySelector(selector);
                            if (fresh) retained.querySelector(selector)?.replaceWith(fresh);
                        }
                        nextEditor.querySelector('[data-ops-inline]').replaceWith(retained);
                    }
                    else incoming.append(row);
                }
                continue;
            }
            const plan = isPretreat && row.querySelector('[data-ops-planned]');
            if (plan && pendingPretreat.has(plan)) next.querySelector('[data-ops-planned]')?.replaceWith(plan);
            const nextEditor = accordion(next);
            const radioFinished = !isPretreat && row.dataset.status === 'radio-pending' && next.dataset.status === 'gone';
            nextEditor.open = desktopInline() || (!radioFinished && ((isPretreat && !media.matches) || editor.open));
            if (radioFinished && selected === row.dataset.missionId) selected = null;
            const disclosureKey = section => section.dataset.opsDisclosure ||
                section.querySelector(':scope > summary')?.textContent.trim();
            const opened = new Set([...editor.querySelectorAll('details[open]')].map(disclosureKey));
            nextEditor.querySelectorAll('details').forEach(section => {
                section.open = !(radioFinished && section.matches('.ops-outbound-extra')) && opened.has(disclosureKey(section));
            });
            if (preserve) {
                const current = editor.querySelector('[data-ops-inline]');
                const replacement = nextEditor.querySelector('[data-ops-inline]');
                // A draft in a different card can be intentionally kept in
                // memory even when this response only expands the chosen card.
                if (replacement.querySelector('.ops-inline-loading') && !replacement.querySelector('.ops-outbound-primary')) {
                    const summary = editor.querySelector('summary');
                    summary.replaceChildren(...nextEditor.querySelector('summary').childNodes);
                    nextEditor.replaceWith(editor);
                } else {
                    const currentExtra = current.querySelector('.ops-outbound-extra');
                    const freshExtra = replacement.querySelector('.ops-outbound-extra');
                    if (freshExtra?.querySelector('.ops-inline-loading') && currentExtra &&
                        (currentExtra.contains(active) || currentExtra.querySelector(dirtyForm))) {
                        // Reconcile the reason like any other form instead of duplicating it in retained details.
                        const reason = currentExtra.querySelector('[data-ops-row-reason]');
                        if (reason) current.querySelector('[data-ops-reason-home]').append(reason);
                        freshExtra.replaceWith(currentExtra);
                    }
                    const freshForms = [...replacement.querySelectorAll('form')];
                    const draftNotice = replacement.querySelector('[data-ops-draft-status]');
                    if (current.querySelector(dirtyForm) && draftNotice) {
                        draftNotice.hidden = false;
                        draftNotice.textContent = 'LIVE DATA UPDATED · Unsaved entries and their original versions were kept. Conflicting saves require review.';
                    }
                    for (const form of current.querySelectorAll('form')) {
                        // Peer confirmation removes the action even if its button had focus.
                        if (radioFinished && form.querySelector('button[value="radio_complete"]')) continue;
                        if (form.dataset.dirty !== 'true' && !pendingPretreat.has(form) &&
                            (!form.contains(active) || form === savedForm) &&
                            !(form.matches('[data-ops-new-participant]') && !form.hidden)) continue;
                        const fresh = freshForms.find(candidate => formKey(candidate) === formKey(form));
                        if (fresh) {
                            if (form.matches('[data-ops-new-participant]') && !form.hidden) {
                                fresh.parentElement.querySelector('[data-ops-add-participant]').hidden = true;
                            }
                            if (form === savedForm && fresh.elements.gallons && !form.elements.gallons) {
                                form.querySelector('[data-ops-record-actual]')?.replaceWith(fresh.elements.gallons);
                            }
                            fresh.replaceWith(form);
                        }
                        else {
                            const review = document.createElement('section');
                            review.className = 'ops-review';
                            const notice = document.createElement('p');
                            notice.textContent = 'This action changed on the server. The original draft is kept for review.';
                            review.append(notice, form);
                            replacement.append(review);
                        }
                    }
                }
            }
            const feedback = editor.querySelector('[data-ops-inline-feedback]');
            if (!feedback.hidden && nextEditor.parentElement) {
                const content = nextEditor.querySelector('[data-ops-inline]');
                inlineMessage(content, feedback.textContent, feedback.classList.contains('is-error'));
                const retry = content.querySelector('[data-ops-pretreat-retry]');
                if (retry && content.querySelector('[data-autosave-retry]')) retry.hidden = false;
            }
        }
    };

    const fetchPage = async (mission = selected, saved = null, afterSave = false) => {
        const requestEpoch = ++epoch;
        const response = await (window.NeoLiveRevision?.fetch || fetch)(inlineRequestUrl(mission), {cache:'no-store',signal:navigationAbort.signal});
        if (!response.ok) throw new Error(`Unable to load current operations (${response.status}). Your draft is kept.`);
        const next = new DOMParser().parseFromString(await response.text(), 'text/html').querySelector('[data-ops-workspace]');
        if (!next) throw new Error('Session expired. Sign in in another tab before continuing; your draft is kept.');
        if (requestEpoch !== epoch || (busy && !afterSave)) return;
        if (next.dataset.operationId !== root.dataset.operationId && dirty()) {
            notify('The canonical sort changed. Draft retained for the original sort; reload current operations in another tab before continuing.', true); return;
        }
        const scroll = window.scrollY;
        const grid = isPretreat && root.querySelector('.ops-pretreat-scroll');
        const gridScroll = grid ? grid.scrollLeft : 0;
        const currentBoard = root.querySelector('[data-ops-board]');
        const nextBoard = next.querySelector('[data-ops-board]');
        const activeRowField = currentBoard.contains(document.activeElement) ? document.activeElement : null;
        const caret = activeRowField && 'selectionStart' in activeRowField ? [activeRowField.selectionStart, activeRowField.selectionEnd] : null;
        const activeTop = activeRowField?.getBoundingClientRect().top;
        reconcilingPretreat = true;
        if (next.dataset.operationId === root.dataset.operationId) reconcileInline(currentBoard, nextBoard, activeRowField, saved);
        currentBoard.replaceWith(nextBoard);
        ['operationId','ramp','rampOverride'].forEach(key => { root.dataset[key] = next.dataset[key]; });
        root.dataset.desktopLayout = next.dataset.layout;
        {
            revision = next.dataset.revision;
            tagOrder();
            applyLayout();
            initialize(!isPretreat, true);
            const activeContent = activeRowField?.closest('[data-ops-inline]');
            const newRow = activeContent && [...nextBoard.querySelectorAll('[data-ops-inline]')].find(content =>
                content.dataset.missionId === activeContent.dataset.missionId && content.dataset.tail === activeContent.dataset.tail);
            const replacement = activeRowField?.form && activeRowField.name && newRow && [...newRow.querySelectorAll('form')].find(form =>
                formKey(form) === formKey(activeRowField.form))?.elements[activeRowField.name];
            const focus = activeRowField?.isConnected ? activeRowField : replacement;
            if (focus && !focus.disabled && !focus.readOnly) {
                focus.focus({preventScroll:true});
                if (caret && caret[0] !== null) focus.setSelectionRange(...caret);
            }
            if (grid) grid.scrollLeft = gridScroll;
            reconcilingPretreat = false;
            const shift = focus?.isConnected ? focus.getBoundingClientRect().top - activeTop : 0;
            window.scrollTo({top:scroll + shift,behavior:'instant'});
            history.replaceState(null,'',currentUrl(selected));
            return;
        }
    };
    const preferences = async changes => {
        if (preferenceBusy || busy) { notify('Wait for the current save to finish before changing the presentation.'); return false; }
        preferenceBusy = true; ++epoch;
        const controls = [...root.querySelectorAll('.ops-modes button,.ops-layouts button,[data-ops-ramp]')];
        controls.forEach(control => {control.disabled=true;});
        try {
            const response = await fetch(root.dataset.preferenceUrl,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...changes,operation_id:Number(root.dataset.operationId)||null})});
            const payload = await response.json();
            if (!response.ok || !payload.ok) throw new Error(payload.error || 'Unable to save presentation preference.');
            Object.assign(root.dataset,{desktopLayout:payload.preferences.layout,ramp:payload.preferences.ramp});
            if ('ramp' in changes) { root.dataset.rampOverride = ''; history.replaceState(null,'',currentUrl(selected)); }
            return true;
        } catch (error) { notify(error.message,true); return false; }
        finally { preferenceBusy = false; controls.forEach(control=>{control.disabled=false;}); drainPretreat(); }
    };
    root.addEventListener('input', event => {
        const form = event.target.closest('form'); if (!form || form.matches('[data-ops-autosave]')) return;
        form.dataset.dirty = 'true'; sync(form); saveDraft();
    });
    root.addEventListener('keydown', event => {
        if (event.key !== 'Enter' || !event.target.matches('[data-clock-time]')) return;
        const form = event.target.closest(autosaveSelector);
        if (!form) return;
        event.preventDefault();
        event.target.blur();
    });
    root.addEventListener('focusout', event => {
        const form = event.target.closest(autosaveSelector);
        if (!reconcilingPretreat && form?.dataset.dirty === 'true' &&
            (event.target.name === 'notes' || event.target.matches('[data-clock-time]'))) queuePretreat(form);
    });
    root.addEventListener('change', async event => {
        if (reconcilingPretreat) return;
        const form = event.target.closest('form');
        if (form?.matches('[data-ops-reason-form]')) {
            form.dataset.dirty = 'true'; saveDraft();
            if (busy || preferenceBusy) {
                const line = inlineFor(form);
                const message = 'Another save is in progress. Your reason draft is kept; select it again after that save.';
                if (line) inlineMessage(line,message,true); else notify(message,true);
                return;
            }
            form.requestSubmit(); return;
        }
        if (form?.matches('[data-ops-autosave]')) {
            if (isPretreat && form.matches('[data-ops-planned]')) {
                queuePretreat(form);
                return;
            }
            if (busy || preferenceBusy) { event.target.checked = !event.target.checked; return; }
            form.requestSubmit();
            return;
        }
        if (form) { form.dataset.dirty = 'true'; sync(form); saveDraft(); }
        if (form?.matches(autosaveSelector) && !['notes','correction_reason'].includes(event.target.name)) queuePretreat(form);
        if (event.target.matches('[data-ops-ramp]')) {
            const ramp = event.target.value;
            if (!media.matches) {
                root.dataset.rampOverride = ramp === 'All' ? '' : ramp;
                history.replaceState(null,'',currentUrl(selected));
                applyLayout();
            } else if (await preferences({ramp})) applyLayout();
        }
    });
    const openAccordion = async (card, open, savedForm = null) => {
        saveDraft();
        const mission = card.closest('[data-ops-row]').dataset.missionId;
        if (open) {
            root.querySelectorAll('[data-ops-accordion][open]').forEach(other => {
                if (other !== card && !other.querySelector(dirtyForm)) {
                    if (desktopInline()) {
                        other.querySelector('.ops-outbound-extra')?.removeAttribute('open');
                        other.querySelector('[data-ops-expand-details]')?.setAttribute('aria-expanded','false');
                    } else other.open = false;
                }
            });
            card.open = true;
            selected = mission;
        } else {
            card.open = desktopInline();
            const extra = card.querySelector('.ops-outbound-extra');
            if (extra) extra.open = false;
            if (selected === mission) selected = null;
            const focus = desktopInline() ? '[data-ops-expand-details]' :
                !media.matches && root.dataset.layout === 'visual' ? '[data-ops-ramp-chevron]' : 'summary';
            card.querySelector(focus)?.focus({preventScroll:true});
        }
        if (!isPretreat) root.querySelectorAll('[data-ops-ramp-chevron]').forEach(button =>
            button.setAttribute('aria-expanded', String(button.closest('[data-ops-accordion]').open)));
        history.replaceState(null,'',currentUrl(selected));
        if (!open) ++epoch;
        if (open && (!isPretreat || card.querySelector('.ops-inline-loading'))) {
            try { await fetchPage(mission, savedForm, Boolean(savedForm)); }
            catch (error) { inlineMessage(card.querySelector('[data-ops-inline]'),error.message,true); }
        }
    };
    root.addEventListener('click', async event => {
        const chevron = event.target.closest('[data-ops-ramp-chevron]');
        if (chevron) {
            event.preventDefault();
            if (busy || preferenceBusy) return;
            const card = chevron.closest('[data-ops-accordion]');
            await openAccordion(card, !card.open);
            return;
        }
        const expand = event.target.closest('[data-ops-expand-details]');
        if (expand) {
            event.preventDefault();
            if (busy || preferenceBusy) return;
            const card = expand.closest('[data-ops-accordion]');
            const extra = card.querySelector('.ops-outbound-extra');
            if (extra.open) {
                await openAccordion(card,false);
                expand.setAttribute('aria-expanded','false');
            } else {
                extra.open = true;
                expand.setAttribute('aria-expanded','true');
                await openAccordion(card,true);
            }
            return;
        }
        const radio = event.target.closest('[data-ops-radio]');
        if (radio) {
            event.preventDefault();
            if (busy || preferenceBusy) return;
            const card = radio.closest('[data-ops-accordion]');
            const mission = card.closest('[data-ops-row]').dataset.missionId;
            await openAccordion(card, true);
            root.querySelector(`[data-ops-row][data-mission-id="${mission}"] .ops-outbound-extra`)?.setAttribute('open','');
            const section = root.querySelector(`[data-ops-row][data-mission-id="${mission}"] [data-ops-radio-section]`);
            if (section) { section.scrollIntoView({block:'nearest'}); section.focus({preventScroll:true}); }
            return;
        }
        const control = event.target.closest('[data-ops-accordion] > summary, [data-ops-collapse-inline]');
        if (control && (!isPretreat || media.matches) && !event.target.closest('button:not([data-ops-collapse-inline]),input,select,textarea,a')) {
            event.preventDefault();
            if (busy || preferenceBusy) return;
            if (!isPretreat && !media.matches && root.dataset.layout === 'visual' && control.matches('summary')) return;
            const card = control.closest('[data-ops-accordion]');
            await openAccordion(card, !card.open);
            return;
        }
        const retry = event.target.closest('[data-ops-pretreat-retry]');
        if (retry) {
            const form = retry.closest('[data-ops-inline]').querySelector(autosaveSelector + '[data-autosave-retry]');
            if (form) queuePretreat(form);
            return;
        }
        const addParticipant = event.target.closest('[data-ops-add-participant]');
        if (addParticipant) {
            const form = addParticipant.previousElementSibling;
            form.hidden = false;
            addParticipant.hidden = true;
            form.elements.truck_number.focus({preventScroll:true});
            return;
        }
        const recordActual = event.target.closest('[data-ops-record-actual]');
        if (recordActual) {
            const form = recordActual.form;
            form.elements.confirm_actual.value = 'yes';
            form.dataset.dirty = 'true';
            saveDraft();
            queuePretreat(form);
            return;
        }
        const config = event.target.closest('[data-ops-pretreat-config] button');
        if (config && (busy || preferenceBusy)) {
            event.preventDefault();
            queuePretreat(config.form);
            return;
        }
        const correct = event.target.closest('[data-ops-pretreat-correction]');
        if (correct) {
            const inline = inlineFor(correct);
            const section = inline.querySelector('[data-ops-disclosure="correction"]');
            section.open = true;
            section.querySelector('[data-subzero-edit]:not([hidden]), input[name="correction_reason"]:not([disabled])')?.focus();
            return;
        }
        const inspection = event.target.closest('[data-open-frost-inspection]');
        if (inspection) {
            const section = inlineFor(inspection)?.querySelector('.neosubzero-frost-inspection');
            if (section) {
                section.open = true;
                section.querySelector('select[name="frost_result"]')?.focus();
                section.scrollIntoView({block:'nearest'});
            }
            return;
        }
        const reloadInline = event.target.closest('[data-ops-reload-inline]');
        if (reloadInline) {
            if (busy || preferenceBusy) return;
            const content = inlineFor(reloadInline);
            if (content.querySelector('form[data-dirty="true"]') && !window.confirm('Discard unsaved entries for this aircraft?')) return;
            const controls = [...content.querySelectorAll('button,input:not([type="hidden"]),select,textarea')].filter(control => !control.disabled);
            busy = true;
            content.setAttribute('aria-busy','true');
            controls.forEach(control => {control.disabled=true;});
            try {
                content.querySelectorAll('form[data-dirty="true"]').forEach(form => {
                    pendingPretreat.delete(form);
                    delete form.dataset.dirty;
                });
                inlineMessage(content, '');
                saveDraft();
                if (content.contains(document.activeElement)) document.activeElement.blur();
                reloadInline.blur();
                await fetchPage(selected || content.dataset.missionId,null,true);
            } catch (error) { inlineMessage(content, error.message, true); }
            finally {
                busy = false;
                controls.forEach(control => {control.disabled=false;});
                content.removeAttribute('aria-busy');
                drainPretreat();
            }
            return;
        }
        const mode = event.target.closest('[data-ops-mode]');
        if (mode && mode.dataset.opsMode !== root.dataset.mode) {
            saveDraft();
            if (await preferences({mode:mode.dataset.opsMode})) {
                const url = new URL(root.dataset.workspaceUrl,location.origin);
                if (root.dataset.rampOverride) url.searchParams.set('ramp',root.dataset.rampOverride);
                // Navigation, not a form save: drafts remain isolated by mode/tail/sort.
                stopLive();
                location.assign(url);
            }
            return;
        }
        const layout = event.target.closest('[data-ops-layout]');
        if (layout && !media.matches) {
            const changes = {layout:layout.dataset.opsLayout};
            if (await preferences(changes)) applyLayout(); return;
        }
        const edit = event.target.closest('[data-subzero-edit]');
        if (edit) {
            const form = edit.closest('form'); form.dataset.unlocked = 'true'; edit.hidden = true;
            form.querySelectorAll('textarea,input[name="reason_for_application"]').forEach(e => {e.readOnly=false;});
            form.querySelector('button[type="submit"]').hidden = false; sync(form);
            const inline = inlineFor(form);
            if (inline) {
                form.dataset.dirty = 'true';
                saveDraft();
                const correction = form.elements.correction_reason;
                if (correction) form.querySelector('[data-ops-disclosure="notes"]')?.setAttribute('open','');
                if (isPretreat && correction) form.querySelector('[data-ops-disclosure="correction"]').open = true;
                correction?.focus();
            }
            const editor = inline || form;
            editor.querySelectorAll('[data-subzero-spray-control]').forEach(e => {e.readOnly=e.dataset.sprayReady==='false';});
            editor.querySelectorAll('[data-subzero-spray-save]').forEach(e => {e.hidden=false;}); return;
        }
        const quickStamp = event.target.closest('[data-ops-pass-stamp]');
        if (quickStamp) {
            event.preventDefault();
            if (busy || preferenceBusy) return;
            const form = quickStamp.form;
            const mission = form.elements.mission_id.value;
            if ([...root.querySelectorAll('[data-subzero-deice-form][data-dirty="true"]')]
                    .some(draft => draft.elements.mission_id.value === mission)) {
                const line = inlineFor(form) || quickStamp.closest('[data-ops-row]').querySelector('[data-ops-inline]');
                const message = 'Save treatment changes before recording a quick pass time. Your draft is kept.';
                if (line) inlineMessage(line,message,true); else notify(message,true);
                return;
            }
            form.elements[quickStamp.dataset.opsPassStamp].value = window.NeoAppsClockTime.normalize(shared.operationalHHMM());
            form.requestSubmit(); return;
        }
        const stamp = event.target.closest('[data-subzero-deice-stamp]');
        if (stamp) {
            const input = stamp.closest('form').elements[stamp.dataset.subzeroDeiceStamp];
            input.value = window.NeoAppsClockTime.normalize(shared.operationalHHMM()); input.dispatchEvent(new Event('input',{bubbles:true}));
        }
    });
    root.addEventListener('submit', async event => {
        const form = event.target.closest('form'); if (!form) return;
        event.preventDefault(); if (busy || preferenceBusy) return;
        const confirmation = form.dataset.subzeroConfirm || (media.matches && form.dataset.subzeroConfirmMobile);
        if (confirmation && !window.confirm(confirmation)) return;
        const inline = inlineFor(form);
        const autosave = form.matches(autosaveSelector) && !event.submitter;
        if (form.matches(autosaveSelector) && !autosave && !form.elements.correction_reason?.value.trim()) {
            inlineMessage(inline, 'Enter a correction reason before applying changes.', true);
            return;
        }
        const sentValues = autosave ? autosaveValues(form) : null;
        const body = new FormData(form), content = inline;
        if (event.submitter?.name) body.set(event.submitter.name,event.submitter.value);
        body.set('target',formAction(form)); body.set('mode',root.dataset.mode);
        body.set('operation_id',root.dataset.operationId); if (content) body.set('physical_tail',content.dataset.tail);
        const toggle = form.querySelector('[data-ops-quick-toggle]');
        const planned = form.matches('[data-ops-planned]');
        const reasonSave = form.matches('[data-ops-reason-form]');
        const quiet = !isPretreat || reasonSave || (isPretreat && (form.matches(autosaveSelector) || planned || form.matches('[data-ops-pretreat-config]')));
        const toggleError = form.querySelector('[data-ops-toggle-error]');
        if (planned) body.set('planned',toggle.checked ? '1' : '0');
        const key = formKey(form), abort = new AbortController();
        const timeout = setTimeout(()=>abort.abort(),25000);
        busy = true; ++epoch;
        const row = inline?.closest('[data-ops-row]');
        if (quiet && row) row.setAttribute('aria-busy', 'true');
        const controls = [...(row || content || form)
            .querySelectorAll('button,input:not([type="hidden"]),select,textarea')]
            .filter(control => !control.disabled);
        if (!autosave) controls.forEach(control => { control.disabled = true; });
        const unlock = () => { if (!autosave) controls.forEach(control => { control.disabled = false; }); };
        if (toggleError) toggleError.hidden = true;
        if (quiet) { if (inline) inlineMessage(inline, ''); else notify(''); }
        else if (inline && !planned) inlineMessage(inline, 'SAVING…');
        else if (!planned) notify('SAVING…');
        let saved = false, correctionRequired = false;
        try {
            const response = await fetch(root.dataset.mutateUrl,{method:'POST',body,signal:abort.signal});
            let payload; try { payload=await response.json(); } catch (_) { throw new Error(`Save could not be confirmed (${response.status}). Your entries are kept. Check current state before retrying.`); }
            if (!response.ok || !payload.ok) {
                correctionRequired = payload.correction_required === true;
                throw new Error(payload.error || 'Save rejected. Your entries are kept.');
            }
            saved = true;
            delete form.dataset.autosaveRetry;
            if (form.matches('[data-ops-outbound-autosave]') && typeof payload.version === 'string' &&
                payload.event_id === body.get('event_id') && form.elements.expected_version.value === body.get('expected_version')) {
                // Advance only this form to its own committed receipt. Other
                // dirty forms retain their original concurrency evidence.
                form.elements.expected_version.value = payload.version;
            }
            if (isPretreat && typeof payload.version === 'string') {
                const previous = body.get('expected_version'), endpoint = formAction(form);
                for (const field of row.querySelectorAll('[name="expected_version"]')) {
                    if (field.value === previous && formAction(field.form) === endpoint) field.value = payload.version;
                }
            }
            if (payload.record_id && form.matches('.ops-pretreat-participant')) {
                form.elements.record_id.value = payload.record_id;
                form.elements.record_version.value = payload.record_version;
                form.removeAttribute('data-ops-new-participant');
            }
            if (autosave) {
                form.dataset.savedValues = sentValues;
                for (const field of form.querySelectorAll('[data-clock-time]')) {
                    if (body.has(field.name)) field.defaultValue = body.get(field.name);
                }
            }
            if (!autosave || autosaveValues(form) === sentValues) delete form.dataset.dirty;
            saveDraft();
            if (planned) notify('');
            if (inline) {
                if (!autosave && form.contains(document.activeElement)) document.activeElement.blur();
                if (!planned && !quiet) inlineMessage(inline, payload.message);
            }
            else notify(quiet ? '' : payload.message);
            if (!reasonSave) unlock();
            if (!isPretreat && !media.matches && root.dataset.layout === 'visual' && row &&
                ['initial_contact','move_to_planned'].includes(body.get('action'))) {
                await openAccordion(accordion(row), true, form);
            } else await fetchPage(selected,form,true);
            if (reasonSave) unlock();
        } catch(error) {
            const message = error.name === 'AbortError' ? 'Save timed out. Your draft is kept. Check current state before retrying; no automatic retry was sent.'
                : error instanceof TypeError ? 'Network error while saving. Your draft is kept. Check current state before retrying; no automatic retry was sent.'
                : error.message;
            if (autosave && !saved && !correctionRequired) form.dataset.autosaveRetry = '';
            if (toggle && !saved) toggle.checked = !toggle.checked;
            if (toggleError) {
                toggleError.textContent = 'Save error';
                toggleError.title = message;
                toggleError.hidden = false;
                if (isPretreat && (!media.matches || accordion(row).open)) inlineMessage(inline, message, true);
                else notify(message,true);
            } else if (inline) {
                if (!isPretreat && !desktopInline()) accordion(row).open = true;
                inlineMessage(inline, message, true);
                if (correctionRequired) {
                    form.querySelector('[data-ops-participant-correction]').hidden = false;
                    form.elements.correction_reason.disabled = false;
                    form.elements.correction_reason.focus({preventScroll:true});
                } else if (autosave && !saved && !message.includes('changed')) {
                    const retry = inline.querySelector('[data-ops-pretreat-retry]');
                    if (retry) retry.hidden = false;
                } else if (autosave && !saved) form.dataset.autosaveConflict = 'true';
            }
            else notify(message,true);
        }
        finally {
            clearTimeout(timeout); busy=false; if (!saved || reasonSave) unlock();
            if (quiet && row) row.removeAttribute('aria-busy');
            drainPretreat();
        }
    });
    root.dataset.desktopLayout = root.dataset.layout;
    root.querySelectorAll('[data-ops-accordion]').forEach(card => {
        card.open = desktopInline() || (!media.matches && isPretreat) || card.hasAttribute('data-ops-initial-open');
    });
    tagOrder(); applyLayout(); initialize(true);
    root.dataset.ready = 'true';
    media.addEventListener('change', () => {
        root.querySelectorAll('[data-ops-accordion]').forEach(card => {
            card.open = (!media.matches && isPretreat) || card.contains(document.activeElement) ||
                !!card.querySelector(dirtyForm) || card.closest('[data-ops-row]').dataset.missionId === selected;
        });
        applyLayout();
        if (!isPretreat && selected && !busy) fetchPage().catch(error => notify(error.message,true));
    });
    if (!isPretreat && requestedDrafts().length) {
        fetchPage().catch(error => notify(error.message,true));
    }
    window.addEventListener('beforeunload',()=>{ saveDraft(); stopLive(); });
    window.addEventListener('pagehide',stopLive);
    // A back/forward-cache restore needs a fresh controller and canonical scope;
    // reload restores any saved draft with its original concurrency token.
    window.addEventListener('pageshow',event=>{ if (event.persisted) location.reload(); });
    const initialRefresh = JSON.parse(root.dataset.refreshStatus || '{}');
    const interval = Number(initialRefresh.live_screen_refresh_interval_ms || root.dataset.refreshIntervalMs);
    if (!window.NeoLiveUpdates || interval < 1000) return;
    controller = window.NeoLiveUpdates.create({intervalMs:interval,continuousWhileVisible:true,poll:async()=>{
        if (busy || preferenceBusy) return;
        const url = new URL(root.dataset.revisionUrl,location.origin); url.searchParams.set('revision',revision);
        url.searchParams.set('mode',root.dataset.mode);
        if (media.matches) url.searchParams.set('presentation','inline');
        const response = await fetch(url,{cache:'no-store',signal:navigationAbort.signal});
        if (!response.ok) throw new Error('Unable to refresh operations.');
        const payload = await response.json();
        if (payload.refresh?.live_screen_refresh_interval_ms) controller.setIntervalMs(payload.refresh.live_screen_refresh_interval_ms);
        controller.setServerStatus(payload.refresh || {});
        if (payload.changed && !busy && !preferenceBusy) await fetchPage();
    }});
    controller.setServerStatus(initialRefresh);
})();
