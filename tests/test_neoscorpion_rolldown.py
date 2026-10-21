"""Canonical history, epoch-safe ledger and future TOP OFF capture only."""
from datetime import datetime, timedelta
from unittest import TestCase
from tests.neoscorpion_handoff_fixture import submit_and_verify
from app.extensions import db
from app.models import (NeoScorpionTopOffEvent, NeoScorpionFuelingEvent, NeoScorpionSortTruck,
    NeoScorpionFuelTruck, NeoScorpionVendorRequest, NeoScorpionTruckDriverAudit, SortDateOperation,
    NeoScorpionFuelAuditEntry)
from app.services import neoscorpion as service, neoscorpion_assets as assets, neoscorpion_inventory as inventory
from app.services.neoscorpion_rolldown import report_context
from tests import test_neoscorpion_vendor_workflow as vendors_fixture
from tests import test_neoscorpion_operational_timeline as timeline_fixture


class RolldownTest(TestCase):
    setUp=timeline_fixture.OperationalTimelineTest.setUp
    prepare=timeline_fixture.OperationalTimelineTest.prepare
    save=timeline_fixture.OperationalTimelineTest.save
    off=timeline_fixture.OperationalTimelineTest.off
    final_data=timeline_fixture.OperationalTimelineTest.final_data
    login=timeline_fixture.OperationalTimelineTest.login
    vendor=vendors_fixture.VendorWorkflowTest.vendor
    connect=vendors_fixture.VendorWorkflowTest.connect
    next_visit=vendors_fixture.VendorWorkflowTest.next_visit
    _exercise_returning_tail=timeline_fixture.OperationalTimelineTest._exercise_returning_tail

    def report(self, **kwargs):
        return report_context(self.gateway,self.operation,**kwargs)

    def truck_audit(self, old, new, at, reason='Dispatcher recorded the truck assignment.', work=None):
        row=NeoScorpionFuelAuditEntry(sort_date_operation_id=self.operation.id,
            fuel_assignment_id=self.assignment.id,fuel_work_state_id=work.id if work else None,
            action='swap_truck',field_name='assigned_truck_id',old_value=str(old) if old else None,
            new_value=str(new) if new else None,created_at=at,changed_by_user_id=self.dispatcher.id,reason=reason)
        db.session.add(row);db.session.commit()
        return row

    def test_initial_and_unproven_changes_are_audit_only_not_operational_swaps(self):
        self.prepare()
        initial=self.truck_audit(None,self.truck.id,self.start-timedelta(minutes=5))
        unknown=self.truck_audit(999,self.truck.id,self.start-timedelta(minutes=4),reason='Legacy planning edit')
        report=self.report()
        for row in (initial,unknown):
            self.assertNotIn(f'audit:{row.id}',[e['source'] for e in report['events']])
            self.assertIn(f'audit:{row.id}',[e['source'] for e in report['assignment_audits']])
        self.assertNotIn('SWAP TRUCK',report['copy_text'])
        self.assertFalse(any(r['Event']=='SWAP TRUCK' for r in report['export_rows']))
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(action='swap_truck').count(),2)

    def test_sent_swap_belongs_to_both_trucks_not_a_later_physical_truck(self):
        self.prepare()
        other= self.fixture._nightly_truck('B');third=self.fixture._nightly_truck('C');db.session.commit()
        at=self.start-timedelta(minutes=5)
        self.truck_audit(None,other.id,at)
        swap=self.truck_audit(other.id,third.id,at+timedelta(minutes=1),work=self.work)
        report=self.report()
        swaps=[e for e in report['events'] if e['source']==f'audit:{swap.id}']
        self.assertEqual({e['truck_id'] for e in swaps},{other.id,third.id})
        for truck in (other,third):
            one=self.report(truck_id=truck.id)
            self.assertEqual(sum(e['source']==f'audit:{swap.id}' for e in one['events']),1)
            self.assertIn('SWAP TRUCK',one['copy_text'])
            self.assertEqual(sum(r['Event']=='SWAP TRUCK' for r in one['export_rows']),1)

    def test_physical_swap_survives_missing_assignment_receipts_but_not_future_on(self):
        self.prepare()
        other=self.fixture._nightly_truck('B');db.session.commit()
        unknown=self.truck_audit(self.truck.id,other.id,self.start-timedelta(minutes=1),reason='Legacy change')
        actual=self.truck_audit(self.truck.id,other.id,self.start+timedelta(minutes=1),reason='Physical truck swap')
        sources=[e['source'] for e in self.report()['events']]
        self.assertNotIn(f'audit:{unknown.id}',sources)
        self.assertEqual(sources.count(f'audit:{actual.id}'),2)

    def test_unsent_selection_and_repeated_a_b_a_changes_do_not_borrow_old_receipt(self):
        self.prepare()
        other=self.fixture._nightly_truck('B');db.session.commit()
        at=self.start-timedelta(minutes=10)
        initial=self.truck_audit(None,self.truck.id,at)
        sent=self.truck_audit(self.truck.id,other.id,at+timedelta(minutes=1))
        planning=self.truck_audit(other.id,self.truck.id,at+timedelta(minutes=2),reason='Unsent planning selection')
        unknown=self.truck_audit(self.truck.id,other.id,at+timedelta(minutes=3),reason='Unsent planning selection')
        sources=[e['source'] for e in self.report()['events']]
        self.assertIn(f'audit:{sent.id}',sources)
        self.assertNotIn(f'audit:{unknown.id}',sources)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(action='swap_truck').count(),4)

    def test_repeated_sent_a_b_a_swaps_and_initial_assignment_are_preserved_once(self):
        self.prepare()
        other=self.fixture._nightly_truck('B');db.session.commit()
        at=self.assignment.truck_assigned_at_utc
        self.truck_audit(None,self.truck.id,at)
        first=self.truck_audit(self.truck.id,other.id,at+timedelta(seconds=1))
        second=self.truck_audit(other.id,self.truck.id,at+timedelta(seconds=2))
        report=self.report()
        for change in (first,second):
            self.assertEqual(sum(e['source']==f'audit:{change.id}' for e in report['events']),2)
        self.assertEqual(sum(e['columns']['Event']=='MISSION ASSIGNED / PREASSIGNED'
            and e['truck_id']==self.truck.id and e['at']==at for e in report['events']),1)
        self.login(self.dispatcher)
        html=self.client.get(f'/neoscorpion/reports/truck-rolldown?operation_id={self.operation.id}').get_data(as_text=True)
        self.assertIn('data-assignment-audit-evidence',html)

    def test_other_cycle_or_assignment_does_not_prove_old_truck_was_sent(self):
        self.prepare()
        other=self.fixture._nightly_truck('B');db.session.commit()
        at=self.start-timedelta(minutes=10)
        self.truck_audit(None,self.truck.id,at,work=self.work)
        from app.models import NeoScorpionFuelWorkState
        child=NeoScorpionFuelWorkState(fuel_assignment_id=self.assignment.id,tail_number=self.work.tail_number,
            visit_number=2,physical_cycle_number=2,physical_truck_id=other.id)
        db.session.add(child);db.session.commit()
        change=self.truck_audit(self.truck.id,other.id,at+timedelta(minutes=1),work=child)
        self.assertNotIn(f'audit:{change.id}',[e['source'] for e in self.report()['events']])

    def test_actual_on_remains_proof_despite_intervening_unverified_planning_audit(self):
        self.prepare()
        other=self.fixture._nightly_truck('B');db.session.commit()
        self.truck_audit(None,other.id,self.start+timedelta(seconds=1),reason='Unsent planning edit')
        changed=self.truck_audit(self.truck.id,other.id,self.start+timedelta(seconds=2),reason='Physical change')
        self.assertEqual(sum(e['source']==f'audit:{changed.id}' for e in self.report()['events']),2)

    def test_legacy_automatic_on_without_provenance_is_not_dispatch_proof(self):
        self.prepare()
        other=self.fixture._nightly_truck('B')
        self.work.manual_on_at_utc=None;self.work.confirmed_on_at_utc=None
        self.work.on_at_utc=self.start;self.work.performance_context={};db.session.commit()
        changed=self.truck_audit(self.truck.id,other.id,self.start+timedelta(seconds=2),reason='Legacy planning change')
        self.assertNotIn(f'audit:{changed.id}',[e['source'] for e in self.report()['events']])

    def test_posted_transfer_and_physical_release_are_distinct_from_complete(self):
        self.prepare()
        self.save(self.start+timedelta(minutes=10),**self.final_data());self.off()
        before=self.report()
        self.assertIn('TRUCK OFF / RELEASED',before['copy_text'])
        self.assertNotIn('DISPATCHER COMPLETE',before['copy_text'])
        submit_and_verify(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        event=NeoScorpionFuelingEvent.query.one()
        report=self.report()
        transfer=next(e['columns'] for e in report['events'] if e['source']==f'fuel:{event.id}')
        self.assertEqual(transfer['Change gal'],'-3,000')
        self.assertEqual(transfer['Balance gal'],'Unavailable')
        self.assertEqual(transfer['Tail'],event.tail_number)
        self.assertEqual(report['summaries'][0]['uplift'],3000)
        self.assertEqual(report['summaries'][0]['ending'],7000)
        self.assertEqual(report['summaries'][0]['physical_minutes'],12)
        self.assertIn('DISPATCHER COMPLETE',report['copy_text'])
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertEqual(self.selection.current_gallons,7000)

    def test_baseline_before_unposted_tf_and_fleet_totals(self):
        self.prepare();self.selection.current_gallons=6000;db.session.commit()
        inventory.correct_baseline(self.gateway,self.operation,self.dispatcher,self.truck.id,5500,0);db.session.commit()
        self.save(self.start+timedelta(minutes=10),**{**self.final_data(),'transfer_fuel_gallons':'1200'});self.off()
        submit_and_verify(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        other=NeoScorpionFuelTruck(gateway_id=self.gateway.id,truck_number='SECOND',capacity_gallons=10000)
        db.session.add(other);db.session.flush()
        db.session.add(NeoScorpionSortTruck(sort_date_operation_id=self.operation.id,fuel_truck_id=other.id,status='available',starting_gallons=2000,current_gallons=2000));db.session.commit()
        report=self.report()
        correction=next(e['columns'] for e in report['events'] if e['columns']['Event']=='INVENTORY VERIFIED / CORRECTED')
        self.assertEqual((correction['Before gal'],correction['Change gal'],correction['Balance gal']),('6,000','-500','5,500'))
        self.assertEqual(report['fleet']['ending'],6300)
        self.assertEqual(report['fleet']['uplift'],1200)
        self.assertEqual(report['fleet']['adjustments'],-500)
        self.assertEqual(len(self.report(truck_id=other.id)['summaries']),1)

    def test_covered_transfer_is_not_deducted_twice_and_pending_not_movement(self):
        self.prepare();self.off();user,_account=self.connect()
        request=inventory.submit_verification(self.gateway,self.operation,user,self.truck.id,'9000');db.session.commit()
        self.assertEqual(request.status,'approved')
        self.save(self.start+timedelta(minutes=16),**self.final_data())
        submit_and_verify(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        event=NeoScorpionFuelingEvent.query.one()
        db.session.add(NeoScorpionVendorRequest(sort_date_operation_id=self.operation.id,fuel_truck_id=self.truck.id,
            user_id=user.id,kind='fuel',status='pending',gallons=1234));db.session.commit()
        report=self.report()
        row=next(e['columns'] for e in report['events'] if e['source']==f'fuel:{event.id}')
        self.assertEqual(row['Change gal'],'+0')
        self.assertIn('no second deduction',row['Notes'])
        self.assertEqual(report['fleet']['ending'],9000)
        self.assertNotIn('1,234',report['copy_text'])
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_top_off_multiple_trips_cross_midnight_and_immutable(self):
        self.prepare();self.off()
        self.assignment.assigned_truck_id=None;db.session.commit()
        at=datetime(2026,10,15,4,55)
        for index in range(2):
            sent=at+timedelta(hours=index)
            assets.mark_nightly_truck_topping_off(self.operation,self.truck,changed_by_user=self.dispatcher,now_utc=sent)
            assets.mark_nightly_truck_topping_off(self.operation,self.truck,changed_by_user=self.dispatcher,now_utc=sent)
            db.session.commit()
            assets.complete_nightly_truck_top_off(self.operation,self.truck,12000+index*1000,
                changed_by_user=self.dispatcher,now_utc=sent+timedelta(minutes=15));db.session.commit()
        rows=NeoScorpionTopOffEvent.query.order_by(NeoScorpionTopOffEvent.id).all()
        self.assertEqual(len(rows),4)
        self.assertEqual(rows[1].sent_event_id,rows[0].id)
        self.assertEqual(rows[3].sent_event_id,rows[2].id)
        report=self.report()
        self.assertEqual(report['fleet']['top_off_visits'],2)
        # First departure was unverified due to OFF with unposted fuel.
        self.assertTrue(report['fleet']['top_off_unknown'])
        self.assertEqual(report['fleet']['top_off_added'],1000)
        returns=[e['columns'] for e in report['events'] if e['columns']['Event']=='TOP OFF RETURNED']
        self.assertEqual([r['Duration min'] for r in returns],['15.00','15.00'])
        self.assertEqual(self.selection.starting_gallons,10000)
        self.assertEqual([e['at'] for e in report['events']],sorted(e['at'] for e in report['events']))
        rows[0].gallons=1
        with self.assertRaisesRegex(ValueError,'append-only'):db.session.flush()
        db.session.rollback()

    def test_legacy_top_off_return_never_fabricates_sent(self):
        self.prepare();self.off();self.assignment.assigned_truck_id=None
        self.selection.status='topping_off';self.selection.top_off_sent_at_utc=datetime(2026,10,14,20)
        db.session.commit()
        assets.complete_nightly_truck_top_off(self.operation,self.truck,15000,changed_by_user=self.dispatcher);db.session.commit()
        row=NeoScorpionTopOffEvent.query.one()
        self.assertIsNone(row.sent_event_id)
        self.assertEqual(row.kind,'returned')
        self.assertIn('Sent record unavailable',self.report()['copy_text'])

    def test_historical_sort_and_later_driver_never_rewrite_event_identity(self):
        self.prepare();self.save(self.start+timedelta(minutes=10),**self.final_data());self.off()
        submit_and_verify(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        db.session.add(NeoScorpionTruckDriverAudit(sort_date_operation_id=self.operation.id,fuel_truck_id=self.truck.id,
            driver_user_id=self.dispatcher.id,changed_by_user_id=self.dispatcher.id,reason='Later driver',created_at=datetime.utcnow()+timedelta(minutes=1)))
        other=SortDateOperation(gateway_id=self.gateway.id,gateway_code=self.gateway.code,generated_by_user_id=self.dispatcher.id,
            sort_date=self.operation.sort_date+timedelta(days=1),sort_name='night',window_minutes=60)
        db.session.add(other);db.session.commit()
        report=self.report()
        physical=next(e['columns'] for e in report['events'] if e['columns']['Event']=='TRUCK ON')
        self.assertIn('historical identity unavailable',physical['Driver'])
        self.assertEqual(report_context(self.gateway,other)['events'],[])
        self.assertEqual(NeoScorpionFuelingEvent.query.one().tail_number,self.work.tail_number)

    def test_report_route_csv_and_vendor_denial(self):
        self.prepare();self.login(self.dispatcher)
        url=f'/neoscorpion/reports/truck-rolldown?operation_id={self.operation.id}'
        response=self.client.get(url)
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        self.assertIn('COPY REPORT',response.get_data(as_text=True))
        csv=self.client.get(url+'&format=csv')
        self.assertEqual(csv.status_code,200)
        self.assertIn('TRUCK ON',csv.get_data(as_text=True))
        self.assertEqual(self.client.get(url+'&truck_id=9999999').status_code,400)
        user,_account,_token=self.vendor();self.client.get('/logout')
        self.client.post('/login',data={'email':user.email,'password':'VendorPassword123!'})
        self.assertEqual(self.client.get(url).status_code,403)

    def test_unverified_and_unknown_history_not_fabricated(self):
        self.prepare();self.selection.inventory_verified=False;db.session.commit()
        report=self.report()
        self.assertFalse(report['fleet']['verified'])
        self.assertIsNone(report['fleet']['oos_minutes'])
        self.assertIn('UNVERIFIED',report['copy_text'])
        self.assertTrue(all(e['columns']['Parking']=='Unavailable' for e in report['events']))

    def test_repeated_a_b_a_visits_keep_independent_event_tails_and_gallons(self):
        self._exercise_returning_tail()
        report=self.report()
        posted=[e['columns'] for e in report['events'] if e['columns']['Event']=='FUEL POSTED']
        self.assertEqual(len(posted),3)
        self.assertEqual(len({p['Work ID'] for p in posted}),3)
        self.assertEqual(sorted((p['Tail'],p['T/F gal']) for p in posted),
                         [('N491UP','2,000'),('N491UP','3,000'),('N492UP','1,000')])
        self.assertEqual(report['fleet']['uplift'],6000)
        self.assertEqual(report['fleet']['ending'],4000)

    def test_truck_swap_history_does_not_attribute_old_visit_to_current_truck(self):
        self._exercise_returning_tail(different_truck=True)
        report=self.report()
        self.assertEqual(len(report['summaries']),2)
        posted=[e['columns'] for e in report['events'] if e['columns']['Event']=='FUEL POSTED']
        self.assertEqual(sorted(p['T/F gal'] for p in posted if p['Truck']==self.truck.truck_number),['1,000','3,000'])
        self.assertEqual(report['fleet']['uplift'],6000)

    def test_verified_top_off_difference_known_and_rollback_does_not_capture_trip(self):
        self.prepare();self.save(self.start+timedelta(minutes=10),**self.final_data());self.off()
        submit_and_verify(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assignment.assigned_truck_id=None;db.session.commit()
        assets.mark_nightly_truck_topping_off(self.operation,self.truck,changed_by_user=self.dispatcher)
        db.session.rollback()
        self.assertEqual(NeoScorpionTopOffEvent.query.count(),0)
        assets.mark_nightly_truck_topping_off(self.operation,self.truck,changed_by_user=self.dispatcher);db.session.commit()
        assets.complete_nightly_truck_top_off(self.operation,self.truck,10000,changed_by_user=self.dispatcher);db.session.commit()
        report=self.report()
        self.assertEqual(report['fleet']['top_off_added'],3000)
        self.assertFalse(report['fleet']['top_off_unknown'])
        self.assertEqual(report['fleet']['adjustments'],0)

    def test_top_off_snapshot_does_not_treat_pretransfer_baseline_as_physical_balance(self):
        self.prepare();self.off()
        inventory.correct_baseline(self.gateway,self.operation,self.dispatcher,self.truck.id,9500,0);db.session.commit()
        self.assertTrue(self.selection.inventory_verified)
        self.assignment.assigned_truck_id=None;db.session.commit()
        assets.mark_nightly_truck_topping_off(self.operation,self.truck,changed_by_user=self.dispatcher);db.session.commit()
        self.assertFalse(NeoScorpionTopOffEvent.query.one().inventory_verified)
        self.assertEqual(self.selection.current_gallons,9500)


if __name__=='__main__':
    import unittest
    unittest.main()
