"""Global OUTBOUND desktop controls and shared mobile/column/radio workflow."""
from datetime import datetime,timezone
from pathlib import Path
from unittest import TestCase
from playwright.sync_api import expect
from app.extensions import db
from app.models import SortDateOperation, SortDateParkingAssignment, NeoSubZeroAuditEntry, NeoSubZeroDepartureDeiceEvent
from tests.browser import test_neosubzero_operations as fixtures
from tests import test_neosubzero_ucc as factories


class GlobalOutboundBrowserTest(TestCase):
    def test_chromium_webkit_compact_autosave_and_global_lifecycle(self):
        evidence=Path('instance/browser-evidence/subzero-outbound-ui').resolve();evidence.mkdir(parents=True,exist_ok=True)
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
                    dialogs=[]
                    def accept(dialog):
                        dialogs.append(dialog.message)
                        dialog.accept()
                    page.on('dialog',accept)
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

                    def inspect_frost(mid):
                        mission = row(mid)
                        frost = mission.get_by_role('button', name='FROST PRESENT', exact=True)
                        expect(frost).to_be_visible()
                        for field in ('frost_result', 'inspection_location', 'inspected_local', 'inspection_notes'):
                            self.assertEqual(mission.locator(f'[name="{field}"]:visible').count(), 0)
                        old = mission.locator('[data-ops-inline]').element_handle()
                        acknowledged(old, frost.click)
                        expect(mission.get_by_text('FROST PRESENT · RECORDED', exact=True)).to_be_visible()
                        with case.app.app_context():
                            self.assertEqual(NeoSubZeroAuditEntry.query.filter_by(mission_id=mid, action='departure_frost_present').count(), 1)

                    def nervous_captain(mid):
                        mission = row(mid)
                        old = mission.locator('[data-ops-inline]').element_handle()
                        acknowledged(old, mission.get_by_role('button', name='NERVOUS CAPTAIN', exact=True).click)
                        expect(mission.get_by_text('NERVOUS CAPTAIN · RECORDED', exact=True)).to_be_visible()
                        expect(mission.locator('[name="reason_for_application"]:visible')).to_have_value('Nervous Captain')
                        with case.app.app_context():
                            audit = NeoSubZeroAuditEntry.query.filter_by(mission_id=mid, action='departure_nervous_captain').one()
                            self.assertEqual(audit.after_values['reason_for_application'], 'Nervous Captain')
                            self.assertEqual(audit.after_values['frost_observation'], 'absent')
                            self.assertTrue(audit.actor_user_id)
                            self.assertIsNotNone(audit.recorded_at_utc)

                    def desktop_evidence(mid):
                        planned = row(mids[-1])
                        fin = row(mid)
                        expect(planned).to_have_css('background-color','rgb(212, 165, 54)')
                        expect(fin).to_have_css('background-color','rgb(40, 93, 168)')
                        for layout in ('list','visual'):
                            case.layout(page,layout)
                            expect(page.locator('[data-ops-workspace]')).to_have_attribute('data-layout',layout)
                            chevron = planned.locator('.ops-disclosure-chevron:visible')
                            if chevron.get_attribute('aria-expanded') == 'false':
                                chevron.click()
                            expect(chevron).to_have_attribute('aria-expanded','true')
                            expect(planned.locator('[data-ops-collapse-inline]')).to_be_hidden()
                            for width in (1366,1920,2560):
                                page.set_viewport_size({'width':width,'height':960})
                                self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                                position = chevron.evaluate('''el => {
                                    const b = el.getBoundingClientRect();
                                    const r = el.closest('[data-ops-row]').getBoundingClientRect();
                                    return {top:b.top-r.top,right:r.right-b.right,height:b.height};
                                }''')
                                self.assertLess(position['top'],9)
                                self.assertLess(position['right'],9)
                                self.assertLessEqual(position['height'],28)
                                plan = planned.locator('[name="treatment_plan"]:visible')
                                page.mouse.move(10,10)
                                expect(plan).to_have_css('background-color','rgb(38, 39, 36)')
                                expect(plan).to_have_css('color','rgb(250, 249, 242)')
                                expect(planned.locator('[data-subzero-pass="1"] legend')).to_have_css('color','rgb(255, 179, 97)')
                                expect(planned.locator('[data-subzero-pass="2"] legend')).to_have_css('color','rgb(119, 229, 161)')
                                if layout == 'list':
                                    pilot_start = fin.locator('.ops-outbound-location .ops-pilot-start')
                                    self.assertTrue(pilot_start.evaluate('el => el.scrollWidth <= el.clientWidth + 1'))
                                    primary = planned.locator('.ops-outbound-primary')
                                    headers = page.locator('.ops-outbound-columns > span').all()
                                    cells = primary.locator('.ops-outbound-identity,.ops-outbound-location,.ops-outbound-status,.ops-outbound-plan,.ops-outbound-reason,[data-subzero-pass]').all()
                                    cells.sort(key=lambda cell:cell.bounding_box()['x'])
                                    self.assertEqual(len(cells),7)
                                    for header,cell in zip(headers,cells):
                                        self.assertAlmostEqual(header.bounding_box()['x'],cell.bounding_box()['x'],delta=1)
                                    controls = primary.locator('select:visible,[data-clock-time]:visible').all()
                                    boxes = [control.bounding_box() for control in controls]
                                    self.assertLessEqual(max(b['y'] for b in boxes)-min(b['y'] for b in boxes),1)
                                    self.assertEqual({b['height'] for b in boxes},{30})
                                    actions = primary.locator('[data-ops-quick-actions] button').all()
                                    self.assertEqual(len({b.bounding_box()['y'] for b in actions}),1)
                                page.evaluate('window.scrollTo(0,0)')
                                page.screenshot(path=str(evidence/f'{engine}-{layout}-{width}.png'),full_page=True)
                            if layout == 'visual':
                                nervous_captain(mids[-1])
                            chevron.click()
                            expect(chevron).to_have_attribute('aria-expanded','false')
                        case.layout(page,'list')

                    # Every parked aircraft may CONFIGURE; actual START remains exclusive.
                    for mid in mids:
                        quick(mid,'initial_contact');quick(mid,'toggle_configured')
                        expect(row(mid)).to_have_attribute('data-status','configured')
                    quick(mids[-1],'toggle_configured');quick(mids[-1],'reset')
                    self.assertEqual(dialogs,[])
                    quick(mids[-1],'initial_contact')
                    change(mids[-1],'treatment_plan','type_i_type_iv')
                    expect(row(mids[0])).to_have_attribute('data-status','configured')
                    active = row(mids[0])
                    reason = active.locator('.ops-outbound-primary [name="reason_for_application"]')
                    reason_form = reason.evaluate_handle('el => el.form').as_element()
                    acknowledged(reason_form, lambda: reason.select_option('Nervous Captain'))
                    expect(reason).to_have_value('Nervous Captain')
                    with case.app.app_context():
                        event = NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=mids[0]).one()
                        self.assertEqual(event.reason_for_application, 'Nervous Captain')
                    active.locator('[data-ops-expand-details]').click()
                    inspect_frost(mids[0])
                    active.locator('[data-ops-expand-details]').click()
                    row(mids[1]).locator('[data-ops-expand-details]').press('Enter')
                    nervous_captain(mids[1])
                    row(mids[1]).locator('[data-ops-expand-details]').click()
                    for index,(mid,plan) in enumerate(zip(mids,('one_type_i','two_type_i','type_i_type_iv'))):
                        print(engine,plan,flush=True)
                        change(mid,'treatment_plan',plan)
                        expect(row(mid).locator('select[name="pass1_surface_area"]')).to_have_value('wings_tail')
                        change(mid,'pass1_surface_area','wings_only')
                        if index:
                            expect(row(mid).locator('select[name="pass2_surface_area"]')).to_have_value('wings_tail')
                            change(mid,'pass2_surface_area','entire_aircraft')
                            change(mid,'treatment_plan','two_type_i' if index==2 else 'type_i_type_iv')
                            expect(row(mid).locator('select[name="pass1_surface_area"]')).to_have_value('wings_only')
                            expect(row(mid).locator('select[name="pass2_surface_area"]')).to_have_value('entire_aircraft')
                            change(mid,'treatment_plan',plan)
                        for width in (1366,1920,2560):
                            page.set_viewport_size({'width':width,'height':900})
                            self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                            primary=row(mid).locator('.ops-outbound-primary')
                            expect(primary).to_be_visible();self.assertLess(primary.bounding_box()['height'],95)
                            cells = ['.ops-outbound-identity', '.ops-outbound-location', '.ops-outbound-status', '.ops-outbound-plan', '.ops-outbound-reason', '[data-subzero-pass="1"]']
                            if index: cells.append('[data-subzero-pass="2"]')
                            boxes = [primary.locator(cell).bounding_box() for cell in cells]
                            self.assertEqual([b['x'] for b in boxes], sorted(b['x'] for b in boxes))
                            self.assertEqual(len(primary.evaluate("el => getComputedStyle(el).gridTemplateRows.split(' ')")),1)
                            self.assertTrue(all(a['x'] + a['width'] <= b['x'] + 1 for a, b in zip(boxes, boxes[1:])))
                            field=primary.locator('[name="pass1_start"]');expect(field).to_be_editable()
                        change(mid,'pass1_start','22:10');change(mid,'pass1_end','22:15')
                        if index:
                            change(mid,'pass2_start','22:16');change(mid,'pass2_end','22:20')
                        expect(row(mid)).to_have_attribute('data-status','fin')
                        expect(row(mid).locator('.ops-outbound-location')).to_contain_text('PILOT START '+('22:16' if index else '22:10'))
                        if index==2:
                            desktop_evidence(mid)
                        quick(mid,'clear');expect(row(mid)).to_have_attribute('data-status','radio-pending')
                        expect(row(mid)).to_have_css('background-color','rgb(240, 45, 159)')
                        self.assertEqual(row(mid).locator('[data-ops-quick-actions] button[value="radio_complete"]').count(),0)
                        # RADIO opens only this mission's inline readback.
                        row(mid).locator('[data-ops-radio]:visible').click()
                        expect(row(mid).locator('[data-radio-script]')).to_be_visible()
                        row(mid).locator('[data-ops-expand-details]').click()
                        expect(row(mid)).to_have_attribute('data-status','radio-pending')
                        row(mid).locator('[data-ops-radio]:visible').click()
                        old=row(mid).locator('[data-ops-inline]').element_handle()
                        acknowledged(old,lambda:row(mid).get_by_role('button',name='RADIO COMPLETE',exact=True).click())
                        expect(row(mid)).to_have_attribute('data-status','gone')
                        expect(row(mid)).to_have_css('background-color','rgb(104, 116, 128)')
                        self.assertFalse(row(mid).locator('.ops-outbound-extra').evaluate('el=>el.open'))

                    # Same mission, same controls, column view and phone; no extra windows.
                    page.set_viewport_size({'width':1366,'height':900});case.layout(page,'visual')
                    target=row(mids[-1]);chevron=target.locator('[data-ops-ramp-chevron]')
                    expect(chevron).to_have_attribute('aria-expanded','false')
                    box, card = chevron.bounding_box(), target.bounding_box()
                    self.assertLess(box['y']-card['y'],10)
                    self.assertLess(card['x']+card['width']-box['x']-box['width'],10)
                    chevron.press('Enter')
                    expect(chevron).to_have_attribute('aria-expanded','true')
                    expect(target.locator('[data-ops-inline]')).to_be_visible()
                    expect(target.locator('.ops-inline-loading')).to_have_count(0)
                    chevron.click();expect(target.locator('[data-ops-inline]')).to_be_hidden()
                    chevron.click();expect(target.locator('[data-ops-inline]')).to_be_visible()
                    expect(target.locator('[name="reason_for_application"]:visible')).to_have_count(1)
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
