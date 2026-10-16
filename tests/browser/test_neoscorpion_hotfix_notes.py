"""Real Hanzo/Dispatch notes, open DETAILS and COPY through existing refresh."""
from datetime import date,datetime,timedelta
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import User,SortDateOperation,SortDateMission,NeoScorpionFuelAssignment
from app.services import neoscorpion as service
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as fixtures

class NotesBrowserTest(unittest.TestCase):
    def test_chromium_webkit_notes_and_load_plan_refresh_desktop_mobile(self):
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass();F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=1000
        try:
            with F.app.app_context():
                gateway=ensure_default_gateway_and_nodes();user=User.query.filter_by(username='drawer-admin').one()
                op=SortDateOperation(generated_by_user_id=user.id,gateway_id=gateway.id,gateway_code=gateway.code,sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(op);db.session.flush()
                mission=SortDateMission(sort_date_operation_id=op.id,sort_date=op.sort_date,gateway_code=gateway.code,sort_name='night',mission_type='departure',mission_source='manual',flight_number='UPS801',origin=gateway.code,destination='SDF',assigned_tail_number='N123UP',planned_datetime_utc=datetime.utcnow()+timedelta(hours=2),timezone='America/Chicago',planned_fuel_load=50000)
                db.session.add(mission);db.session.flush();a=NeoScorpionFuelAssignment(sort_date_operation_id=op.id,sort_date_mission_id=mission.id,assigned_fueler_user_id=user.id,confirmed_tail_number='N123UP');db.session.add(a);db.session.commit()
                oid,aid,mid,gid,uid=op.id,a.id,mission.id,gateway.id,user.id
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',side_effect=lambda gateway,now=None:[db.session.get(SortDateOperation,oid)]):
                for engine in (F.pw.chromium,F.pw.webkit):
                    browser=engine.launch()
                    try:
                        context=browser.new_context(viewport={'width':1440,'height':900});page=context.new_page();F().login(page);page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                        F().ready(page,'/neoscorpion/fuel-dispatch');page.locator('[data-neoscorpion-dispatch-details]').first.click()
                        detail=page.locator(f'#neoscorpion-dispatch-detail-{mid}-1')
                        hanzo=page.context.new_page();F().ready(hanzo,'/neoscorpion/hanzo')
                        text='Live note '+engine.name+' <unsafe>'
                        with F.app.app_context():
                            values={f'remaining_{code}':'10' for code in ('l_out','l_in','ctr','r_in','r_out','tt')}
                            service.save_fueler_entry(db.session.get(type(gateway),gid),db.session.get(User,uid),dict(assignment_id=aid,notes=text,apu_running='no',actual_ctr='3.5',**values));db.session.commit()
                        page.bring_to_front();expect(detail.locator('[data-fueler-notes]')).to_have_text(text,timeout=20000)
                        expect(detail).to_be_visible();output=detail.locator('.neoscorpion-load-planning-output')
                        expect(output).to_have_text('UPS801 SDF N123UP CF > 3500',timeout=15000)
                        self.assertEqual(detail.locator('[data-copy-value]').get_attribute('data-copy-value'),output.inner_text())
                        page.evaluate("Object.defineProperty(navigator,'clipboard',{value:{writeText:async value=>window.copied=value},configurable:true})")
                        detail.locator('[data-copy-value]').click();expect(detail.locator('[data-copy-value]')).to_have_text('COPIED')
                        self.assertEqual(page.evaluate('window.copied'),output.inner_text())
                        hanzo.bring_to_front();expect(hanzo.locator('[data-fueler-notes]')).to_have_text(text,timeout=20000)
                        for width in (1440,390):
                            hanzo.set_viewport_size({'width':width,'height':844})
                            expect(hanzo.locator('[data-fueler-notes]')).to_be_visible()
                            self.assertFalse(hanzo.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                            page.set_viewport_size({'width':width,'height':844})
                            self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                    finally:browser.close()
        finally:F.tearDownClass()
