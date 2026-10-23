(() => {
    'use strict';
    const root=document.querySelector('.sz-log');
    if (!root) return;
    const feedback=root.querySelector('[data-spray-copy-feedback]');
    root.addEventListener('click',async event=>{
        const button=event.target.closest('[data-spray-copy]');
        if (!button) return;
        const text=document.getElementById(button.dataset.sprayCopy)?.textContent;
        if (text===undefined) return;
        button.disabled=true;
        try {
            if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
            else throw new Error('clipboard-unavailable');
            feedback.textContent='COPIED — ready for manual reporting.';
        } catch (_) {
            const field=document.createElement('textarea');field.value=text;
            field.setAttribute('aria-label','Report copy fallback');root.append(field);field.focus();field.select();
            let copied=false;
            try {copied=document.execCommand('copy');} catch (_) { /* Manual fallback stays selected. */ }
            if(copied){field.remove();feedback.textContent='COPIED — ready for manual reporting.';}
            else feedback.textContent='Clipboard unavailable. The complete report is selected below; copy it manually.';
        } finally {button.disabled=false;feedback.hidden=false;}
    });
})();
