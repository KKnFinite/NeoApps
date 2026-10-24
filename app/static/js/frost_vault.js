(() => {
    'use strict';
    const init=()=>{
        const root=document.querySelector('[data-frost-vault]');if(!root)return;
        const form=root.querySelector('[data-frost-upload]'),button=form.querySelector('button'),output=form.querySelector('output');
        let saving=false;
        form.addEventListener('submit',async event=>{
            event.preventDefault();if(saving||!form.reportValidity())return;
            const data=new FormData(form);saving=true;button.disabled=true;output.textContent='IMPORTING…';
            const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
            try {
                const response=await fetch(form.getAttribute('action'),{method:'POST',body:data,credentials:'same-origin',
                    headers:{'X-Requested-With':'XMLHttpRequest','Accept':'application/json'},signal:controller.signal});
                const result=await response.json().catch(()=>null);
                if(!response.ok||!result?.ok)throw new Error(result?.error||result?.message||'Import was not confirmed. The selected file is kept; check the catalog before retrying.');
                window.location.assign(form.getAttribute('action'));
            }catch(error){output.textContent=error.name==='AbortError'?'Import timed out. Check the catalog before retrying; exact duplicates will be rejected.':error.message;output.dataset.error='true';}
            finally {clearTimeout(timer);saving=false;button.disabled=false;}
        });
        if(window.NeoLiveUpdates){const controller=window.NeoLiveUpdates.create({intervalMs:10000,continuousWhileVisible:true,poll:async()=>{
            const response=await fetch(root.dataset.statusUrl,{credentials:'same-origin',cache:'no-store'});
            if(!response.ok)throw new Error('FROST status refresh failed.');
            const result=await response.json();
            for(const row of result.documents){
                const card=root.querySelector(`[data-frost-document="${row.id}"]`);if(!card)continue;
                card.querySelector('[data-frost-delivery]').textContent=row.archived?'OFF-SITE VERIFIED':'ENCRYPTED STAGING · DELIVERY PENDING';
                card.querySelector('[data-frost-error]').textContent=row.error||'';
                card.querySelector('[data-frost-extracted]').hidden=!row.archived;
            }
        }});controller.setServerStatus({auto_refresh_enabled:true});
        window.addEventListener('pagehide',()=>controller.destroy(),{once:true});}
    };
    if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
