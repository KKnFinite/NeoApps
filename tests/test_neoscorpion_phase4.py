"""Same-request canonical evidence reuse, not cached operational authority."""
from copy import deepcopy
from unittest.mock import patch
import unittest
from sqlalchemy import event
from app.extensions import db
from app.models import NeoScorpionFuelingEvent, NeoScorpionSortTruck
from app.services import neoscorpion as fuel
from app.services import neoscorpion_pending_uplift as pending
from app.services import neoscorpion_assignment_times as receipts
from app.services import neoscorpion_drivers as drivers
from app.services.neoscorpion_dispatch_fragments import _digest
from tests import test_dispatch_refresh_fragments as fixtures


class Phase4Test(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.DispatchFragmentsTest();self.case.setUp()
        self.addCleanup(self.case.doCleanups);self.case.ready()

    def measured(self, path):
        statements=[]
        def capture(c,cur,sql,*args):statements.append(sql)
        event.listen(db.engine,'before_cursor_execute',capture)
        try:response=self.case.client.get(path)
        finally:event.remove(db.engine,'before_cursor_execute',capture)
        self.assertEqual(response.status_code,200)
        self.assertFalse(any(s.lstrip().split()[0].upper() in {'INSERT','UPDATE','DELETE'} for s in statements))
        return response,statements

    def test_shared_reads_preserve_full_dispatch_markup_with_less_sql(self):
        c=self.case; original=pending.attach_console
        optimized,queries=self.measured('/neoscorpion/fuel-dispatch/live-panel')
        # The fallback intentionally obtains its own fresh Work/plans/events,
        # exercising the existing independent-caller contract as a reference.
        with patch.object(pending,'attach_console',side_effect=lambda context,**kw:original(context)):
            independent,baseline=self.measured('/neoscorpion/fuel-dispatch/live-panel')
        self.assertEqual(_digest(optimized.json['html'],'test','panel'),_digest(independent.json['html'],'test','panel'))
        self.assertLess(sum(s.startswith('SELECT') for s in queries),sum(s.startswith('SELECT') for s in baseline))

    def complete(self):
        c=self.case;c.login(c.dispatcher)
        response=c.client.post('/neoscorpion/fuel-dispatch/complete',data=dict(
            assignment_id=c.assignment.id,physical_work_id=c.work.id,
            physical_sequence=c.work.physical_sequence_number),headers={'X-Requested-With':'XMLHttpRequest'})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        return NeoScorpionFuelingEvent.query.one()

    def test_event_projection_preserves_typed_receipt_facts_without_forecast_payload(self):
        c=self.case;physical=self.complete();snapshot=deepcopy(physical.performance_snapshot)
        before=(deepcopy(physical.performance_snapshot),physical.transfer_fuel_gallons,c.selection.current_gallons)
        queries=[]
        def capture(conn,cur,sql,*args):queries.append(sql)
        event.listen(db.engine,'before_cursor_execute',capture)
        try:rows=receipts.assignment_event_rows(c.operation)
        finally:event.remove(db.engine,'before_cursor_execute',capture)
        self.assertEqual(len(rows),1);projected=rows[0]
        self.assertEqual(projected.id,physical.id)
        for key,value in projected.performance_snapshot.items():self.assertEqual(value,snapshot.get(key))
        self.assertNotIn('forecasts',projected.performance_snapshot)
        self.assertTrue(all(' AS receipt_facts' in sql for sql in queries))
        self.assertFalse(any('performance_snapshot AS neoscorpion_fueling_events_performance_snapshot' in sql for sql in queries))
        self.assertEqual((physical.performance_snapshot,physical.transfer_fuel_gallons,c.selection.current_gallons),before)

    def test_projection_never_promotes_malformed_legacy_fact_types(self):
        physical=self.complete();original=deepcopy(physical.performance_snapshot)
        # Controlled legacy-corruption fixtures, not operational writes. In
        # particular a string or boolean must not become a numeric owner ID.
        for value in (True,False,'123',123,None,{'unknown':1},[1]):
            physical.performance_snapshot={**original,'fueler_id':value}
            db.session.flush()
            projected=receipts.assignment_event_rows(self.case.operation)[0].performance_snapshot['fueler_id']
            self.assertEqual(type(projected),type(value))
            self.assertEqual(projected,value)
        physical.performance_snapshot=original;db.session.flush()

    def test_null_legacy_snapshot_does_not_create_attribution_or_crash(self):
        from sqlalchemy import null
        physical=self.complete();original=deepcopy(physical.performance_snapshot)
        for value in (None,{},null()):
            physical.performance_snapshot=value;db.session.flush()
            projected=receipts.assignment_event_rows(self.case.operation)[0]
            self.assertTrue(all(value is None for value in projected.performance_snapshot.values()))
        physical.performance_snapshot=original;db.session.flush()

    def test_completed_event_totals_and_releases_survive_shared_projection(self):
        c=self.case;physical=self.complete();before=(physical.id,c.selection.current_gallons)
        context=fuel.fuel_dispatch_context(c.gateway,include_spear=False)
        self.assertEqual(context['sort_fuel_totals']['transfer']['value'],physical.transfer_fuel_gallons)
        self.assertTrue(context['rows'][0]['administratively_complete'])
        self.assertEqual((physical.id,c.selection.current_gallons),before)

    def test_unclaimed_driver_does_not_build_fleet_evidence(self):
        with patch.object(fuel,'_fuel_rows',side_effect=AssertionError('No selected truck needs no physical rows')):
            response,queries=self.measured('/neoscorpion/truck-driver/live-panel')
        self.assertIn('SELECT YOUR TRUCK',response.json['html'])
        self.assertFalse(any('FROM neoscorpion_fuel_work_states' in sql for sql in queries))
        self.assertEqual(response.json['operation_id'],self.case.operation.id)

    def test_unclaimed_driver_matches_slow_filtered_context_and_claim_recovers_work(self):
        c=self.case; fast=self.measured('/neoscorpion/truck-driver/live-panel')[0]
        original=fuel.fueler_context
        def slow(*args,**kwargs):
            if kwargs.get('truck_id')==-1:kwargs['truck_id']=-2
            return original(*args,**kwargs)
        with patch.object(fuel,'fueler_context',side_effect=slow):
            reference=self.measured('/neoscorpion/truck-driver/live-panel')[0]
        self.assertEqual(fast.json['panel_digest'],reference.json['panel_digest'])
        drivers.change_driver(c.gateway,c.operation,c.dispatcher,c.truck.id,c.dispatcher.id,
            expected_driver_id=None,expected_driver_truck_id=None)
        db.session.commit()
        response=self.measured('/neoscorpion/truck-driver/live-panel')[0]
        self.assertIn(c.mission.assigned_tail_number,response.json['html'])
        self.assertNotEqual(fast.json['panel_digest'],response.json['panel_digest'])

    def test_fresh_requests_and_independent_resource_fallback_preserve_changes(self):
        c=self.case
        first=self.measured('/neoscorpion/fuel-dispatch/live-panel')[0]
        c.mission.assigned_tail_number='N777UP';db.session.commit()
        next_response=self.measured('/neoscorpion/fuel-dispatch/live-panel')[0]
        self.assertIn('N777UP',next_response.json['html'])
        self.assertNotEqual(first.json['html'],next_response.json['html'])
        self.assertEqual(db.session.get(NeoScorpionSortTruck,c.selection.id).current_gallons,10000)


if __name__=='__main__':unittest.main()
