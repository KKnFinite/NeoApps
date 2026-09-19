(() => {
  'use strict';
  const form = document.querySelector('[data-record-review]');
  if (!form) return;
  const canvas = form.querySelector('[data-signature-pad]'), ctx = canvas.getContext('2d');
  const status = form.querySelector('[data-record-status]');
  let drawing = false, ink = false, busy = false;
  const clear = () => { ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, canvas.width, canvas.height); ink = false; };
  const point = e => { const r = canvas.getBoundingClientRect(); return [(e.clientX-r.left)*canvas.width/r.width,(e.clientY-r.top)*canvas.height/r.height]; };
  clear();
  canvas.addEventListener('pointerdown', e => { if (busy) return; drawing=true; canvas.setPointerCapture(e.pointerId); ctx.beginPath(); ctx.moveTo(...point(e)); });
  canvas.addEventListener('pointermove', e => { if (!drawing) return; ctx.strokeStyle='#111'; ctx.lineWidth=4; ctx.lineCap='round'; ctx.lineTo(...point(e)); ctx.stroke(); ink=true; });
  ['pointerup','pointercancel'].forEach(name => canvas.addEventListener(name, () => { drawing=false; }));
  form.querySelector('[data-clear-signature]').addEventListener('click', () => { if (!busy) clear(); });
  form.addEventListener('submit', async e => {
    e.preventDefault();
    if (busy) return;
    const method=e.submitter?.value;
    if (method==='signature' && !ink) { status.textContent='Draw initials or a signature first.'; return; }
    if (!['rts','signature'].includes(method)) return;
    busy=true; status.textContent='Finalizing…';
    const data=new FormData(form); data.set('method',method);
    try {
      if (method==='signature') { const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png')); if (!blob) throw new Error(); data.set('signature',blob,'signature.png'); }
      const response=await fetch(location.href,{method:'POST',body:data,credentials:'same-origin'});
      if (!response.ok) { status.textContent=await response.text(); return; }
      clear(); location.assign(response.url);
    } catch (_) { status.textContent='Finalization could not be confirmed. Reload and verify the record before retrying.'; }
    finally { busy=false; }
  });
})();
