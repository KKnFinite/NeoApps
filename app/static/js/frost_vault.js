(() => {
    'use strict';
    const init=()=>{
        const root=document.querySelector('[data-frost-vault]');if(!root)return;
        const form=root.querySelector('[data-frost-upload]'),button=form.querySelector('[data-frost-submit]'),output=form.querySelector('output');
        const file=form.querySelector('[type=file]'),filename=form.querySelector('[data-frost-filename]'),progress=form.querySelector('progress');
        let saving=false;
        button.hidden=!file.files.length;
        file.addEventListener('change',()=>{
            filename.textContent=file.files[0]?.name||'No CSV selected';
            button.hidden=!file.files.length;
            if(file.files.length&&!saving){
                if(typeof form.requestSubmit==='function')form.requestSubmit();else button.click();
            }
        });
        const upload=data=>new Promise((resolve,reject)=>{
            const xhr=new XMLHttpRequest();xhr.open('POST',form.getAttribute('action'));
            xhr.timeout=120000;xhr.setRequestHeader('X-Requested-With','XMLHttpRequest');xhr.setRequestHeader('Accept','application/json');
            xhr.upload.addEventListener('progress',event=>{
                if(event.lengthComputable){
                    progress.value=Math.round(event.loaded/event.total*100);
                    output.textContent=event.loaded===event.total?'PROCESSING CSV…':`UPLOADING ${progress.value}%…`;
                }
            });
            xhr.onload=()=>{
                let result;try{result=JSON.parse(xhr.responseText);}catch(_){ /* Non-JSON errors are not success. */ }
                if(xhr.status>=200&&xhr.status<300&&result?.ok)resolve(result);
                else reject(new Error(result?.error||result?.message||(xhr.status===413?'CSV exceeds the 10 MiB upload limit.':xhr.status===403?'You are not authorized to import in this gateway.':
                    'Import was not confirmed. Your file is kept; check the catalog and sign-in before retrying.')));
            };
            xhr.onerror=()=>reject(new Error('Network error. Your selected file is kept. Check the catalog before retrying.'));
            xhr.ontimeout=()=>reject(new Error('Upload timed out. Your selected file is kept. Check the catalog before retrying; exact duplicates are rejected.'));
            xhr.onabort=()=>reject(new Error('Upload interrupted. Your selected file is kept. Check the catalog before retrying.'));
            xhr.send(data);
        });
        form.addEventListener('submit',async event=>{
            event.preventDefault();if(saving||!form.reportValidity())return;
            const data=new FormData(form);saving=true;button.disabled=true;file.disabled=true;
            delete output.dataset.error;progress.hidden=false;progress.value=0;output.textContent='UPLOADING CSV…';form.setAttribute('aria-busy','true');
            try {
                const result=await upload(data);
                output.textContent='IMPORTED · Encrypted original saved.';
                const target=new URL(form.getAttribute('action'),window.location.href);target.searchParams.set('imported',String(result.document_id));
                window.location.assign(target.href);
            }catch(error){output.textContent=error.message;output.dataset.error='true';button.textContent='RETRY UPLOAD';}
            finally {saving=false;button.disabled=false;file.disabled=false;progress.hidden=true;form.removeAttribute('aria-busy');}
        });
        if(window.NeoLiveUpdates){const controller=window.NeoLiveUpdates.create({intervalMs:10000,continuousWhileVisible:true,poll:async()=>{
            const response=await fetch(root.dataset.statusUrl,{credentials:'same-origin',cache:'no-store'});
            if(!response.ok)throw new Error('FROST status refresh failed.');
            const result=await response.json();
            if(result.history){
                const health=result.history;
                const write=(selector,value)=>{const el=root.querySelector(selector);if(el)el.textContent=value;};
                write('[data-history-state]',health.state);
                for(const el of root.querySelectorAll('[data-history-catalog]'))el.textContent=health.catalog[el.dataset.historyCatalog]??'—';
                for(const el of root.querySelectorAll('[data-history-total]'))el.textContent=health.totals[el.dataset.historyTotal]??0;
                write('[data-history-range]',`${health.catalog.date_start||'—'} – ${health.catalog.date_end||'—'}`);
                write('[data-history-pending]',health.pending_partitions);
                write('[data-history-success]',health.last_success||'Not yet processed');
                write('[data-history-errors]',health.errors.map(item=>`${item.month}: ${item.error}`).join(' · '));
                const cohorts=root.querySelector('[data-history-cohorts]');
                if(cohorts){
                    const signature=JSON.stringify(health.descriptive_cohorts);
                    if(cohorts.dataset.signature!==signature){
                        const fragment=document.createDocumentFragment();
                        for(const cohort of health.descriptive_cohorts||[]){
                            const row=document.createElement('tr');
                            const values=[`${cohort.season} · ${cohort.label}`,cohort.applications,
                                ...['temperature_c','spread_c','wind_kt','night','clear_sky'].map(key=>cohort.fields[key]?.mean?.toFixed(2)??'—')];
                            for(const value of values){const cell=document.createElement('td');cell.textContent=String(value);row.append(cell);}
                            fragment.append(row);
                        }
                        cohorts.replaceChildren(fragment);cohorts.dataset.signature=signature;
                    }
                }
                const links=root.querySelector('[data-history-links]');
                if(links){
                    const signature=JSON.stringify(health.partition_links);
                    if(links.dataset.signature!==signature){
                        const fragment=document.createDocumentFragment();
                        for(const item of health.partition_links){
                            const link=document.createElement('a');link.href=`/neosubzero/frost-vault/history/${Number(item.id)}`;
                            link.textContent=`${item.month} · ${item.status.replaceAll('_',' ')}`;fragment.append(link);
                        }
                        links.replaceChildren(fragment);links.dataset.signature=signature;
                    }
                }
            }
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
