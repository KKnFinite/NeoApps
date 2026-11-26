(() => {
  'use strict';
  const root=document.querySelector('[data-neorain-reports]');
  const form=root?.querySelector('[data-report-inputs]');
  if(!form)return;
  const tabButtons = [...root.querySelectorAll('[data-report-tab]')];
  const panels = [...root.querySelectorAll('[data-report-panel]')];
  const preference = 'neoapps.neorain.report-tab';
  function selectTab(name, persist = false) {
    const tab = tabButtons.find(button => button.dataset.reportTab === name && !button.hidden);
    if (!tab) return;
    for (const button of tabButtons) {
      button.setAttribute('aria-selected', String(button === tab));
      button.tabIndex = button === tab ? 0 : -1;
    }
    panels.forEach(panel => { panel.hidden = panel.dataset.reportPanel !== name; });
    root.querySelector('[data-sort-tab]').value = name;
    if (persist) {
      try { localStorage.setItem(preference, name); } catch (_) { /* Tab selection still works. */ }
      const url = new URL(root.dataset.reportsUrl, location.origin);
      url.searchParams.set('operation_id', root.dataset.operation);
      url.searchParams.set('tab', name);
      history.replaceState(history.state, '', url);
    }
  }
  if (root.dataset.explicitTab !== 'true') {
    try { selectTab(localStorage.getItem(preference)); } catch (_) { /* Keep the server selection. */ }
  }
  for (const button of tabButtons) {
    button.addEventListener('click', () => selectTab(button.dataset.reportTab, true));
    button.addEventListener('keydown', event => {
      const available = tabButtons.filter(tab => !tab.hidden);
      const index = available.indexOf(button);
      const next = {
        ArrowLeft: (index + available.length - 1) % available.length,
        ArrowRight: (index + 1) % available.length,
        Home: 0,
        End: available.length - 1,
      }[event.key];
      if (next === undefined) return;
      event.preventDefault();
      const tab = available[next];
      selectTab(tab.dataset.reportTab, true);
      tab.focus();
    });
  }
  const inputs=[...form.querySelectorAll('[data-report-field]')];
  const live=root.querySelector('[data-rainrock-live]');
  const notesToggle=root.querySelector('[data-rainrock-notes]');
  const updateNotes=()=>{
    if (!notesToggle) return;
    live.querySelectorAll('[data-rainrock-note-variant]').forEach(detail=>{
      detail.hidden=(detail.dataset.rainrockNoteVariant==='full')!==notesToggle.checked;
    });
  };
  notesToggle?.addEventListener('change',updateNotes);
  const status=root.querySelector('[data-report-save-status]');
  const copyStatus=root.querySelector('[data-rainrock-copy-status]');
  let editable=form.dataset.canEdit==='true';
  const dirty=input=>input.value!==input.dataset.savedValue;
  const timers=new Map(),pending=new Set(),errors=new Map();
  let activeSave=null,generation=0,polling=false,capturing=false;
  const showStatus=()=>{
    status.textContent=!editable?'Read-only':errors.size?'Save failed — check marked inputs.':activeSave?'Saving…':inputs.some(dirty)?'Unsaved changes':'Auto-saves';
    status.classList.toggle('is-error',!!errors.size);
    inputs.forEach(input=>{
      root.querySelector(`[data-field-status="${input.name}"]`).textContent=errors.get(input)||'';
      root.querySelector(`[data-report-use-saved="${input.name}"]`).hidden=!errors.has(input);
    });
  };
  let lastReportHtml;
  const apply=(payload,sent=null)=>{
    if(String(payload.operation_id)!==root.dataset.operation)return;
    for (const button of tabButtons) {
      button.hidden = button.dataset.reportTab === 'rainrock' ? payload.rainrock_html == null : payload.briefing == null;
    }
    const currentTab = tabButtons.find(button => button.getAttribute('aria-selected') === 'true' && !button.hidden);
    const fallback = currentTab || tabButtons.find(button => !button.hidden);
    if (fallback) selectTab(fallback.dataset.reportTab);
    if (live && lastReportHtml !== payload.rainrock_html) {
      if (payload.rainrock_html == null) live.replaceChildren();
      else window.NeoRainBoard.updateReport(live, payload.rainrock_html);
      lastReportHtml = payload.rainrock_html;
      updateNotes();
    }
    root.dispatchEvent(new CustomEvent('neorain:reports-updated', {detail: payload}));
    editable = payload.can_edit;
    inputs.forEach(input=>{
      const field=payload.fields[input.name];
      input.readOnly = !editable || !field;
      input.closest('label').hidden = !field && !dirty(input);
      if (!field) return;
      const acknowledged=sent&&Object.hasOwn(sent,input.name);
      if(!acknowledged&&dirty(input))return;
      const unchanged=acknowledged?input.value===sent[input.name]:true;
      input.dataset.savedValue=field.input_value??'';
      input.dataset.version=field.version;
      if(unchanged)input.value=field.input_value??'';
      const inherited = field.value != null && field.value !== input.dataset.savedValue;
      input.title = inherited ? field.source + ' · ' + field.value : field.source;
      if (input.tagName === 'INPUT') input.placeholder = inherited ? field.value : '—';
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
  root.flushReportInputs = flush;
  form.addEventListener('submit',event=>{event.preventDefault();flush();});
  form.addEventListener('click',async event=>{
    const button=event.target.closest('[data-report-use-saved]');if(!button)return;
    const input=inputs.find(i=>i.name===button.dataset.reportUseSaved);
    if(!input||activeSave)return;
    const draft=input.value,before=++generation;button.disabled=true;cancel(input);pending.delete(input);
    try{
      const response=await fetch(root.dataset.stateUrl,{credentials:'same-origin',cache:'no-store'});
      const payload=await response.json();
      if(!response.ok||!payload.ok)throw new Error('Unable to load saved value. Draft retained.');
      if(generation!==before||input.value!==draft)return;
      apply(payload);
      const field=payload.fields[input.name];
      if (!field) throw new Error('This input is no longer available.');
      input.value=field.input_value??'';
      input.dataset.savedValue=input.value;input.dataset.version=field.version;
      errors.delete(input);input.focus({preventScroll:true});
    }catch(error){errors.set(input,error.message);}
    finally{button.disabled=false;generation++;showStatus();}
  });
  let sortEndMs = Number(root.dataset.sortEndMs);
  const refresh=async()=>{
    if (!(Date.now() < sortEndMs)) { clearInterval(refreshTimer); return; }
    if (Date.now() < Number(root.dataset.sortStartMs) || !window.NeoLiveUpdates?.insidePhysicalSort()) return;
    if(document.hidden||activeSave||polling||capturing)return;
    const before=generation;polling=true;
    try{
      const response=await fetch(root.dataset.autoStateUrl,{credentials:'same-origin',cache:'no-store'});
      const payload=await response.json();
      if (Date.now() < Number(root.dataset.sortStartMs) || Date.now() >= sortEndMs || !window.NeoLiveUpdates?.insidePhysicalSort()) return;
      if (payload.refresh) sortEndMs = Number(payload.refresh.sort_end_ms);
      if (!(Date.now() < sortEndMs)) { clearInterval(refreshTimer); return; }
      if(response.ok&&payload.ok&&before===generation&&!activeSave){apply(payload);showStatus();}
    }catch(_){/* Preserve confirmed output and drafts during an outage. */}
    finally{polling=false;}
  };
  const interval=+root.dataset.refreshMs;
  const refreshTimer = root.dataset.autoStateUrl && Date.now() < sortEndMs && interval > 0
    ? setInterval(refresh, Math.max(1000, interval))
    : null;
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
  root.querySelector('[data-rainrock-copy]')?.addEventListener('click',()=>exportImage(true));
  root.querySelector('[data-rainrock-download]')?.addEventListener('click',()=>exportImage(false));
})();
