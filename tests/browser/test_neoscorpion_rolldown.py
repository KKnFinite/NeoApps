"""Real authenticated report, COPY/CSV, historical selection and mobile bounds."""
import os
import json
import csv
from io import StringIO
import unittest
from datetime import date,datetime,timedelta
from app.extensions import db
from app.models import (User,SortDateOperation,SortDateMission,NeoScorpionFuelTruck,NeoScorpionSortTruck,
    NeoScorpionFuelAssignment,NeoScorpionFuelWorkState,NeoScorpionFuelingEvent,NeoScorpionTopOffEvent,
    NeoScorpionTruckDriverAudit,NeoScorpionTruckInventoryAudit,NeoScorpionFuelAuditEntry)
from app.services.neoscorpion_rolldown import report_context
from app.services.access_control import ensure_default_gateway_and_nodes
from playwright.sync_api import expect
from tests.browser import test_mobile_drawer as fixtures


class RolldownBrowserTest(unittest.TestCase):
    def test_report_readonly_history_copy_csv_and_responsive_layout(self):
        F=fixtures.MobileDrawerBrowserTest
        F.setUpClass();browser=None
        try:
            with F.app.app_context():
                gateway=ensure_default_gateway_and_nodes();actor=User.query.filter_by(username='drawer-admin').one()
                operation=SortDateOperation(gateway_id=gateway.id,gateway_code=gateway.code,sort_date=date.today()-timedelta(days=1),
                    sort_name='night',window_minutes=60,generated_by_user_id=actor.id)
                truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number='422808',capacity_gallons=10000)
                db.session.add_all([operation,truck]);db.session.flush()
                db.session.add(NeoScorpionSortTruck(sort_date_operation_id=operation.id,fuel_truck_id=truck.id,
                    status='available',starting_gallons=8000,current_gallons=3000))
                mission=SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,gateway_code=gateway.code,
                    sort_name='night',mission_type='departure',mission_source='manual',flight_number='UPS1038',origin=gateway.code,
                    destination='SDF',assigned_tail_number='N491UP',timezone='America/Chicago')
                db.session.add(mission);db.session.flush()
                assignment=NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,sort_date_mission_id=mission.id)
                db.session.add(assignment);db.session.flush()
                at=datetime(2026,10,15,4,50)
                for index,(tail,gallons) in enumerate((('N491UP',3000),('N492UP',500),('N491UP',2000))):
                    start=at+timedelta(minutes=index*20)
                    work=NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id,tail_number=tail,visit_number=index+1,
                        physical_sequence_number=index+1,physical_cycle_number=1,physical_truck_id=truck.id,
                        physical_fueler_user_id=actor.id,manual_on_at_utc=start,off_at_utc=start+timedelta(minutes=10),
                        physical_transfer_gallons=gallons,physical_finalized_at_utc=start+timedelta(minutes=15))
                    db.session.add(work);db.session.flush()
                    db.session.add(NeoScorpionFuelingEvent(sort_date_operation_id=operation.id,fuel_assignment_id=assignment.id,
                        fuel_work_state_id=work.id,tail_number=tail,fuel_truck_id=truck.id,sequence_number=index+1,
                        started_at_utc=start,ended_at_utc=work.off_at_utc,transfer_fuel_gallons=gallons,
                        fueler_user_id=actor.id,created_at=start+timedelta(minutes=15)))
                sent=NeoScorpionTopOffEvent(sort_date_operation_id=operation.id,fuel_truck_id=truck.id,kind='sent',
                    occurred_at_utc=at+timedelta(hours=2),gallons=2500,inventory_verified=True,inventory_epoch=0,actor_user_id=actor.id)
                db.session.add(sent);db.session.flush()
                db.session.add(NeoScorpionTopOffEvent(sort_date_operation_id=operation.id,fuel_truck_id=truck.id,kind='returned',sent_event_id=sent.id,
                    occurred_at_utc=at+timedelta(hours=2,minutes=20),gallons=9000,inventory_verified=True,inventory_epoch=1,actor_user_id=actor.id))
                db.session.commit();operation_id,truck_id=operation.id,truck.id
            browser=getattr(F.pw,os.environ.get('NEO_ROLLDOWN_BROWSER','chromium')).launch()
            context=browser.new_context(viewport={'width':1440,'height':900});page=context.new_page();errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            F().login(page)
            F().ready(page,'/neoscorpion/reports')
            page.get_by_role('link',name='Truck Rolldown',exact=False).click()
            page.wait_for_url('**/reports/truck-rolldown')
            page.locator('[name=operation_id]').select_option(str(operation_id))
            with page.expect_navigation():page.get_by_role('button',name='View',exact=True).click()
            expect(page.locator('.rolldown-event').filter(has_text='UPLIFT')).to_have_count(0)
            expect(page.locator('.rolldown-event').filter(has_text='FUEL POSTED')).to_have_count(3)
            expect(page.locator('[data-truck-summary]')).to_contain_text('5,500')
            self.assertEqual(page.locator('.neoscorpion-rolldown .neoscorpion-panel form[method=post]').count(),0)
            page.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>window.__copied=text}})")
            page.get_by_role('button',name='COPY REPORT').click()
            expect(page.locator('[data-prof-copy-status]')).to_have_text('Copied')
            self.assertEqual(page.evaluate('window.__copied'),page.locator('[data-prof-copy-text]').input_value())
            with page.expect_download() as download:
                page.get_by_role('button',name='CSV',exact=True).click()
            content=open(download.value.path(),encoding='utf-8-sig').read()
            self.assertIn('N491UP',content);self.assertIn('N492UP',content);self.assertIn('TOP OFF RETURNED',content)
            for width,height in ((1440,900),(1920,1080),(390,844),(375,568)):
                page.set_viewport_size({'width':width,'height':height})
                page.evaluate('window.scrollTo(0,0)')
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1)
                self.assertTrue(page.locator('.rolldown-event').first.evaluate('(e)=>e.getBoundingClientRect().width<=innerWidth'))
                if width>700:
                    columns=page.locator('.rolldown-event > summary').evaluate_all("els=>els.map(e=>[...e.children].map(n=>n.getBoundingClientRect().x))")
                    self.assertTrue(all(abs(a-b)<1 for row in columns[1:] for a,b in zip(row,columns[0])))
                print('ROLLDOWN_MEASURE '+json.dumps(dict(browser=os.environ.get('NEO_ROLLDOWN_BROWSER','chromium'),width=width,
                    **page.evaluate("""()=>({report:document.querySelector('.neoscorpion-rolldown .neoscorpion-panel').getBoundingClientRect().height,
                    events:[...document.querySelectorAll('.rolldown-event')].map(e=>e.getBoundingClientRect().height)})"""))),flush=True)
                page.screenshot(path=str(F.evidence/f'rolldown-{width}.png'),full_page=False)
                page.locator('.rolldown-event').first.scroll_into_view_if_needed()
                page.screenshot(path=str(F.evidence/f'rolldown-timeline-{width}.png'),full_page=False)
            page.locator('[name=truck_id]').select_option(str(truck_id))
            with page.expect_navigation():page.get_by_role('button',name='View',exact=True).click()
            expect(page.locator('[data-truck-summary]')).to_have_count(1)
            self.assertNotIn('Fleet totals',page.locator('body').inner_text())
            # Extend the same isolated sort with a second truck, repeat TOP OFF,
            # explicit assignment/completion evidence, driver change and swap.
            # Measurements above deliberately retain the exact baseline fixture.
            with F.app.app_context():
                actor=User.query.filter_by(username='drawer-admin').one()
                gateway=ensure_default_gateway_and_nodes()
                second=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number='SECOND',capacity_gallons=10000)
                db.session.add(second);db.session.flush();second_id=second.id
                db.session.add(NeoScorpionSortTruck(sort_date_operation_id=operation_id,fuel_truck_id=second.id,
                    status='unavailable_oos',starting_gallons=None,current_gallons=None,inventory_verified=False))
                works=NeoScorpionFuelWorkState.query.order_by(NeoScorpionFuelWorkState.id).all()
                assignment=db.session.get(NeoScorpionFuelAssignment,works[-1].fuel_assignment_id)
                assignment.active_work_state_id=works[-1].id;assignment.completed_at_utc=works[-1].physical_finalized_at_utc
                for posted in NeoScorpionFuelingEvent.query.all():
                    posted.performance_snapshot={'truck_assigned_at':(posted.started_at_utc-timedelta(minutes=3)).isoformat(),
                        'parking':'C07','completed_at':(posted.ended_at_utc+timedelta(minutes=5)).isoformat()}
                db.session.add(NeoScorpionTruckDriverAudit(sort_date_operation_id=operation_id,fuel_truck_id=truck_id,
                    driver_user_id=actor.id,changed_by_user_id=actor.id,reason='Recorded later driver',created_at=at+timedelta(minutes=30)))
                db.session.add(NeoScorpionFuelAuditEntry(sort_date_operation_id=operation_id,fuel_assignment_id=assignment.id,
                    fuel_work_state_id=works[0].id,action='swap_truck',field_name='assigned_truck_id',old_value=str(truck_id),
                    new_value=str(second_id),changed_by_user_id=actor.id,reason='Recorded truck swap',created_at=at+timedelta(hours=4)))
                # Initial/unsent changes must remain inspectable but must not
                # leak into either truck's operational timeline or exports.
                initial=NeoScorpionFuelAuditEntry(sort_date_operation_id=operation_id,fuel_assignment_id=assignment.id,
                    action='swap_truck',field_name='assigned_truck_id',old_value=None,new_value=str(second_id),
                    changed_by_user_id=actor.id,reason='Dispatcher recorded the truck assignment.',created_at=at-timedelta(minutes=10))
                unsent=NeoScorpionFuelAuditEntry(sort_date_operation_id=operation_id,fuel_assignment_id=assignment.id,
                    action='swap_truck',field_name='assigned_truck_id',old_value=str(second_id),new_value=str(truck_id),
                    changed_by_user_id=actor.id,reason='Unsent planning selection',created_at=at-timedelta(minutes=9))
                db.session.add_all([initial,unsent]);db.session.flush();filtered_ids=(initial.id,unsent.id)
                db.session.add(NeoScorpionTruckInventoryAudit(sort_date_operation_id=operation_id,fuel_truck_id=truck_id,
                    action='verified',prior_recorded_gallons=8000,confirmed_gallons=7800,actor_user_id=actor.id,
                    reason='Verified correction. '+('Full audit note retained. '*30),created_at=at+timedelta(minutes=1)))
                db.session.add(NeoScorpionTruckInventoryAudit(sort_date_operation_id=operation_id,fuel_truck_id=truck_id,
                    fueling_event_id=NeoScorpionFuelingEvent.query.order_by(NeoScorpionFuelingEvent.id).first().id,
                    action='discrepancy',prior_recorded_gallons=8000,transfer_fuel_gallons=3000,
                    actor_user_id=actor.id,reason='Recorded discrepancy, not a guessed balance',created_at=at+timedelta(minutes=15)))
                sent=NeoScorpionTopOffEvent(sort_date_operation_id=operation_id,fuel_truck_id=truck_id,kind='sent',
                    occurred_at_utc=at+timedelta(hours=3),gallons=7000,inventory_verified=True,inventory_epoch=1,actor_user_id=actor.id)
                db.session.add(sent);db.session.flush()
                db.session.add(NeoScorpionTopOffEvent(sort_date_operation_id=operation_id,fuel_truck_id=truck_id,kind='returned',
                    sent_event_id=sent.id,occurred_at_utc=at+timedelta(hours=3,minutes=25),gallons=9500,
                    inventory_verified=True,inventory_epoch=2,actor_user_id=actor.id))
                db.session.commit()
                expected=report_context(gateway,db.session.get(SortDateOperation,operation_id))
                evidence=[(event['source'],event['columns']) for event in expected['events']]
                individual=report_context(gateway,db.session.get(SortDateOperation,operation_id),truck_id=truck_id)
                expected_copy=individual['copy_text']
                expected_export=individual['export_rows']
                individual_sources=[event['source'] for event in individual['events']]
            page.goto(F.origin+f'/neoscorpion/reports/truck-rolldown?operation_id={operation_id}')
            expect(page.locator('[data-truck-summary]')).to_have_count(2)
            for identifier in filtered_ids:
                self.assertEqual(page.locator(f'.rolldown-event[data-rolldown-source="audit:{identifier}"]').count(),0)
                self.assertGreater(page.locator(f'[data-assignment-audit-source="audit:{identifier}"]').count(),0)
                self.assertNotIn(f'Source ID: audit:{identifier}\n',page.locator('[data-prof-copy-text]').input_value())
            page.locator('[data-assignment-audit-evidence] > summary').click()
            page.locator(f'[data-assignment-audit-source="audit:{filtered_ids[1]}"]').first.locator('summary').click()
            expect(page.locator('[data-assignment-audit-evidence]')).to_contain_text('Unsent planning selection')
            self.assertEqual(page.locator('.rolldown-event').count(),len(evidence))
            self.assertEqual(page.locator('.rolldown-event').evaluate_all("els=>els.map(e=>e.dataset.rolldownSource)"),[source for source,_ in evidence])
            for index,(source,columns) in enumerate(evidence):
                event=page.locator('.rolldown-event').nth(index)
                self.assertFalse(event.evaluate('e=>e.open'))
                fields=event.locator('[data-evidence-field]').evaluate_all("els=>Object.fromEntries(els.map(e=>[e.dataset.evidenceField,e.querySelector('dd').textContent.trim()]))")
                self.assertEqual(fields,{key:str(value) for key,value in columns.items() if value is not None and value not in ('','Unavailable')})
            for event_name in ('MISSION ASSIGNED','DISPATCHER COMPLETE','DRIVER CHANGE','SWAP TRUCK','INVENTORY VERIFIED / CORRECTED'):
                self.assertIn(event_name,page.locator('[data-rolldown-timeline]').inner_text())
            expect(page.locator('.rolldown-event').filter(has_text='TOP OFF RETURNED')).to_have_count(2)
            expect(page.locator('.rolldown-unverified')).to_be_visible()
            for width in (1440,390,375):
                page.set_viewport_size({'width':width,'height':844})
                first=page.locator('.rolldown-event').first
                first.locator('summary').click();self.assertTrue(first.evaluate('e=>e.open'))
                expect(first.locator('[data-evidence-field="UTC"]')).to_be_visible()
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1)
                page.screenshot(path=str(F.evidence/f'rolldown-evidence-{width}.png'))
                first.locator('summary').click()
                correction=page.locator('.rolldown-event').filter(has_text='Full audit note retained.').first
                correction.locator('summary').click()
                expect(correction.locator('[data-evidence-field="Notes"]')).to_contain_text('Full audit note retained. '*30)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1)
                correction.locator('summary').click()
            page.locator('[name=truck_id]').select_option(str(truck_id))
            with page.expect_navigation():page.get_by_role('button',name='View',exact=True).click()
            self.assertEqual(page.locator('.rolldown-event').evaluate_all("els=>els.map(e=>e.dataset.rolldownSource)"),individual_sources)
            self.assertTrue(all(truck=='422808' for truck in page.locator('.rolldown-event').evaluate_all("els=>els.map(e=>e.dataset.rolldownTruck)")))
            self.assertEqual(page.locator('[data-prof-copy-text]').input_value(),expected_copy)
            with page.expect_download() as download:page.get_by_role('button',name='CSV',exact=True).click()
            with open(download.value.path(),encoding='utf-8-sig') as exported:rows=list(csv.DictReader(StringIO(exported.read())))
            self.assertEqual(rows,[{key:("'"+value if isinstance(value,str) and value.startswith(('=','+','-','@')) else
                str(value) if value is not None else '') for key,value in row.items()} for row in expected_export])
            self.assertEqual(errors,[])
            with F.app.app_context():
                self.assertEqual(NeoScorpionFuelingEvent.query.count(),3)
                self.assertEqual(NeoScorpionTopOffEvent.query.count(),4)
                self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=truck_id).one().current_gallons,3000)
        finally:
            if browser:browser.close()
            F.tearDownClass()
