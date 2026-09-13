(() => {
  'use strict';
  const root = document.querySelector('.neostaffing-people-console');
  if (!root) return;
  const media = matchMedia('(max-width: 980px)');
  let active = null, previous = null, scroll = 0, inert = [];
  const release = () => {
    if (!active) return;
    active.removeAttribute('aria-modal'); active.removeAttribute('role');
    inert.forEach(([node,value]) => { node.inert=value; }); inert=[];
    document.body.style.position=''; document.body.style.top=''; document.body.style.width='';
    window.scrollTo(0,scroll); active=null;
    previous?.focus();
  };
  const sync = () => {
    const next = media.matches ? root.querySelector('details[data-neostaffing-drawer][open] .neostaffing-people-drawer-panel,.neostaffing-people-detail-drawer:not([hidden])') : null;
    if (next === active) return;
    release(); if (!next) return;
    previous=document.activeElement; scroll=window.scrollY; active=next;
    // Inert only siblings on the sheet's ancestor path, never the sheet itself.
    let branch=active;
    while (branch.parentElement) {
      [...branch.parentElement.children].filter(node=>node!==branch).forEach(node=>{inert.push([node,node.inert]);node.inert=true;});
      branch=branch.parentElement;
      if (branch===document.body) break;
    }
    document.body.style.position='fixed';document.body.style.top=`-${scroll}px`;document.body.style.width='100%';
    active.setAttribute('role','dialog');active.setAttribute('aria-modal','true');
    active.querySelector('[data-neostaffing-close-drawer]')?.focus();
  };
  new MutationObserver(sync).observe(root,{subtree:true,attributes:true,attributeFilter:['open','hidden']});
  media.addEventListener('change',sync);
  document.addEventListener('keydown',event=>{
    if (!active || event.key!=='Tab') return;
    const items=[...active.querySelectorAll('button,a[href],input,select,textarea')].filter(node=>!node.disabled&&node.getClientRects().length);
    if (!items.length) return;
    const first=items[0],last=items[items.length-1];
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  });
  sync();
})();
