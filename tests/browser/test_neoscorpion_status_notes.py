"""Painted STATUS notes: Small tier, two lines, row height and live replacement."""
import unittest
from datetime import date,datetime,timedelta
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import User,SortDateOperation,SortDateMission,NeoScorpionSortAssetState
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services import neoscorpion as service
from app.neonodes.neoscorpion import routes
from tests.browser import test_mobile_drawer as fixtures

class StatusNotesBrowserTest(unittest.TestCase):
    def test_small_centered_notes_fit_desktop_mobile_and_refresh(self):
        Fixture=fixtures.MobileDrawerBrowserTest
        Fixture.setUpClass(); browser=None; version=[0]
        Fixture.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=1000
        original=routes._dispatch_presentation_context
        try:
            with Fixture.app.app_context():
                gateway=ensure_default_gateway_and_nodes(); actor=User.query.filter_by(username='drawer-admin').one()
                now=datetime.utcnow()
                operation=SortDateOperation(generated_by_user_id=actor.id,gateway_id=gateway.id,
                    gateway_code=gateway.code,sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(operation); db.session.flush()
                db.session.add(NeoScorpionSortAssetState(sort_date_operation_id=operation.id,revision=1))
                for i in range(9):
                    db.session.add(SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,
                        gateway_code=gateway.code,sort_name='night',mission_type='departure',mission_source='manual',
                        flight_number=f'UPS{700+i}',origin=gateway.code,destination='SDF',assigned_tail_number=f'N{480+i}UP',
                        timezone='America/Chicago',planned_datetime_utc=now+timedelta(hours=2),
                        planned_fuel_load=25000,fuel_status='waiting'))
                db.session.commit(); op_id=operation.id
            def context(gateway,**kwargs):
                result=original(gateway,**kwargs)
                for i,row in enumerate(result['rows']):
                    stage,secondary=[('complete',''),('fueling',''),('fueling',''),('assigned','ARRIVAL UNCONFIRMED'),
                        ('assigned','WAITING FOR TRUCK · TRUCK STILL FUELING'),('ready','Needs Fueler'),('review','Dispatcher review requested'),
                        ('ready','Needs Truck'),('review','Dispatcher review requested')][i]
                    row.update(dispatch_status_key=stage,dispatch_status_label=stage.upper(),fuel_status_secondary=secondary,
                        off_time='00:14' if i==0 else '-',truck_travel_until='',call_dispatch_alert=None,
                        physical_off_forecast=dict(likely=datetime.utcnow()+timedelta(minutes=10),difference=5 if i==2 else 0,source='BASELINE'))
                    if i==8: row['call_dispatch_alert']={'fingerprint':'test-critical-warning'}
                service._attach_dispatch_off_estimates(result,gateway,{}, {})
                if version[0]: result['rows'][1].update(fueler_off_status_note='OVER +5 MIN',fueler_off_prof_difference=5)
                return result
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                    side_effect=lambda gateway,now=None:[db.session.get(SortDateOperation,op_id)]),patch.object(routes,'_dispatch_presentation_context',side_effect=context):
                browser=Fixture.pw.chromium.launch(); page=browser.new_page(viewport={'width':1440,'height':900})
                Fixture().login(page); page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                Fixture().ready(page,'/neoscorpion/fuel-dispatch'); page.evaluate('document.fonts.ready')
                notes=page.locator('.neoscorpion-dispatch-primary-row [data-fuel-status-secondary]')
                expect(notes).to_have_count(9)
                expect(notes.nth(8)).to_have_text('CALL DISPATCH')
                self.assertIn('TRUCK OFF00:14',notes.nth(0).inner_text().replace('\n',''))
                self.assertTrue(notes.nth(1).inner_text().startswith('EST OFF '))
                expect(notes.nth(2)).to_have_text('OVER +5 MIN')
                for width,height in ((1440,98),(390,73),(375,73)):
                    page.set_viewport_size({'width':width,'height':844}); page.wait_for_timeout(100)
                    for note in notes.all():
                        metrics=note.evaluate('''el=>{const s=getComputedStyle(el),r=el.getBoundingClientRect(),cell=el.closest("td").getBoundingClientRect(),row=el.closest("tr"),p=el.parentElement.querySelector(".neoscorpion-fuel-status-chip").getBoundingClientRect();return {font:parseFloat(s.fontSize),small:parseFloat(getComputedStyle(row.querySelector(".neoscorpion-dispatch-size-small")).fontSize),lines:r.height/parseFloat(s.lineHeight),left:r.left,right:r.right,cellLeft:cell.left,cellRight:cell.right,center:(r.left+r.right)/2,pillCenter:(p.left+p.right)/2,needed:el.scrollWidth,overflow:el.scrollWidth>el.clientWidth+1,rowHeight:row.getBoundingClientRect().height,rule:getComputedStyle(row.closest("table")).getPropertyValue("--dispatch-row-height").trim()}}''')
                        self.assertAlmostEqual(metrics['font'],metrics['small'],delta=.1)
                        self.assertLessEqual(metrics['lines'],2.1,(width,note.inner_text(),metrics))
                        self.assertFalse(metrics['overflow'],(width,note.inner_text(),metrics))
                        self.assertGreaterEqual(metrics['left'],metrics['cellLeft']-1)
                        self.assertLessEqual(metrics['right'],metrics['cellRight']+1,(width,note.inner_text(),metrics))
                        self.assertAlmostEqual(metrics['center'],metrics['pillCenter'],delta=1)
                        self.assertEqual(metrics['rule'],f'{height}px')
                        self.assertLessEqual(metrics['rowHeight'],height+1,(width,note.inner_text(),metrics))
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                version[0]=1
                with Fixture.app.app_context():
                    NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=op_id).one().revision+=1; db.session.commit()
                expect(notes.nth(1)).to_have_text('OVER +5 MIN',timeout=15000)
                self.assertNotIn('BASELINE',notes.nth(1).inner_text())
                self.assertIn('BASELINE',notes.nth(1).get_attribute('title'))
        finally:
            if browser: browser.close()
            Fixture.tearDownClass()
