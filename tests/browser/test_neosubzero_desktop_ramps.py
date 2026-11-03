"""Desktop ignores mobile ramp preference without changing its durable value."""
import json
import unittest
from datetime import timedelta
from pathlib import Path
from playwright.sync_api import expect
from app.extensions import db
from app.models import User,Gateway,SortDateOperation,SortDateMission,SortDateParkingAssignment,NeoSubZeroUserPreference
from app.services.neosubzero_preferences import set_neosubzero_operations_preferences
from app.services.neosubzero_operations import operations_context
from tests.browser import test_neosubzero_operations as fixtures
from tests import test_neosubzero_ucc as uccfixtures

EVIDENCE=Path('instance/subzero-desktop-ramps/browser')


class DesktopRampsBrowserTest(unittest.TestCase):
    def exercise(self,engine):
        case=fixtures.OperationsBrowserTest();case.setUpClass();browser=None
        EVIDENCE.mkdir(parents=True,exist_ok=True);measurements=[]
        try:
            case.seed()
            with case.app.app_context():
                f=uccfixtures.NeoSubZeroUccTest();f.operation=db.session.get(SortDateOperation,case.operation_id)
                for name,tail,position in (('REMOTE','N-REM','R04'),('REVIEW','N-REV',None)):
                    m=f._departure('UPS-'+name,tail,'DEN',4)
                    if position:db.session.add(SortDateParkingAssignment(sort_date_operation_id=f.operation.id,tail_number=tail,ramp_code='R',position_code=position))
                db.session.commit()
                uid=User.query.filter_by(username='drawer-admin').one().id
            browser=getattr(case.pw,engine).launch()
            context=browser.new_context(viewport={'width':1366,'height':900},service_workers='block')
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            case.login(page)
            for mode in ('pretreat','outbound'):
                for layout in ('list','visual'):
                    with case.app.app_context():
                        user=db.session.get(User,uid);op=db.session.get(SortDateOperation,case.operation_id)
                        set_neosubzero_operations_preferences(user,op,dict(mode=mode,layout=layout,ramp='Bravo'));db.session.commit()
                        expected=[str(row['mission_id']) for row in operations_context(Gateway.query.filter_by(code='RFD').one(),op,mode)['rows']]
                    page.set_viewport_size({'width':1366,'height':900})
                    case.ready(page,'/neosubzero/operations?mode='+mode)
                    root=page.locator('[data-ops-workspace]');board=page.locator('[data-ops-board]')
                    def all_visible():
                        expect(board.locator('[data-ops-row]:visible')).to_have_count(len(expected))
                        expect(page.get_by_label('Ramp filter',exact=True)).to_be_hidden()
                        if layout=='list':self.assertEqual(board.locator('[data-ops-row]:visible').evaluate_all('(rows)=>rows.map(r=>r.dataset.missionId)'),expected)
                        else:
                            self.assertEqual(board.locator('.ops-ramp-column:visible h2').all_text_contents(),['ALPHA','BRAVO','CHARLIE','DELTA','ECHO','REMOTE','LOCATION REVIEW'])
                    all_visible()
                    for width in (320,375,390,430,900,901,1366,1920):
                        page.set_viewport_size({'width':width,'height':900})
                        page.wait_for_function('(w)=>document.documentElement.clientWidth===w',arg=width)
                        if width<=900:
                            expect(page.get_by_label('Ramp filter',exact=True)).to_be_visible()
                            expect(page.get_by_label('Ramp filter',exact=True)).to_have_value('Bravo')
                            expect(board.locator('[data-ops-row]:visible')).to_have_count(1)
                            self.assertEqual(board.locator('[data-ops-row]:visible').get_attribute('data-ramp'),'Bravo')
                            if layout=='visual':expect(board.locator('.ops-ramp-column:visible')).to_have_count(1)
                        else:all_visible()
                        measurements.append(dict(engine=engine,mode=mode,layout=layout,width=width,
                            visible=board.locator('[data-ops-row]:visible').count(),overflow=page.evaluate('document.documentElement.scrollWidth-innerWidth')))
                        self.assertLessEqual(measurements[-1]['overflow'],1)
                        with case.app.app_context():self.assertEqual(NeoSubZeroUserPreference.query.filter_by(user_id=uid).one().operations_ramp,'Bravo')
                    page.screenshot(path=str(EVIDENCE/f'{engine}-{mode}-{layout}-desktop.png'),full_page=True)
                    # A desktop layout change is a layout-only patch, never a
                    # write of the effective All value over mobile's Bravo.
                    other='visual' if layout=='list' else 'list'
                    case.layout(page,other);case.layout(page,layout)
                    with case.app.app_context():self.assertEqual(NeoSubZeroUserPreference.query.filter_by(user_id=uid).one().operations_ramp,'Bravo')
                    all_visible()
                    context_notes=None
                    if mode=='outbound':
                        page.locator('[data-ops-context] summary').click()
                        context_notes=page.locator('[data-ops-context] [name="notes"]')
                        context_notes.fill('Uncommitted draft retained across viewports')
                    with case.app.app_context():
                        m=db.session.get(SortDateMission,case.missions[0]);m.eta_datetime_utc+=timedelta(minutes=1);db.session.commit()
                        fresh=operations_context(Gateway.query.filter_by(code='RFD').one(),db.session.get(SortDateOperation,case.operation_id),mode)['rows']
                        expected=[str(row['mission_id']) for row in fresh]
                        expected_etd=next(row['etd'] for row in fresh if row['mission_id']==m.id)
                    expect(board.locator('[data-ops-row][data-mission-id="%s"] .ops-location'%case.missions[0])).to_contain_text(expected_etd,timeout=10000)
                    all_visible()
                    page.set_viewport_size({'width':390,'height':900})
                    expect(page.get_by_label('Ramp filter',exact=True)).to_have_value('Bravo')
                    expect(board.locator('[data-ops-row]:visible')).to_have_count(1)
                    if context_notes is not None:expect(context_notes).to_have_value('Uncommitted draft retained across viewports')
                    page.screenshot(path=str(EVIDENCE/f'{engine}-{mode}-{layout}-mobile.png'),full_page=True)
            self.assertFalse(errors,errors)
        finally:
            (EVIDENCE/f'{engine}-measurements.json').write_text(json.dumps(measurements,indent=2))
            if browser:browser.close()
            case.tearDownClass()

    def test_chromium_all_desktop_ramps(self):self.exercise('chromium')
    def test_webkit_all_desktop_ramps(self):self.exercise('webkit')
