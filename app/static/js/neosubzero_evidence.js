(() => {
    'use strict';
    const root=document.querySelector('[data-evidence-root]');
    if (!root) return;
    const output=root.querySelector('[data-evidence-feedback]');
    const notify=(message,error=false)=>{output.hidden=false;output.textContent=message;output.classList.toggle('ops-review',error);};
    let busy=false;
    const key=form=>['subzero.evidence',root.dataset.userId,root.dataset.operationId,form.action,...['record_id','action'].map(n=>form.elements[n]?.value || '')].join(':');
    const forms=[...root.querySelectorAll('[data-evidence-form]')];
    root.querySelector('[data-evidence-reload]')?.addEventListener('click',()=>{
        if (busy) return;
        if (root.querySelector('form[data-dirty="true"]') && !window.confirm('Discard unsaved entries on this screen and reload current server values?')) return;
        try {forms.forEach(form=>sessionStorage.removeItem(key(form)));} catch (_) {notify('Browser storage could not be cleared. Open current values in a new tab.',true);return;}
        location.reload();
    });
    const save=form=>{
        try {sessionStorage.setItem(key(form),JSON.stringify([...new FormData(form)]));} catch (_) { /* Keep the visible draft. */ }
    };
    forms.forEach(form=>{
        try {
            const draft=JSON.parse(sessionStorage.getItem(key(form)) || 'null');
            if (draft) {
                draft.forEach(([name,value])=>{
                    const input=form.elements[name];
                    if (!input || name==='csrf_token') return;
                    if (input.tagName==='SELECT' && value && ![...input.options].some(option=>option.value===value)) {
                        const option=document.createElement('option');option.value=value;option.textContent='PREVIOUS SELECTION ('+value+') — REVIEW';input.append(option);
                    }
                    if (input.type==='checkbox') input.checked=input.value===value; else input.value=value;
                });
                form.dataset.dirty='true';root.dataset.dirty='true';
                notify('Unsaved entries restored with their original version. If rejected, open current values to resolve the conflict.');
            }
        } catch (_) { /* The canonical form stays available. */ }
        form.addEventListener('input',()=>{form.dataset.dirty='true';root.dataset.dirty='true';save(form);});
        form.addEventListener('change',()=>{form.dataset.dirty='true';root.dataset.dirty='true';save(form);});
        form.addEventListener('submit',async event=>{
            event.preventDefault();if(busy)return;
            busy=true;save(form);
            const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),25000);
            const buttons=[...form.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
            notify('SAVING…');
            try {
                const body=new FormData(form);
                if(event.submitter?.name)body.set(event.submitter.name,event.submitter.value);
                const response=await fetch(form.action,{method:'POST',body,signal:abort.signal,headers:{'X-Requested-With':'XMLHttpRequest','Accept':'application/json'}});
                const result=await response.json();
                if(!response.ok || !result.ok)throw new Error(result.error || 'Save rejected. Your entries were kept.');
                try{sessionStorage.removeItem(key(form));sessionStorage.setItem('subzero.evidence.result',result.message || 'Saved.');}catch(_){}
                delete form.dataset.dirty;location.reload();
            } catch(error) {
                notify(error.name==='AbortError' ? 'Save could not be confirmed. Your entries were kept; check current values before retrying. No automatic retry was sent.' : error.message || 'Save failed. Your entries were kept.',true);
            } finally {clearTimeout(timer);busy=false;buttons.forEach(b=>b.disabled=false);}
        });
    });
    try {const result=sessionStorage.getItem('subzero.evidence.result');if(result){notify(result);sessionStorage.removeItem('subzero.evidence.result');}}catch(_){}
})();
