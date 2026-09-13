(() => {
  'use strict';
  const root = document.querySelector('[data-timecards]');
  if (!root) return;
  let busy = false;
  root.addEventListener('click', async event => {
    const button = event.target.closest('[data-archive-download]');
    if (!button || busy) return;
    busy = true; button.disabled = true;
    const status = root.querySelector('[data-timecard-status]');
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
    const headers = {'Content-Type':'application/json', ...(csrf ? {'X-CSRFToken':csrf} : {})};
    const week = root.querySelector('[data-archive-week]').value;
    try {
      const response = await fetch('/neostaffing/timecards/archive', {method:'POST', headers,
        body:JSON.stringify({week, complete:button.dataset.archiveDownload === 'complete'})});
      if (!response.ok) throw new Error((await response.json()).error || 'Archive failed.');
      const blob = await response.blob(); // No acknowledgement on an interrupted transfer.
      const token = response.headers.get('X-Timecard-Archive');
      const link = document.createElement('a');
      const url = URL.createObjectURL(blob);
      link.href = url; link.download = `timecards-${week}.zip`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      if (token) {
        const ack = await fetch('/neostaffing/timecards/archive/acknowledge', {method:'POST', headers, body:JSON.stringify({token})});
        if (!ack.ok) throw new Error((await ack.json()).error || 'Download received, but purge was not scheduled.');
        status.textContent = 'Complete archive received. Three-day purge countdown started.';
      } else status.textContent = 'Scoped report downloaded. Retention unchanged.';
    } catch (error) { status.textContent = error.message; }
    finally { busy = false; button.disabled = false; }
  });
})();
