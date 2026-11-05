"""Exact approved colors/contrast on actual isolated Operations and DUCC pages."""
import json
import re
import unittest
from datetime import datetime
from pathlib import Path
from playwright.sync_api import expect
from tests.browser import test_neosubzero_ucc as fixtures
from tests.browser import test_neosubzero_operations as ops
from app.extensions import db
from app.models import SortDateMission,SortDateOperation,SortDateParkingAssignment,NeoSubZeroDepartureDeiceEvent,User
from app.services.neosubzero_evidence import evidence_values

EVIDENCE=Path('instance/subzero-palette/browser')
PALETTE={
    'planned':('deice-planned','#D4A536'), 'configured':('configured','#278756'),
    'deicing':('deicing','#D9EDF0'), 'fin':('finished','#285DA8'),
    'radio-pending':('radio-pending','#F02D9F'), 'negative':('negative','#C34854'),
    'gone':('gone','#687480'), 'no-deice':(None,'#687480'),
}


def rgb(hex_color):return 'rgb('+', '.join(str(int(hex_color[n:n+2],16)) for n in (1,3,5))+')'


def contrast(first,second):
    def luminance(value):
        channels=[int(n)/255 for n in re.findall(r'\d+',value)[:3]]
        linear=[c/12.92 if c<=.04045 else ((c+.055)/1.055)**2.4 for c in channels]
        return sum(c*weight for c,weight in zip(linear,(.2126,.7152,.0722)))
    high,low=sorted((luminance(first),luminance(second)),reverse=True)
    return (high+.05)/(low+.05)


SNAPSHOT="""elements=>elements.filter(e=>e.getBoundingClientRect().height).map(e=>{
 const style=getComputedStyle(e),box=e.getBoundingClientRect();
 return {id:e.dataset.missionId||e.dataset.uccAircraft,state:e.dataset.status,
   background:style.backgroundColor,color:style.color,border:style.borderTopColor,
   right:box.right,width:box.width,text:[...e.querySelectorAll('span,strong,b,small')]
     .filter(t=>t.textContent.trim()&&t.getBoundingClientRect().height)
     .map(t=>({color:getComputedStyle(t).color,opacity:getComputedStyle(t).opacity}))};
})"""


class PaletteBrowserTest(unittest.TestCase):
    def exercise(self,engine):
        case=fixtures.UccBrowserTest();case.setUpClass();browser=None;measurements=[]
        EVIDENCE.mkdir(parents=True,exist_ok=True)
        try:
            ids={}
            with case.app.app_context():
                operation=db.session.get(SortDateOperation,case.operation_id)
                fixture=fixtures.NeoSubZeroUccTest();fixture.operation=operation
                actor=User.query.filter_by(username='drawer-admin').one()
                for index,status in enumerate(PALETTE):
                    mission=db.session.get(SortDateMission,case.missions[index]) if index<6 else fixture._departure('UPS-P'+str(index),'N-P'+str(index),'SDF',3)
                    mission.actual_block_out_datetime_utc=datetime(2026,10,23,5) if status=='no-deice' else None
                    assignment=SortDateParkingAssignment.query.filter_by(sort_date_operation_id=operation.id,tail_number=mission.assigned_tail_number).one_or_none()
                    if assignment is None:
                        assignment=SortDateParkingAssignment(sort_date_operation_id=operation.id,tail_number=mission.assigned_tail_number)
                        db.session.add(assignment)
                    assignment.ramp_code='A';assignment.position_code=f'A0{index+1}'
                    event=NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=mission.id).one_or_none()
                    if status!='no-deice':
                        event=event or fixture._deice_event(mission,'deice_planned')
                        event.status={'planned':'deice_planned','configured':'configured','deicing':'configured','fin':'finished',
                            'radio-pending':'cleared','negative':'negative','gone':'cleared'}[status]
                        event.treatment_plan='one_type_i' if status!='negative' else None
                        event.pass1_surface_area='wings_only' if status!='negative' else None
                        event.configured_at_utc=datetime(2026,10,23,4) if status not in ('planned','negative') else None
                        event.pass1_started_at_utc=datetime(2026,10,23,4,1) if status in ('deicing','fin','radio-pending','gone') else None
                        event.pass1_ended_at_utc=datetime(2026,10,23,4,5) if status in ('fin','radio-pending','gone') else None
                        event.pass_evidence={'1':{'fluid':'Type I','concentration':50}} if event.pass1_started_at_utc else None
                        event.cleared_at_utc=datetime(2026,10,23,4,6) if event.status=='cleared' else None
                        event.radio_state='pending' if status=='radio-pending' else 'complete' if status=='gone' else None
                        event.radio_completed_at_utc=datetime(2026,10,23,4,7) if status=='gone' else None
                        event.radio_completed_by_user_id=actor.id if status=='gone' else None
                    ids[status]=mission.id
                # A genuine ownership review is a separate amber state, not
                # an alternative treatment color or fabricated spray event.
                review=fixture._departure('UPS-REVIEW','N-REVIEW','DEN',4);review_id=review.id
                db.session.add(SortDateParkingAssignment(sort_date_operation_id=operation.id,tail_number=review.assigned_tail_number,ramp_code='A',position_code='A09'))
                fixture._deice_event(review,'deice_planned');fixture._deice_event(review,'configured')
                db.session.commit()
                original=[evidence_values(e) for e in NeoSubZeroDepartureDeiceEvent.query.order_by(NeoSubZeroDepartureDeiceEvent.id)]
            browser=getattr(case.pw,engine).launch()
            context=browser.new_context(viewport={'width':1440,'height':1000},service_workers='block')
            errors=[];page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            case.login(page);ops.OperationsBrowserTest.ready(case,page,'/neosubzero/operations?mode=outbound')
            for width in (320,375,390,430,1440,1920):
                page.set_viewport_size({'width':width,'height':1000})
                for layout in ('list','visual'):
                    ops.OperationsBrowserTest.layout(case,page,layout)
                    cards={int(row['id']):row for row in page.locator('[data-ops-row]').evaluate_all(SNAPSHOT)}
                    for status,(_,hex_color) in PALETTE.items():
                        card=cards[ids[status]]
                        self.assertEqual(card['state'],'gone' if status=='no-deice' else status)
                        self.check_color(card,hex_color)
                    self.assertEqual(cards[review_id]['state'],'review')
                    self.assertNotIn(cards[review_id]['background'],[rgb(value[1]) for value in PALETTE.values()])
                    overflow=page.evaluate('document.documentElement.scrollWidth-innerWidth')
                    self.assertLessEqual(overflow,1)
                    measurements.append(dict(engine=engine,screen='operations',layout=layout,width=width,overflow=overflow,cards=cards))
                    if width in (390,1920):page.screenshot(path=str(EVIDENCE/f'{engine}-operations-{layout}-{width}.png'),full_page=True)
            ducc=context.new_page();ducc.on('pageerror',lambda e:errors.append(str(e)))
            ducc.set_viewport_size({'width':390,'height':1000});case.ready(ducc,'/neosubzero/ucc?view=ramp&ramp=Alpha')
            for width in (320,375,390,430,1440,1920):
                ducc.set_viewport_size({'width':width,'height':1000})
                expect(ducc.locator('[data-ucc-ramp="Alpha"] .ucc-ramp-diagram')).to_be_visible()
                planes={int(row['id']):row for row in ducc.locator('[data-ucc-aircraft]').evaluate_all(SNAPSHOT)}
                for status,(ducc_state,hex_color) in PALETTE.items():
                    if ducc_state is None:
                        self.assertNotIn(ids[status],planes) # GONE aircraft must not become a phantom in DUCC.
                    else:
                        plane=ducc.locator(f'[data-ucc-aircraft="{ids[status]}"]')
                        expect(plane).to_have_class(re.compile('state-'+ducc_state))
                        if width > 900:
                            # Desktop DUCC now colors only the shared airplane;
                            # its card and labels remain a readable dark surface.
                            expect(plane).to_have_css('background-color','rgb(9, 23, 33)')
                            expect(plane.locator('.ucc-plane-icon')).to_have_css('color',rgb(hex_color))
                        else:
                            self.check_color(planes[ids[status]],hex_color)
                self.assertLessEqual(ducc.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                measurements.append(dict(engine=engine,screen='ducc',width=width,planes=planes))
                if width in (390,1920):ducc.screenshot(path=str(EVIDENCE/f'{engine}-ducc-{width}.png'),full_page=True)
            # Selection/focus/hover remain visible without masking the status.
            for status,(ducc_state,hex_color) in PALETTE.items():
                if ducc_state is None:continue
                plane=ducc.locator(f'[data-ucc-aircraft="{ids[status]}"]')
                plane.hover();expect(plane).to_have_css('background-color','rgb(9, 23, 33)')
                plane.click();expect(plane).to_have_attribute('aria-pressed','true')
                expect(plane).to_have_css('background-color','rgb(9, 23, 33)')
                ducc.keyboard.press('Tab');plane.focus();expect(plane).to_have_css('outline-style','solid')
                expect(plane).to_have_css('outline-width','2px')
            with case.app.app_context():
                self.assertEqual(original,[evidence_values(e) for e in NeoSubZeroDepartureDeiceEvent.query.order_by(NeoSubZeroDepartureDeiceEvent.id)])
            self.assertFalse(errors,errors)
        finally:
            (EVIDENCE/f'{engine}-measurements.json').write_text(json.dumps(measurements,indent=2))
            if browser:browser.close()
            case.tearDownClass()

    def check_color(self,row,hex_color):
        self.assertEqual(row['background'],rgb(hex_color))
        self.assertGreaterEqual(contrast(row['background'],row['color']),4.5)
        for text in row['text']:
            self.assertEqual(float(text['opacity']),1)
            self.assertGreaterEqual(contrast(row['background'],text['color']),4.5)

    def test_chromium_approved_palette(self):self.exercise('chromium')
    def test_webkit_approved_palette(self):self.exercise('webkit')
