/* Reuse the canonical assignment revision; only replace read-only regions. */
(() => {'use strict';
 document.addEventListener('change',event=>{const select=event.target.closest('[data-vendor-driver-assignment] select[name=user_id]');if(select)select.form.elements.expected_driver_truck_id.value=select.selectedOptions[0]?.dataset.currentTruck||'';});
 document.addEventListener('click',event=>{const button=event.target.closest('[data-fuel-verification-skip]');if(button)button.closest('details').open=false;});
 const root=document.querySelector('[data-vendor-refresh]');if(!root)return;
 let revision=null,busy=false;
 async function refresh(){if(busy||document.hidden)return;busy=true;
  try{const response=await fetch(root.dataset.revisionUrl,{credentials:'same-origin',cache:'no-store'});
   if(response.status===403){document.querySelectorAll('[data-vendor-live]').forEach(n=>n.textContent='Operational access revoked. Open MY ACCOUNT.');return;}
   if(!response.ok)return;const next=await response.json();const value=JSON.stringify(next);
   if(revision!==null&&revision!==value){
    if(document.activeElement?.closest('form'))return;
    const page=await fetch(location.href,{credentials:'same-origin',cache:'no-store'});if(!page.ok)return;
    const doc=new DOMParser().parseFromString(await page.text(),'text/html');
    const old=document.querySelector('[data-vendor-live]'),fresh=doc.querySelector('[data-vendor-live]');
    if(old&&fresh)old.replaceChildren(...fresh.childNodes);else return;
   }revision=value;
  }catch(_){/* A transient outage never resets a form or retries a mutation. */}finally{busy=false;}
 }refresh();setInterval(refresh,10000);
})();
