"""OOS replacement -> explicit removal -> existing Fueler live refresh, both engines."""
import unittest
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from app.extensions import db
from app.models import NeoScorpionFuelAssignment, NeoScorpionFuelWorkState, NeoScorpionFuelingEvent, SortDateMission, SortDateTailState
from app.services.parking_plan import set_tail_out_of_service
from tests import test_neoscorpion_remove_assignment as fixtures

EVIDENCE=Path('instance/remove-assignment-20261104/browser')


class RemoveAssignmentBrowserTest(unittest.TestCase):
    def test_chromium_webkit_oos_replacement_confirmation_and_live_removal(self):
        EVIDENCE.mkdir(parents=True,exist_ok=True)
        with sync_playwright() as pw:
            for engine in ('chromium','webkit'):
                with self.subTest(engine=engine):
                    case=fixtures.RemoveAssignmentTest();case.setUp();case.prepare()
                    app=case.app
                    app.config.update(SESSION_COOKIE_SECURE=False,LIVE_SCREEN_REFRESH_INTERVAL_MS=5000,
                        GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=False,GOOGLE_MOTHERBRAIN_READER_ENABLED=False,FLIGHT_API_ENABLED=False)
                    aid,mid,wid=case.assignment.id,case.mission.id,case.work.id
                    server=make_server('127.0.0.1',0,app,threaded=True)
                    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
                    origin=f'http://127.0.0.1:{server.server_port}'
                    browser=getattr(pw,engine).launch();errors=[]
                    try:
                        def login(user,width):
                            ctx=browser.new_context(viewport={'width':width,'height':900},service_workers='block')
                            page=ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                            page.goto(origin+'/login');page.locator('[name=email]').fill(user.email)
                            page.locator('[name=password]').fill('TestPassword123!')
                            page.locator('button[type=submit]').click();page.wait_for_url(lambda url:'/login' not in url)
                            page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                            return page
                        a=login(case.fueler,390);d=login(case.dispatcher,1440)
                        a.goto(origin+'/neoscorpion/fueler')
                        expect(a.locator(f'[data-fuel-assignment-id="{aid}"]').first).to_be_visible()
                        a.screenshot(path=str(EVIDENCE/f'{engine}-fueler-before.png'),full_page=True)
                        # Synthetic canonical OOS action; mission replacement is a
                        # MotherBrain fact, not a Scorpion cancellation instruction.
                        with app.app_context():
                            set_tail_out_of_service(db.session.get(type(case.operation),case.operation.id),'N412UP',True,
                                user=db.session.get(type(case.dispatcher),case.dispatcher.id))
                            db.session.get(SortDateMission,mid).assigned_tail_number='N919UP';db.session.commit()
                        d.goto(origin+'/neoscorpion/fuel-dispatch')
                        d.locator('[data-neoscorpion-dispatch-details]').first.click()
                        d.get_by_role('button',name='REMOVE ASSIGNMENT',exact=True).click()
                        form=d.locator('form[action$="/remove-assignment"]')
                        expect(form).to_be_visible()
                        form.locator('[name=reason]').fill('Old aircraft OOS; replace obsolete assignment')
                        form.locator('[name=confirm_remove]').check()
                        d.screenshot(path=str(EVIDENCE/f'{engine}-dispatcher-confirm.png'),full_page=True)
                        d.once('dialog',lambda dialog:dialog.dismiss())
                        form.get_by_role('button',name='CONFIRM REMOVE',exact=True).click()
                        with app.app_context():
                            self.assertIsNotNone(db.session.get(NeoScorpionFuelAssignment,aid).assigned_fueler_user_id)
                        d.once('dialog',lambda dialog:dialog.accept())
                        with d.expect_response(lambda r:'/fuel-dispatch/remove-assignment' in r.url and r.request.method=='POST') as result:
                            form.get_by_role('button',name='CONFIRM REMOVE',exact=True).click()
                        self.assertEqual(result.value.status,200)
                        a.bring_to_front()
                        # No reload: use the shipped revision-first live controller.
                        expect(a.locator(f'[data-fuel-assignment-id="{aid}"]')).to_have_count(0,timeout=20000)
                        a.screenshot(path=str(EVIDENCE/f'{engine}-fueler-removed.png'),full_page=True)
                        with app.app_context():
                            self.assertEqual(db.session.get(SortDateMission,mid).assigned_tail_number,'N919UP')
                            self.assertTrue(SortDateTailState.query.filter_by(tail_number='N412UP').one().is_out_of_service)
                            work=db.session.get(NeoScorpionFuelWorkState,wid)
                            self.assertEqual(len(work.tank_states),3)
                            self.assertIsNone(work.manual_on_at_utc);self.assertIsNone(work.off_at_utc)
                            self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
                        self.assertFalse(errors,errors)
                    finally:
                        browser.close();server.shutdown();thread.join(timeout=5);case.tearDown()
