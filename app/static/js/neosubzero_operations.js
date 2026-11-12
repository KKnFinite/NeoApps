(() => {
    'use strict';
    const root = document.querySelector('[data-ops-workspace]');
    if (!root) return;
    const shared = window.NeoSubZeroDepartureForms;
    const pane = root.querySelector('[data-ops-detail]');
    const feedback = root.querySelector('[data-ops-feedback]');
    const media = window.matchMedia('(max-width:900px)');
    const primaryRamps = ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo'];
    let selected = pane.querySelector('[data-mission-id]')?.dataset.missionId || null;
    let epoch = 0, busy = false, preferenceBusy = false;
    let controller = null;
    const navigationAbort = new AbortController();
    const stopLive = () => { navigationAbort.abort(); controller?.destroy(); };
    let revision = root.dataset.revision;
    const syncFeedback = () => {
        const inline = pane.querySelector('[data-ops-detail-feedback]');
        if (inline) { inline.textContent = feedback.textContent; inline.hidden = feedback.hidden; inline.classList.toggle('is-error', feedback.classList.contains('is-error')); }
    };
    const notify = (text, error = false) => {
        feedback.textContent = text; feedback.hidden = !text;
        feedback.classList.toggle('is-error', error);
        syncFeedback();
    };
    const formAction = form => new URL(form.getAttribute('action'), location.origin).pathname;
    const formKey = form => [formAction(form), form.elements.pass_number?.value || '',
        form.elements.position_number?.value || '', form.elements.record_id?.value || '',
        form.querySelector('input[name="action"]')?.value || form.querySelector('button[name="action"]')?.value || form.elements.inspection_action?.value || ''].join(':');
    const draftKey = detail => ['neoapps.subzero.operations.draft', root.dataset.userId,
        root.dataset.operationId, root.dataset.mode, detail.dataset.missionId, detail.dataset.tail].join(':');
    const detail = () => pane.querySelector('[data-ops-detail-content]');
    const isPretreat = root.dataset.mode === 'pretreat';
    const draftContents = () => isPretreat ? [...root.querySelectorAll('[data-ops-inline]')] : [detail()].filter(Boolean);
    const inlineFor = form => form.closest('[data-ops-inline]');
    const inlineMessage = (content, text, error = false) => {
        const line = content.querySelector('[data-ops-inline-feedback]');
        line.textContent = text;
        line.hidden = !text;
        line.classList.toggle('is-error', error);
        content.querySelector('[data-ops-reload-inline]').hidden = !error;
    };
    const dirty = () => !!root.querySelector('form[data-dirty="true"]');
    const draftMessage = text => {
        const note = pane.querySelector('[data-ops-draft-status]');
        if (note) { note.textContent = text; note.hidden = !text; }
    };
    const saveDraft = () => {
        const context = root.querySelector('[data-ops-context] form');
        if (context) {
            try {
                const key = ['neoapps.subzero.operations.context',root.dataset.userId,root.dataset.operationId].join(':');
                if (context.dataset.dirty === 'true') sessionStorage.setItem(key,JSON.stringify([...context.elements].filter(e=>e.name && e.name!=='csrf_token').map(e=>({name:e.name,value:e.value}))));
                else sessionStorage.removeItem(key);
            } catch (_) { notify('Context draft remains open. Browser storage is unavailable; save before reloading.',true); }
        }
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
                if (isPretreat) inlineMessage(content, message, true);
                else draftMessage(message);
            }
        }
    };
    const sync = form => {
        if (form.matches('[data-subzero-deice-form]')) {
            if (!form.dataset.unlocked) form.dataset.unlocked = form.dataset.locked === 'true' ? 'false' : 'true';
            shared.syncForm(form);
        } else if (form.matches('.neosubzero-treatment-form')) {
            if (!form.dataset.unlocked) form.dataset.unlocked = form.querySelector('[data-subzero-edit]:not([hidden])') || !form.querySelector('button[type="submit"]') ? 'false' : 'true';
            const unlocked = form.dataset.unlocked === 'true';
            const names = ['pass1_start', 'pass1_end', 'pass2_start', 'pass2_end'];
            const useSecond = !form.elements.pass2_plan || form.elements.pass2_plan.value === 'type_iv';
            names.forEach((name, index) => {
                const enabled = unlocked && (index < 2 || useSecond) && (!index || !!form.elements[names[index-1]].value);
                form.elements[name].readOnly = !enabled;
                if (index >= 2) form.elements[name].disabled = !useSecond;
                const stamp = form.querySelector(`[data-subzero-stamp="${name}"]`);
                if (stamp) stamp.hidden = !enabled;
            });
            form.querySelectorAll('select').forEach(e => { e.disabled = !unlocked; });
            form.elements.pass2_surface_area.disabled = !unlocked || !useSecond;
            form.elements.notes.readOnly = !unlocked;
            const edit = form.querySelector('[data-subzero-edit]');
            const save = form.querySelector('button[type="submit"]');
            if (edit) edit.hidden = unlocked;
            if (save) save.hidden = !unlocked;
        }
    };
    const initialize = (restore = false) => {
        if (!isPretreat) pane.querySelectorAll('form').forEach(sync);
        for (const content of draftContents()) {
            if (isPretreat) content.querySelectorAll('form').forEach(sync);
            if (!restore) continue;
            let drafts = [];
            try { drafts = JSON.parse(sessionStorage.getItem(draftKey(content)) || '[]');
                drafts.forEach(draft => { draft.fields = draft.fields.filter(field => field.name !== 'csrf_token'); });
                if (drafts.length) sessionStorage.setItem(draftKey(content),JSON.stringify(drafts));
            } catch (_) { /* current DOM remains usable */ }
            for (const draft of drafts) {
                const form = [...content.querySelectorAll('form')].find(e => formKey(e) === draft.key);
                if (!form) continue;
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
                if (isPretreat && draft.fields.some(field => field.name === 'notes' && field.value !== form.elements.notes?.defaultValue)) {
                    form.querySelector('.ops-pretreat-notes').open = true;
                }
            }
            if (drafts.length) {
                const message = 'RESTORED UNSAVED DRAFT · Original version retained. Resolve any server conflict before saving.';
                if (isPretreat) {
                    content.closest('details').open = true;
                    inlineMessage(content, message, true);
                } else draftMessage(message);
            }
        }
    };
    const applyLayout = () => {
        const board = root.querySelector('[data-ops-board]');
        const rows = [...board.querySelectorAll('[data-ops-row]')];
        // Preserve global server ordering while moving rows into/out of columns.
        rows.sort((a,b) => Number(a.dataset.order) - Number(b.dataset.order));
        rows.forEach(row => board.append(row));
        board.querySelectorAll('.ops-ramp-column').forEach(e => e.remove());
        if (isPretreat) root.dataset.layout = 'list';
        const visual = root.dataset.layout === 'visual';
        // Desktop always shows every ramp. Keep the stored mobile selection
        // intact, including when live refresh or a viewport change reapplies it.
        const ramp = !media.matches ? 'All' : visual && root.dataset.ramp === 'All' ? 'Alpha' : root.dataset.ramp;
        const selector = root.querySelector('[data-ops-ramp]');
        selector.querySelector('option[value="All"]').disabled = visual && media.matches;
        selector.value = ramp;
        if (visual) {
            const ramps = [...primaryRamps, ...['Remote','Location Review'].filter(r => rows.some(row => row.dataset.ramp === r))];
            ramps.forEach(name => {
                const column = document.createElement('section'); column.className = 'ops-ramp-column'; column.dataset.ramp = name;
                if (!primaryRamps.includes(name)) column.classList.add('is-extra');
                column.hidden = media.matches && name !== ramp;
                const heading = document.createElement('h2'); heading.textContent = name.toUpperCase(); column.append(heading);
                rows.filter(row => row.dataset.ramp === name).forEach(row => { row.hidden = ramp !== 'All' && row.dataset.ramp !== ramp; column.append(row); });
                if (!column.querySelector('[data-ops-row]:not([hidden])')) { const empty = document.createElement('p'); empty.textContent = 'No aircraft'; column.append(empty); }
                board.append(column);
            });
        } else rows.forEach(row => { row.hidden = ramp !== 'All' && row.dataset.ramp !== ramp; });
        root.querySelectorAll('[data-ops-layout]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.opsLayout === root.dataset.layout)));
    };
    const tagOrder = () => root.querySelectorAll('[data-ops-row]').forEach((row,index) => { row.dataset.order = index; });
    const currentUrl = mission => {
        const url = new URL(root.dataset.workspaceUrl, location.origin);
        url.searchParams.set('mode',root.dataset.mode);
        if (mission) url.searchParams.set('mission',mission);
        return url;
    };
    const reconcilePretreat = (board, incoming, active) => {
        const nextRows = new Map([...incoming.querySelectorAll('[data-ops-row]')].map(row => [row.dataset.missionId, row]));
        if (!media.matches) nextRows.forEach(row => { row.querySelector('[data-ops-pretreat-row]').open = true; });
        for (const row of board.querySelectorAll('[data-ops-row]')) {
            const next = nextRows.get(row.dataset.missionId);
            const editor = row.querySelector('[data-ops-pretreat-row]');
            const preserve = row.contains(active) || row.querySelector('form[data-dirty="true"]');
            if (next?.dataset.tail !== row.dataset.tail) {
                if (preserve) {
                    editor.open = true;
                    inlineMessage(row.querySelector('[data-ops-inline]'),
                        'Aircraft identity or eligibility changed. Original-tail draft retained for review.', true);
                    if (next) next.replaceWith(row);
                    else incoming.append(row);
                }
                continue;
            }
            const nextEditor = next.querySelector('[data-ops-pretreat-row]');
            if (preserve) {
                row.className = next.className;
                row.dataset.status = next.dataset.status;
                row.dataset.ramp = next.dataset.ramp;
                const summary = editor.querySelector('summary');
                summary.replaceChildren(...nextEditor.querySelector('summary').childNodes);
                next.replaceWith(row);
                continue;
            }
            nextEditor.open = editor.open;
            const opened = new Set([...editor.querySelectorAll('details[open]')]
                .map(section => section.querySelector(':scope > summary')?.textContent));
            nextEditor.querySelectorAll('details').forEach(section => {
                section.open = opened.has(section.querySelector(':scope > summary')?.textContent);
            });
            const feedback = editor.querySelector('[data-ops-inline-feedback]');
            if (!feedback.hidden) {
                inlineMessage(nextEditor.querySelector('[data-ops-inline]'), feedback.textContent, feedback.classList.contains('is-error'));
            }
        }
    };

    const fetchPage = async (mission = selected, saved = null) => {
        const requestEpoch = ++epoch;
        const response = await (window.NeoLiveRevision?.fetch || fetch)(currentUrl(mission), {cache:'no-store',signal:navigationAbort.signal});
        if (!response.ok) throw new Error(`Unable to load current operations (${response.status}). Your draft is kept.`);
        const next = new DOMParser().parseFromString(await response.text(), 'text/html').querySelector('[data-ops-workspace]');
        if (!next) throw new Error('Session expired. Sign in in another tab before continuing; your draft is kept.');
        if (requestEpoch !== epoch || busy) return;
        if (next.dataset.operationId !== root.dataset.operationId && dirty()) {
            notify('The canonical sort changed. Draft retained for the original sort; reload current operations in another tab before continuing.', true); return;
        }
        const scroll = window.scrollY, paneScroll = pane.scrollTop;
        const contextForm=root.querySelector('[data-ops-context] form');
        if (next.dataset.operationId !== root.dataset.operationId || (!contextForm?.dataset.dirty && !contextForm?.contains(document.activeElement))) {
            const previous = root.querySelector('[data-ops-context]');
            const incoming = next.querySelector('[data-ops-context]');
            if (previous && incoming) {incoming.open=previous.open;previous.replaceWith(incoming);}
        }
        const currentBoard = root.querySelector('[data-ops-board]');
        const nextBoard = next.querySelector('[data-ops-board]');
        const activeRowField = isPretreat && currentBoard.contains(document.activeElement) ? document.activeElement : null;
        const caret = activeRowField && 'selectionStart' in activeRowField ? [activeRowField.selectionStart, activeRowField.selectionEnd] : null;
        if (isPretreat && next.dataset.operationId === root.dataset.operationId) reconcilePretreat(currentBoard, nextBoard, activeRowField);
        currentBoard.replaceWith(nextBoard);
        ['operationId','layout','ramp'].forEach(key => { root.dataset[key] = next.dataset[key]; });
        if (isPretreat) {
            revision = next.dataset.revision;
            tagOrder();
            applyLayout();
            initialize();
            if (activeRowField?.isConnected) {
                activeRowField.focus({preventScroll:true});
                if (caret && caret[0] !== null) activeRowField.setSelectionRange(...caret);
            }
            window.scrollTo({top:scroll,behavior:'instant'});
            return;
        }
        const incoming = next.querySelector('[data-ops-detail-content]');
        const current = detail();
        // Optional evidence/details must not close on an idle live refresh,
        // including Safari summary taps that do not move keyboard focus.
        const expansionKey=element=>[element.closest('[data-participant-id]')?.dataset.participantId || '',element.querySelector(':scope > summary')?.textContent].join(':');
        if (current && incoming && current.dataset.missionId === incoming.dataset.missionId && current.dataset.tail === incoming.dataset.tail) {
            const opened=new Set([...current.querySelectorAll('details[open]')].map(expansionKey));
            incoming.querySelectorAll('details').forEach(element=>{if(opened.has(expansionKey(element)))element.open=true;});
        }
        if (current && incoming && current.dataset.missionId === incoming.dataset.missionId && current.dataset.tail === incoming.dataset.tail && (dirty() || pane.contains(document.activeElement))) {
            const freshForms = [...incoming.querySelectorAll('form')];
            const active=document.activeElement;
            const caret=active && 'selectionStart' in active ? [active.selectionStart,active.selectionEnd] : null;
            current.querySelectorAll('form').forEach(form => {
                if (form.dataset.dirty !== 'true' && (!form.contains(active) || formKey(form) === saved)) return;
                const replacement = freshForms.find(e => formKey(e) === formKey(form));
                if (replacement) replacement.replaceWith(form);
                else {
                    const review=document.createElement('section');review.className='ops-review';
                    const note=document.createElement('p');note.textContent='This action changed on the server. Original draft retained for review; reload current values before retrying.';
                    review.append(note,form);incoming.querySelector('[data-ops-detail-body]').append(review);
                }
            });
            current.replaceWith(incoming);
            if (active?.isConnected) {active.focus({preventScroll:true});if(caret)active.setSelectionRange(...caret);}
            draftMessage('LIVE DATA UPDATED · Unsaved entries and their original versions were kept. Conflicting saves require review.');
            initialize();saveDraft();
        } else if (current && (dirty() || pane.contains(document.activeElement)) && incoming && current.dataset.missionId === incoming.dataset.missionId && current.dataset.tail !== incoming.dataset.tail) {
            draftMessage('TAIL CHANGED · Original-tail draft preserved. Close these details and review the replacement aircraft.');
            notify('Aircraft identity changed; original physical evidence has not been reassigned.', true);
        } else {
            saveDraft(); pane.replaceChildren(...next.querySelector('[data-ops-detail]').childNodes);
            pane.hidden = !incoming; selected = incoming?.dataset.missionId || null; initialize(true);
        }
        revision = next.dataset.revision; tagOrder(); applyLayout(); syncFeedback();
        pane.scrollTop = paneScroll; window.scrollTo({top:scroll,behavior:'instant'});
        history.replaceState(null,'',currentUrl(selected));
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
            Object.assign(root.dataset,{layout:payload.preferences.layout,ramp:payload.preferences.ramp});
            return true;
        } catch (error) { notify(error.message,true); return false; }
        finally { preferenceBusy = false; controls.forEach(control=>{control.disabled=false;}); }
    };
    root.addEventListener('input', event => {
        const form = event.target.closest('form'); if (!form) return;
        form.dataset.dirty = 'true'; sync(form); saveDraft();
    });
    root.addEventListener('change', async event => {
        const form = event.target.closest('form');
        if (form) { form.dataset.dirty = 'true'; sync(form); saveDraft(); }
        if (event.target.matches('[data-ops-ramp]')) {
            if (await preferences({ramp:event.target.value})) applyLayout();
        }
    });
    root.addEventListener('click', async event => {
        const inspection = event.target.closest('[data-open-frost-inspection]');
        if (inspection) {
            const section = pane.querySelector('.neosubzero-frost-inspection');
            if (section) {
                section.open = true;
                section.querySelector('select[name="frost_result"]')?.focus();
                section.scrollIntoView({block:'nearest'});
            }
            return;
        }
        const open = event.target.closest('[data-ops-open]');
        if (open) { event.preventDefault(); saveDraft(); const mission = open.closest('[data-ops-row]').dataset.missionId; try { pane.querySelector(':focus')?.blur(); selected = mission; await fetchPage(mission); } catch(error) { notify(error.message,true); } return; }
        const close = event.target.closest('[data-ops-close]');
        if (close) { saveDraft(); pane.hidden = true; selected = null; history.replaceState(null,'',currentUrl(null)); return; }
        const reloadInline = event.target.closest('[data-ops-reload-inline]');
        if (reloadInline) {
            const content = inlineFor(reloadInline);
            if (content.querySelector('form[data-dirty="true"]') && !window.confirm('Discard unsaved entries for this aircraft?')) return;
            try {
                content.querySelectorAll('form[data-dirty="true"]').forEach(form => delete form.dataset.dirty);
                inlineMessage(content, '');
                saveDraft();
                reloadInline.blur();
                await fetchPage();
            } catch (error) { inlineMessage(content, error.message, true); }
            return;
        }
        const reload = event.target.closest('[data-ops-reload]');
        if (reload) {
            if (pane.querySelector('form[data-dirty="true"]') && !window.confirm('Discard unsaved entries for this aircraft and reload current server values?')) return;
            const current = detail();
            if (current) { try { sessionStorage.removeItem(draftKey(current)); } catch (_) { /* Refresh still works in memory. */ } }
            pane.replaceChildren();
            try { await fetchPage(selected); notify('Current aircraft values loaded.'); } catch(error) { notify(error.message,true); }
            return;
        }
        const mode = event.target.closest('[data-ops-mode]');
        if (mode && mode.dataset.opsMode !== root.dataset.mode) {
            saveDraft();
            if (await preferences({mode:mode.dataset.opsMode})) {
                // Navigation, not a form save: drafts remain isolated by mode/tail/sort.
                stopLive();
                location.assign(new URL(root.dataset.workspaceUrl,location.origin));
            }
            return;
        }
        const layout = event.target.closest('[data-ops-layout]');
        if (layout) {
            const changes = {layout:layout.dataset.opsLayout};
            if (changes.layout === 'visual' && media.matches && root.dataset.ramp === 'All') changes.ramp = 'Alpha';
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
                form.elements.pass1_surface_area.focus();
            }
            const editor = inline || pane;
            editor.querySelectorAll('[data-subzero-spray-control]').forEach(e => {e.readOnly=e.dataset.sprayReady==='false';});
            editor.querySelectorAll('[data-subzero-spray-save]').forEach(e => {e.hidden=false;}); return;
        }
        const stamp = event.target.closest('[data-subzero-deice-stamp], [data-subzero-stamp]');
        if (stamp) {
            const input = stamp.closest('form').elements[stamp.dataset.subzeroDeiceStamp || stamp.dataset.subzeroStamp];
            input.value = window.NeoAppsClockTime.normalize(shared.operationalHHMM()); input.dispatchEvent(new Event('input',{bubbles:true}));
        }
    });
    root.addEventListener('submit', async event => {
        const form = event.target.closest('form'); if (!form) return;
        event.preventDefault(); if (busy) return;
        if (form.dataset.subzeroConfirm && !window.confirm(form.dataset.subzeroConfirm)) return;
        const inline = inlineFor(form);
        const body = new FormData(form), content = inline || form.closest('[data-ops-detail-content]');
        if (event.submitter?.name) body.set(event.submitter.name,event.submitter.value);
        body.set('target',formAction(form)); body.set('mode',root.dataset.mode);
        body.set('operation_id',root.dataset.operationId); if (content) body.set('physical_tail',content.dataset.tail);
        const key = formKey(form), abort = new AbortController();
        const timeout = setTimeout(()=>abort.abort(),25000);
        busy = true; ++epoch;
        const buttons = [...form.querySelectorAll('button')]; buttons.forEach(button => {button.disabled=true;});
        if (inline) inlineMessage(inline, 'SAVING…');
        else notify('SAVING…');
        try {
            const response = await fetch(root.dataset.mutateUrl,{method:'POST',body,signal:abort.signal});
            let payload; try { payload=await response.json(); } catch (_) { throw new Error(`Save could not be confirmed (${response.status}). Your entries are kept. Check current state before retrying.`); }
            if (!response.ok || !payload.ok) throw new Error(payload.error || 'Save rejected. Your entries are kept.');
            delete form.dataset.dirty;
            saveDraft();
            if (inline) {
                if (form.contains(document.activeElement)) document.activeElement.blur();
                inlineMessage(inline, payload.message);
            }
            else notify(payload.message);
            busy = false;
            await fetchPage(selected,key);
        } catch(error) {
            const message = error.name === 'AbortError' ? 'Save timed out. Your draft is kept. Check current state before retrying; no automatic retry was sent.'
                : error instanceof TypeError ? 'Network error while saving. Your draft is kept. Check current state before retrying; no automatic retry was sent.'
                : error.message;
            if (inline) inlineMessage(inline, message, true);
            else notify(message,true);
        }
        finally { clearTimeout(timeout); busy=false; buttons.forEach(button=>{button.disabled=false;}); }
    });
    const context = root.querySelector('[data-ops-context] form');
    if (context) {
        try {
            const draft = JSON.parse(sessionStorage.getItem(['neoapps.subzero.operations.context',root.dataset.userId,root.dataset.operationId].join(':')) || 'null');
            if (draft) { const safeDraft=draft.filter(saved=>saved.name!=='csrf_token'); sessionStorage.setItem(['neoapps.subzero.operations.context',root.dataset.userId,root.dataset.operationId].join(':'),JSON.stringify(safeDraft)); safeDraft.forEach(saved=>{if (context.elements[saved.name]) context.elements[saved.name].value=saved.value;}); context.dataset.dirty='true'; notify('RESTORED UNSAVED APPLICATION CONTEXT DRAFT'); }
        } catch (_) { /* Current server values remain available. */ }
    }
    if (isPretreat) root.querySelectorAll('[data-ops-pretreat-row]').forEach(row => {
        row.open = !media.matches || row.hasAttribute('data-ops-initial-open');
    });
    tagOrder(); applyLayout(); initialize(true); syncFeedback();
    root.dataset.ready = 'true';
    media.addEventListener('change', () => {
        if (isPretreat) root.querySelectorAll('[data-ops-pretreat-row]').forEach(row => {
            row.open = !media.matches || row.contains(document.activeElement) || !!row.querySelector('form[data-dirty="true"]');
        });
        applyLayout();
    });
    window.addEventListener('beforeunload',()=>{ saveDraft(); stopLive(); });
    window.addEventListener('pagehide',stopLive);
    // A back/forward-cache restore needs a fresh controller and canonical scope;
    // reload restores any saved draft with its original concurrency token.
    window.addEventListener('pageshow',event=>{ if (event.persisted) location.reload(); });
    const interval = Number(root.dataset.refreshIntervalMs);
    if (!window.NeoLiveUpdates || interval < 1000) return;
    controller = window.NeoLiveUpdates.create({intervalMs:interval,continuousWhileVisible:true,poll:async()=>{
        if (busy || preferenceBusy) return;
        const url = new URL(root.dataset.revisionUrl,location.origin); url.searchParams.set('revision',revision);
        url.searchParams.set('mode',root.dataset.mode);
        const response = await fetch(url,{cache:'no-store',signal:navigationAbort.signal});
        if (!response.ok) throw new Error('Unable to refresh operations.');
        const payload = await response.json(); controller.setServerStatus(payload.refresh || {});
        if (payload.changed && !busy && !preferenceBusy) await fetchPage();
    }});
    controller.setServerStatus(JSON.parse(root.dataset.refreshStatus || '{}'));
})();
