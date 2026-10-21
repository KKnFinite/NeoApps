(() => {
  'use strict';
  const root=document.querySelector('[data-rainrock]');
  const form=root?.querySelector('[data-rainrock-inputs]');
  if(!form)return;
  const inputs=[...form.querySelectorAll('[data-rainrock-field]')];
  const live=root.querySelector('[data-rainrock-live]');
  const status=root.querySelector('[data-rainrock-save-status]');
  const copyStatus=root.querySelector('[data-rainrock-copy-status]');
  const editable=form.dataset.canEdit==='true';
  const dirty=input=>input.value!==input.dataset.savedValue;
  const timers=new Map(),pending=new Set(),errors=new Map();
  let activeSave=null,generation=0,polling=false,capturing=false;
  const showStatus=()=>{
    status.textContent=!editable?'Read-only':errors.size?'Save failed — check marked inputs.':activeSave?'Saving…':inputs.some(dirty)?'Unsaved changes':'Auto-saves';
    status.classList.toggle('is-error',!!errors.size);
    inputs.forEach(input=>{
      root.querySelector(`[data-field-status="${input.name}"]`).textContent=errors.get(input)||'';
      root.querySelector(`[data-rainrock-use-saved="${input.name}"]`).hidden=!errors.has(input);
    });
  };
  const apply=(payload,sent=null)=>{
    if(String(payload.operation_id)!==root.dataset.operation)return;
    if(live.innerHTML!==payload.html)live.innerHTML=payload.html;
    inputs.forEach(input=>{
      const field=payload.fields[input.name];if(!field)return;
      const acknowledged=sent&&Object.hasOwn(sent,input.name);
      if(!acknowledged&&dirty(input))return;
      const unchanged=acknowledged?input.value===sent[input.name]:true;
      input.dataset.savedValue=field.input_value??'';
      input.dataset.version=field.version;
      if(unchanged)input.value=field.input_value??'';
      input.title=field.source;
    });
  };
  const drain=()=>{
    if(activeSave)return activeSave;
    generation++;
    activeSave=(async()=>{
      while(pending.size){
        const changed=[...pending].filter(i=>editable&&!i.readOnly&&dirty(i));pending.clear();
        if(!changed.length)continue;
        const sent=Object.fromEntries(changed.map(i=>[i.name,i.value]));
        const versions=Object.fromEntries(changed.map(i=>[i.name,+i.dataset.version]));
        try{
          const response=await fetch(form.action,{method:'POST',credentials:'same-origin',keepalive:true,
            headers:{'Content-Type':'application/json'},body:JSON.stringify({values:sent,versions})});
          const payload=await response.json().catch(()=>({}));
          if(!response.ok||!payload.ok)throw new Error(payload.error||'Unable to save. Your draft is retained.');
          apply(payload,sent);
          changed.forEach(i=>{errors.delete(i);if(dirty(i)&&!timers.has(i))pending.add(i);});
        }catch(error){changed.forEach(i=>{errors.set(i,error.message);pending.delete(i);});}
      }
    })().finally(()=>{activeSave=null;generation++;showStatus();});
    showStatus();return activeSave;
  };
  const cancel=input=>{clearTimeout(timers.get(input));timers.delete(input);};
  const enqueue=input=>{cancel(input);if(editable&&!input.readOnly&&dirty(input)&&!errors.has(input)){pending.add(input);return drain();}};
  form.addEventListener('input',event=>{
    const input=event.target;if(!inputs.includes(input)||!editable)return;
    errors.delete(input);cancel(input);
    if(dirty(input))timers.set(input,setTimeout(()=>enqueue(input),650));showStatus();
  });
  form.addEventListener('focusout',event=>{if(inputs.includes(event.target))enqueue(event.target);});
  const flush=async()=>{
    inputs.forEach(input=>{cancel(input);if(editable&&dirty(input)&&!errors.has(input))pending.add(input);});
    if(pending.size||activeSave)await drain();
    return !inputs.some(dirty);
  };
  form.addEventListener('submit',event=>{event.preventDefault();flush();});
  form.addEventListener('click',async event=>{
    const button=event.target.closest('[data-rainrock-use-saved]');if(!button)return;
    const input=inputs.find(i=>i.name===button.dataset.rainrockUseSaved);
    if(!input||activeSave)return;
    const draft=input.value,before=++generation;button.disabled=true;cancel(input);pending.delete(input);
    try{
      const response=await fetch(root.dataset.stateUrl,{credentials:'same-origin',cache:'no-store'});
      const payload=await response.json();
      if(!response.ok||!payload.ok)throw new Error('Unable to load saved value. Draft retained.');
      if(generation!==before||input.value!==draft)return;
      apply(payload);
      const field=payload.fields[input.name];input.value=field.input_value??'';
      input.dataset.savedValue=input.value;input.dataset.version=field.version;
      errors.delete(input);input.focus({preventScroll:true});
    }catch(error){errors.set(input,error.message);}
    finally{button.disabled=false;generation++;showStatus();}
  });
  const refresh=async()=>{
    if(document.hidden||activeSave||polling||capturing)return;
    const before=generation;polling=true;
    try{
      const response=await fetch(root.dataset.stateUrl,{credentials:'same-origin',cache:'no-store'});
      const payload=await response.json();
      if(response.ok&&payload.ok&&before===generation&&!activeSave){apply(payload);showStatus();}
    }catch(_){/* Preserve confirmed output and drafts during an outage. */}
    finally{polling=false;}
  };
  const interval=+root.dataset.refreshMs;
  if(interval>0)setInterval(refresh,Math.max(1000,interval));
  window.addEventListener('beforeunload',event=>{if(inputs.some(dirty)){event.preventDefault();event.returnValue='';}});

  async function imageBlob(){
    if(!await flush())throw new Error('Inputs are not saved. Resolve the marked errors before copying.');
    await document.fonts.ready;
    const original=root.querySelector('[data-rainrock-report]');
    const clone=original.cloneNode(true);clone.classList.add('rainrock-export');
    // Stable offscreen snapshot: no inputs, buttons, navigation or viewport crop.
    clone.style.position='absolute';clone.style.left='-100000px';clone.style.top='0';
    root.appendChild(clone);
    try{
      const height=Math.ceil(Math.max(clone.scrollHeight,clone.getBoundingClientRect().height)),width=1200;
      const budget=/iPhone|iPad|iPod/.test(navigator.userAgent)?5000000:64000000;
      const scale=Math.min(2,30000/height,Math.sqrt(budget/(width*height)));
      if(scale<0.5)throw new Error('This report exceeds this browser’s safe image size. Use a desktop browser for the complete export.');
      const canvas=await html2canvas(clone,{backgroundColor:'#080c19',scale,width,height,
        windowWidth:1440,windowHeight:height,scrollX:0,scrollY:0,logging:false,
        onclone:doc=>{const report=doc.querySelector('.rainrock-export');report.style.left='0';report.style.top='0';}});
      if(canvas.width!==Math.floor(width*scale)||canvas.height!==Math.floor(height*scale))throw new Error('Incomplete image capture rejected.');
      return await new Promise((resolve,reject)=>canvas.toBlob(blob=>blob?resolve(blob):reject(new Error('Image encoding failed. Try downloading in a desktop browser.')),'image/png'));
    }finally{clone.remove();}
  }
  const download=blob=>{
    const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;
    a.download='Rainrock-'+root.dataset.operation+'.png';a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);
  };
  async function exportImage(copy){
    if(capturing)return;capturing=true;generation++;copyStatus.textContent='Preparing complete image…';
    root.querySelectorAll('[data-rainrock-copy],[data-rainrock-download]').forEach(b=>b.disabled=true);
    try{
      const promised=imageBlob();
      if(copy&&navigator.clipboard?.write&&window.ClipboardItem){
        try{
          // Call synchronously in the click gesture; Safari accepts a promised
          // PNG while autosave/fonts/capture finish without losing activation.
          await navigator.clipboard.write([new ClipboardItem({'image/png':promised})]);
          copyStatus.textContent='Image copied — ready to paste into your email.';
        }catch(error){
          const blob=await promised;download(blob);
          copyStatus.textContent='Clipboard image copy unavailable. Complete PNG downloaded.';
        }
      }else{download(await promised);copyStatus.textContent=copy?'Clipboard image copy unsupported. Complete PNG downloaded.':'Complete PNG downloaded.';}
    }catch(error){copyStatus.textContent=error.message||'Image capture failed. No partial image was copied.';}
    finally{capturing=false;generation++;root.querySelectorAll('[data-rainrock-copy],[data-rainrock-download]').forEach(b=>b.disabled=false);}
  }
  root.querySelector('[data-rainrock-copy]').addEventListener('click',()=>exportImage(true));
  root.querySelector('[data-rainrock-download]').addEventListener('click',()=>exportImage(false));
})();
