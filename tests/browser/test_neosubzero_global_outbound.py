"""Global OUTBOUND desktop controls and shared mobile/column/radio workflow."""
from datetime import datetime,timezone
from pathlib import Path
from unittest import TestCase
from playwright.sync_api import expect
from app.extensions import db
from app.models import SortDateOperation,SortDateMission,SortDateParkingAssignment,User,NeoSubZeroDepartureDeiceEvent
from tests.browser import test_neosubzero_operations as fixtures
from tests import test_neosubzero_ucc as factories


class GlobalOutboundBrowserTest(TestCase):
    def test_chromium_webkit_compact_autosave_and_global_lifecycle(self):
        evidence=Path('instance/browser-evidence/subzero-global-outbound').resolve();evidence.mkdir(parents=True,exist_ok=True)
        for engine in ('chromium','webkit'):
            with self.subTest(engine=engine):
                case=fixtures.OperationsBrowserTest();case.setUpClass();browser=None
                try:
                    with case.app.app_context():
                        fixture=factories.NeoSubZeroUccTest();fixture.operation=db.session.get(SortDateOperation,case.operation_id)
                        mids=[case.flow_id]
                        for i in range(3):
                            m=fixture._departure(f'FIX88{i}',f'N77{i+8}UP','SYN',4)
                            db.session.add(SortDateParkingAssignment(sort_date_operation_id=case.operation_id,tail_number=m.assigned_tail_number,ramp_code='D',position_code=f'D0{3-i}'))
                            mids.append(m.id)
                        db.session.commit()
                    browser=getattr(case.pw,engine).launch();page=browser.new_page(viewport={'width':1366,'height':900},service_workers='block')
                    page.on('dialog',lambda dialog:dialog.accept())
                    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                    page.clock.install(time=datetime(2026,10,23,3,10,tzinfo=timezone.utc))
                    case.login(page);case.ready(page,'/neosubzero/operations?mode=outbound')

                    def row(mid):return page.locator(f'[data-ops-row][data-mission-id="{mid}"]')
                    def acknowledged(control,action):
                        with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/operations/mutate')) as pending:action()
                        self.assertEqual(pending.value.status,200,pending.value.text())
                        self.assertTrue(pending.value.json()['ok'])
                        page.wait_for_function('element => !element.isConnected',arg=control)
                    def quick(mid,action):
                        r=row(mid);old=r.locator('[data-ops-inline]').element_handle()
                        acknowledged(old,lambda:r.locator(f'[data-ops-quick-actions] button[value="{action}"]:visible').first.click())
                    def change(mid,name,value):
                        f=row(mid).locator('.ops-outbound-pass-form');old=f.element_handle()
                        field=f.locator(f'[name="{name}"]')
                        if field.input_value()==value:return  # Unchanged choices intentionally do not autosave.
                        if name in ('treatment_plan','pass1_surface_area','pass2_surface_area'):
                            acknowledged(old,lambda:field.select_option(value))
                        else:
                            field.fill(value)
                            acknowledged(old,lambda:field.press('Tab'))

                    # Every parked aircraft may CONFIGURE; actual START remains exclusive.
                    for mid in mids:
                        quick(mid,'initial_contact');quick(mid,'toggle_configured')
                        expect(row(mid)).to_have_attribute('data-status','configured')
                    quick(mids[-1],'toggle_configured');quick(mids[-1],'reset')
                    expect(row(mids[0])).to_have_attribute('data-status','configured')
                    for index,(mid,plan) in enumerate(zip(mids,('one_type_i','two_type_i','type_i_type_iv'))):
                        print(engine,plan,flush=True)
                        change(mid,'treatment_plan',plan)
                        change(mid,'pass1_surface_area','wings_only')
                        if index:change(mid,'pass2_surface_area','wings_tail')
                        for width in (1024,1366,1920):
                            page.set_viewport_size({'width':width,'height':900})
                            self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                            primary=row(mid).locator('.ops-outbound-primary')
                            expect(primary).to_be_visible();self.assertLess(primary.bounding_box()['height'],160)
                            field=primary.locator('[name="pass1_start"]');expect(field).to_be_editable()
                        if index==2:
                            row(mid).scroll_into_view_if_needed()
                            page.screenshot(path=str(evidence/f'{engine}-desktop-horizontal.png'))
                        change(mid,'pass1_start','22:10');change(mid,'pass1_end','22:15')
                        if index:
                            change(mid,'pass2_start','22:16');change(mid,'pass2_end','22:20')
                        expect(row(mid)).to_have_attribute('data-status','fin')
                        expect(row(mid).locator('.ops-outbound-location')).to_contain_text('PILOT START '+('22:16' if index else '22:10'))
                        quick(mid,'clear');expect(row(mid)).to_have_attribute('data-status','radio-pending')
                        expect(row(mid)).to_have_css('background-color','rgb(240, 45, 159)')
                        self.assertEqual(row(mid).locator('[data-ops-quick-actions] button[value="radio_complete"]').count(),0)
                        # RADIO opens only this mission's inline readback.
                        row(mid).locator('[data-ops-radio]:visible').click()
                        expect(row(mid).locator('[data-radio-script]')).to_be_visible()
                        row(mid).locator('[data-ops-collapse-inline]').click()
                        expect(row(mid)).to_have_attribute('data-status','radio-pending')
                        row(mid).locator('[data-ops-radio]:visible').click()
                        old=row(mid).locator('[data-ops-inline]').element_handle()
                        acknowledged(old,lambda:row(mid).get_by_role('button',name='RADIO COMPLETE',exact=True).click())
                        expect(row(mid)).to_have_attribute('data-status','gone')
                        expect(row(mid)).to_have_css('background-color','rgb(104, 116, 128)')
                        self.assertFalse(row(mid).locator('.ops-outbound-extra').evaluate('el=>el.open'))

                    # Same mission, same controls, column view and phone; no extra windows.
                    page.set_viewport_size({'width':1366,'height':900});case.layout(page,'visual')
                    target=row(mids[-1]);target.locator('.ops-mission-summary').click()
                    expect(target.locator('[data-ops-inline]')).to_be_visible()
                    expect(target.locator('.ops-inline-loading')).to_have_count(0)
                    quick(mids[-1],'initial_contact')
                    change(mids[-1],'treatment_plan','type_i_type_iv')
                    page.set_viewport_size({'width':390,'height':844})
                    expect(target.locator('[data-ops-inline]')).to_be_visible()
                    for group in target.locator('[data-subzero-pass]:visible').all():
                        boxes=group.locator(':scope > label').evaluate_all('es=>es.map(e=>{const r=e.getBoundingClientRect();return {y:r.y,right:r.right}})')
                        self.assertLess(max(b['y'] for b in boxes)-min(b['y'] for b in boxes),1,boxes)
                        self.assertTrue(all(b['right']<=390 for b in boxes),boxes)
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                    target.evaluate('row=>row.scrollIntoView({block:"start"})')
                    page.screenshot(path=str(evidence/f'{engine}-mobile.png'))
                    self.assertEqual(page.get_by_role('dialog').count(),0)
                    self.assertEqual(errors,[])
                except Exception:
                    if browser:
                        page.screenshot(path=str(evidence/f'{engine}-failure.png'),full_page=True)
                        (evidence/f'{engine}-failure.html').write_text(page.content(),encoding='utf-8')
                    raise
                finally:
                    if browser:browser.close()
                    case.tearDownClass()
