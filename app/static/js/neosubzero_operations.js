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
        form.elements.position_number?.value || '', form.querySelector('input[name="action"]')?.value || ''].join(':');
    const draftKey = detail => ['neoapps.subzero.operations.draft', root.dataset.userId,
        root.dataset.operationId, root.dataset.mode, detail.dataset.missionId, detail.dataset.tail].join(':');
    const detail = () => pane.querySelector('[data-ops-detail-content]');
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
                if (context.dataset.dirty === 'true') sessionStorage.setItem(key,JSON.stringify([...context.elements].filter(e=>e.name).map(e=>({name:e.name,value:e.value}))));
                else sessionStorage.removeItem(key);
            } catch (_) { notify('Context draft remains open. Browser storage is unavailable; save before reloading.',true); }
        }
        const content = detail(); if (!content) return;
        const forms = [...content.querySelectorAll('form[data-dirty="true"]')].map(form => ({
            key: formKey(form), unlocked: form.dataset.unlocked,
            fields: [...form.elements].filter(e => e.name && !['submit', 'button'].includes(e.type)).map(e => ({name:e.name, value:e.value, checked:e.checked}))
        }));
        try {
            if (forms.length) sessionStorage.setItem(draftKey(content), JSON.stringify(forms));
            else sessionStorage.removeItem(draftKey(content));
        } catch (_) { draftMessage('Draft remains open. Browser storage is unavailable; do not reload until saved.'); }
    };
    const sync = form => {
        if (form.matches('[data-subzero-deice-form]')) {
            if (!form.dataset.unlocked) form.dataset.unlocked = form.dataset.locked === 'true' ? 'false' : 'true';
            shared.syncForm(form);
        } else if (form.matches('.neosubzero-treatment-form')) {
            if (!form.dataset.unlocked) form.dataset.unlocked = form.querySelector('[data-subzero-edit]:not([hidden])') || !form.querySelector('button[type="submit"]') ? 'false' : 'true';
            const unlocked = form.dataset.unlocked === 'true';
            const names = ['pass1_start', 'pass1_end', 'pass2_start', 'pass2_end'];
            names.forEach((name, index) => {
                const enabled = unlocked && (!index || !!form.elements[names[index-1]].value);
                form.elements[name].readOnly = !enabled;
                const stamp = form.querySelector(`[data-subzero-stamp="${name}"]`);
                if (stamp) stamp.hidden = !enabled;
            });
            form.querySelectorAll('select').forEach(e => { e.disabled = !unlocked; });
        }
    };
    const initialize = (restore = false) => {
        pane.querySelectorAll('form').forEach(sync);
        if (!restore || !detail()) return;
        let drafts = [];
        try { drafts = JSON.parse(sessionStorage.getItem(draftKey(detail())) || '[]'); } catch (_) { /* current DOM remains usable */ }
        for (const draft of drafts) {
            const form = [...pane.querySelectorAll('form')].find(e => formKey(e) === draft.key);
            if (!form) continue;
            form.dataset.unlocked = draft.unlocked || form.dataset.unlocked;
            draft.fields.forEach(saved => {
                const field = form.elements[saved.name];
                if (!field || !('value' in field)) return;
                field.value = saved.value;
                if (['checkbox', 'radio'].includes(field.type)) field.checked = saved.checked;
            });
            form.dataset.dirty = 'true'; sync(form);
        }
        if (drafts.length) draftMessage('RESTORED UNSAVED DRAFT · Original version retained. Resolve any server conflict before saving.');
    };
    const applyLayout = () => {
        const board = root.querySelector('[data-ops-board]');
        const rows = [...board.querySelectorAll('[data-ops-row]')];
        // Preserve global server ordering while moving rows into/out of columns.
        rows.sort((a,b) => Number(a.dataset.order) - Number(b.dataset.order));
        rows.forEach(row => board.append(row));
        board.querySelectorAll('.ops-ramp-column').forEach(e => e.remove());
        const visual = root.dataset.layout === 'visual';
        const ramp = visual && media.matches && root.dataset.ramp === 'All' ? 'Alpha' : root.dataset.ramp;
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
                rows.filter(row => row.dataset.ramp === name).forEach(row => { row.hidden = root.dataset.ramp !== 'All' && row.dataset.ramp !== root.dataset.ramp; column.append(row); });
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
    const fetchPage = async (mission = selected, saved = null) => {
        const requestEpoch = ++epoch;
        const response = await fetch(currentUrl(mission), {cache:'no-store',signal:navigationAbort.signal});
        if (!response.ok) throw new Error(`Unable to load current operations (${response.status}). Your draft is kept.`);
        const next = new DOMParser().parseFromString(await response.text(), 'text/html').querySelector('[data-ops-workspace]');
        if (!next) throw new Error('Session expired. Sign in in another tab before continuing; your draft is kept.');
        if (requestEpoch !== epoch || busy) return;
        if (next.dataset.operationId !== root.dataset.operationId && dirty()) {
            notify('The canonical sort changed. Draft retained for the original sort; reload current operations in another tab before continuing.', true); return;
        }
        const scroll = window.scrollY, paneScroll = pane.scrollTop;
        if (next.dataset.operationId !== root.dataset.operationId) {
            const previous = root.querySelector('[data-ops-context]');
            const incoming = next.querySelector('[data-ops-context]');
            if (previous && incoming) previous.replaceWith(incoming);
        }
        root.querySelector('[data-ops-board]').replaceWith(next.querySelector('[data-ops-board]'));
        ['operationId','layout','ramp'].forEach(key => { root.dataset[key] = next.dataset[key]; });
        const incoming = next.querySelector('[data-ops-detail-content]');
        const current = detail();
        if (current && incoming && current.dataset.missionId === incoming.dataset.missionId && current.dataset.tail === incoming.dataset.tail && (dirty() || pane.contains(document.activeElement))) {
            const freshForms = [...incoming.querySelectorAll('form')];
            current.querySelectorAll('form').forEach(form => {
                if (form.dataset.dirty === 'true' || (form.contains(document.activeElement) && formKey(form) !== saved)) return;
                const replacement = freshForms.find(e => formKey(e) === formKey(form));
                if (replacement) form.replaceWith(replacement);
            });
            draftMessage('LIVE DATA UPDATED · Unsaved entries and their original versions were kept. Conflicting saves require review.');
            initialize();
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
        const open = event.target.closest('[data-ops-open]');
        if (open) { event.preventDefault(); saveDraft(); const mission = open.closest('[data-ops-row]').dataset.missionId; try { pane.querySelector(':focus')?.blur(); selected = mission; await fetchPage(mission); } catch(error) { notify(error.message,true); } return; }
        const close = event.target.closest('[data-ops-close]');
        if (close) { saveDraft(); pane.hidden = true; selected = null; history.replaceState(null,'',currentUrl(null)); return; }
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
            pane.querySelectorAll('[data-subzero-spray-control]').forEach(e => {e.readOnly=e.dataset.sprayReady==='false';});
            pane.querySelectorAll('[data-subzero-spray-save]').forEach(e => {e.hidden=false;}); return;
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
        const body = new FormData(form), content = form.closest('[data-ops-detail-content]');
        if (event.submitter?.name) body.set(event.submitter.name,event.submitter.value);
        body.set('target',formAction(form)); body.set('mode',root.dataset.mode);
        body.set('operation_id',root.dataset.operationId); if (content) body.set('physical_tail',content.dataset.tail);
        const key = formKey(form), abort = new AbortController();
        const timeout = setTimeout(()=>abort.abort(),25000);
        busy = true; ++epoch;
        const buttons = [...form.querySelectorAll('button')]; buttons.forEach(button => {button.disabled=true;});
        notify('SAVING…');
        try {
            const response = await fetch(root.dataset.mutateUrl,{method:'POST',body,signal:abort.signal});
            let payload; try { payload=await response.json(); } catch (_) { throw new Error(`Save could not be confirmed (${response.status}). Your entries are kept. Check current state before retrying.`); }
            if (!response.ok || !payload.ok) throw new Error(payload.error || 'Save rejected. Your entries are kept.');
            delete form.dataset.dirty; saveDraft(); notify(payload.message);
            busy = false; await fetchPage(selected,key);
        } catch(error) { notify(error.name === 'AbortError' ? 'Save timed out. Your draft is kept. Check current state before retrying; no automatic retry was sent.' : error instanceof TypeError ? 'Network error while saving. Your draft is kept. Check current state before retrying; no automatic retry was sent.' : error.message,true); }
        finally { clearTimeout(timeout); busy=false; buttons.forEach(button=>{button.disabled=false;}); }
    });
    const context = root.querySelector('[data-ops-context] form');
    if (context) {
        try {
            const draft = JSON.parse(sessionStorage.getItem(['neoapps.subzero.operations.context',root.dataset.userId,root.dataset.operationId].join(':')) || 'null');
            if (draft) { draft.forEach(saved=>{if (context.elements[saved.name]) context.elements[saved.name].value=saved.value;}); context.dataset.dirty='true'; notify('RESTORED UNSAVED APPLICATION CONTEXT DRAFT'); }
        } catch (_) { /* Current server values remain available. */ }
    }
    tagOrder(); applyLayout(); initialize(true); syncFeedback();
    root.dataset.ready = 'true';
    media.addEventListener('change',applyLayout);
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
