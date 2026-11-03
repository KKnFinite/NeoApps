"""Semantic real-HTML assertions and persisted physical-state snapshots for R03J."""
from copy import deepcopy
from html.parser import HTMLParser
from urllib.parse import urlsplit,parse_qs
from sqlalchemy import event
from app.extensions import db
from app import models
from app.services import neoscorpion as fuel


class Element:
    def __init__(self,tag,attrs=(),parent=None):
        self.tag,self.attrs,self.parent,self.children=tag,dict(attrs),parent,[]

    def select(self,tag=None,attrs=None,css_class=None):
        own=(tag is None or self.tag==tag) and all(k in self.attrs and (v is None or self.attrs[k]==v) for k,v in (attrs or {}).items())
        own=own and (css_class is None or css_class in self.attrs.get('class','').split())
        return ([self] if own else [])+[n for c in self.children if isinstance(c,Element) for n in c.select(tag,attrs,css_class)]

    def text(self):return ''.join(c.text() if isinstance(c,Element) else c for c in self.children)

    def hidden(self):
        return self.tag=='template' or 'hidden' in self.attrs or 'inert' in self.attrs or self.attrs.get('aria-hidden')=='true' or bool(self.parent and self.parent.hidden())

    def values(self):return {n.attrs['name']:n.attrs.get('value','') for n in self.select('input') if 'name' in n.attrs}


class Document(HTMLParser):
    VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
    def __init__(self,html):
        super().__init__(convert_charrefs=True);self.root=Element('document');self.stack=[self.root]
        self.feed(html.decode() if isinstance(html,bytes) else html)
    def handle_starttag(self,tag,attrs):
        node=Element(tag,attrs,self.stack[-1]);self.stack[-1].children.append(node)
        if tag not in self.VOID:self.stack.append(node)
    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in self.VOID:self.handle_endtag(tag)
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,0,-1):
            if self.stack[i].tag==tag:self.stack=self.stack[:i];break
    def handle_data(self,data):self.stack[-1].children.append(data)


def card(case,html,assignment):
    nodes=Document(html).root.select(attrs={'data-fuel-data-card':None,'data-fuel-assignment-id':str(assignment.id)})
    case.assertEqual(len(nodes),1,'Expected one card for the exact assignment')
    case.assertFalse(nodes[0].hidden());return nodes[0]


def off_form(case,node,assignment,*,enabled,reason=None):
    forms=node.select('form',css_class='neoscorpion-fueler-off-form')
    case.assertEqual(len(forms),1)
    form=forms[0];buttons=form.select('button');case.assertEqual(len(buttons),1)
    button=buttons[0]
    case.assertEqual(button.text().strip(),'TRUCK OFF');case.assertEqual(button.attrs.get('type'),'submit')
    case.assertEqual(form.attrs.get('method','').lower(),'post')
    target=urlsplit(form.attrs['action'])
    case.assertTrue(target.path.endswith(f'/assignments/{assignment.id}/off'),form.attrs['action'])
    query=parse_qs(target.query)
    if 'physical_work_id' in query:
        case.assertEqual(query['physical_work_id'],[node.attrs['data-physical-work-id']])
    case.assertFalse(form.hidden());case.assertFalse(button.hidden())
    case.assertEqual('disabled' not in button.attrs,enabled)
    if not enabled:
        case.assertEqual(button.attrs.get('aria-disabled'),'true')
        if reason:case.assertIn(reason,button.attrs.get('title',''))
    return form


def confirmed_off(case,node,time):
    case.assertEqual(node.select('form',css_class='neoscorpion-fueler-off-form'),[])
    finish=node.select(css_class='scorpion-fueler-finish');case.assertEqual(len(finish),1)
    case.assertIn(f'TRUCK OFF {time} · CONFIRMED',' '.join(finish[0].text().split()))
    case.assertFalse(any(b.text().strip()=='MARK OFF' and 'disabled' not in b.attrs and not b.hidden() for b in node.select('button')))


def snapshot():
    names=('NeoScorpionFuelAssignment','NeoScorpionFuelWorkState','NeoScorpionFuelTankState',
        'NeoScorpionFuelingEvent','NeoScorpionSortTruck','NeoScorpionTruckInventoryAudit',
        'NeoScorpionFuelAuditEntry','NeoScorpionSortAssetState','NeoScorpionTailFuelState','SortDateMission')
    data={name:[fuel._fuel_rollback_values(r) for r in db.session.query(getattr(models,name)).populate_existing().order_by(getattr(models,name).id)] for name in names}
    data['event_tanks']=[[fuel._fuel_rollback_values(t) for t in sorted(e.tank_snapshots,key=lambda t:t.tank_code)] for e in models.NeoScorpionFuelingEvent.query.order_by(models.NeoScorpionFuelingEvent.id)]
    return deepcopy(data)


def read_views(case,assignment):
    before=snapshot();writes=[]
    def record(_conn,_cursor,statement,*args):
        if statement.lstrip().split(' ',1)[0].upper() in ('INSERT','UPDATE','DELETE') and 'neoscorpion_' in statement.lower():writes.append(statement)
    event.listen(db.engine,'before_cursor_execute',record)
    try:
        full=case.client.get('/neoscorpion/fueler');case.assertEqual(full.status_code,200)
        live=case.client.get('/neoscorpion/fueler/live-panel');case.assertEqual(live.status_code,200)
        partial=case.client.get(f'/neoscorpion/fueler/assignments/{assignment.id}');case.assertEqual(partial.status_code,200)
    finally:event.remove(db.engine,'before_cursor_execute',record)
    case.assertEqual(writes,[]);case.assertEqual(snapshot(),before)
    return [card(case,full.data,assignment),card(case,live.json['html'],assignment),card(case,partial.json['html'],assignment)]
