/* Only model metadata/approval. No browser or server training path. */
(() => {
    'use strict';
    for (const form of document.querySelectorAll('[data-model-form]')) {
        let saving=false;
        form.addEventListener('submit',async event=>{
            event.preventDefault();if(saving||!form.reportValidity())return;
            const data=new FormData(form),submitter=event.submitter;
            if(submitter?.name)data.set(submitter.name,submitter.value);
            const buttons=[...form.querySelectorAll('button')],disabled=buttons.map(b=>b.disabled),output=form.querySelector('output');
            const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),60000);
            saving=true;buttons.forEach(b=>b.disabled=true);output.textContent='SAVING…';
            try {
                const response=await fetch(form.getAttribute('action'),{method:'POST',body:data,credentials:'same-origin',signal:controller.signal,headers:{'X-Requested-With':'XMLHttpRequest'}});
                if(response.status===413)throw new Error('Model JSON exceeds the 256 KiB upload limit.');
                let result;try{result=await response.json();}catch(_){throw new Error('Model action was not confirmed. Check sign-in and current values.');}
                if(!response.ok||!result.ok)throw new Error(result.error||'Model action was not confirmed.');
                output.textContent=result.message+' Open current model values to review.';
                const approved=document.querySelector('[data-model-approved]');if(approved)approved.textContent=result.models.approved_version||'NONE';
                for(const input of document.querySelectorAll('[data-model-form] [name=revision]'))input.value=result.models.revision;
            }catch(error){output.textContent=(error.name==='AbortError'?'Request timed out; check current values before retrying.':error.message||'Request failed.')+' Your file and entries are retained.';}
            finally{clearTimeout(timeout);saving=false;buttons.forEach((b,i)=>b.disabled=disabled[i]);}
        });
    }
})();
