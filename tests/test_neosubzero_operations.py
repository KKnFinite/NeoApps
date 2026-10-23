"""Release 1 presentation/preferences and canonical-handler integration."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from app.extensions import db
from app.models import NeoSubZeroUserPreference, SortDateOperation, SortDateMission, SortDateParkingAssignment, NeoSubZeroDepartureDeiceEvent, NeoSubZeroPretreatState, SortDateTailState
from app.services.live_collaboration import entity_version
from app.services.neosubzero_operations import operations_context, PRIMARY_RAMPS
from app.services.neosubzero_preferences import neosubzero_operations_preferences, set_neosubzero_operations_preferences
from tests import test_neosubzero_ucc as fixtures


class OperationsTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.NeoSubZeroUccTest(); self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.f.bravo = self.f._departure('5X300', 'N300', 'DEN', 3)
        db.session.add(SortDateParkingAssignment(sort_date_operation_id=self.f.operation.id,tail_number='N300',ramp_code='B',position_code='B01'));db.session.commit()
        self.user = self.f._user('operations-admin', 'grandmaster')
        self.client = self.f.app.test_client(); self.f._login(self.client, self.user)
        self.current = patch('app.neonodes.neosubzero.routes.current_neosubzero_operation', return_value=self.f.operation)
        self.current.start(); self.addCleanup(self.current.stop)

    def post(self, target='departure-deice/mutate', **values):
        defaults = dict(target='/neosubzero/'+target, operation_id=self.f.operation.id, mode='outbound',
            mission_id=self.f.alpha.id, physical_tail=self.f.alpha.assigned_tail_number, board='coordinator')
        return self.client.post('/neosubzero/operations/mutate', data={**defaults, **values})

    def test_legacy_routes_and_unified_share_workspace_and_controls(self):
        for path, mode in (('operations','outbound'),('outbound','outbound'),('pretreat','pretreat')):
            page = self.client.get('/neosubzero/'+path)
            self.assertEqual(page.status_code, 200)
            self.assertIn(b'data-ops-workspace', page.data)
            self.assertIn(('data-mode="'+mode+'"').encode(), page.data)
            self.assertEqual(page.data.count(b'data-ops-primary-pass="1"'), 3)
            self.assertEqual(page.data.count(b'data-ops-primary-pass="2"'), 3)
        self.assertEqual(self.client.get('/neosubzero/coordinator').status_code,200)

    def test_no_current_sort_is_safe_in_both_modes_and_revision(self):
        with patch('app.neonodes.neosubzero.routes.current_neosubzero_operation',return_value=None):
            for mode in ('pretreat','outbound'):
                response=self.client.get('/neosubzero/operations?mode='+mode)
                self.assertEqual(response.status_code,200)
                self.assertIn(b'No current sort.',response.data)
                self.assertNotIn(b'data-ops-row ',response.data)
            self.assertEqual(self.client.get('/neosubzero/operations/revision').status_code,200)
            self.assertEqual(self.post(action='initial_contact').status_code,409)

    def test_preferences_are_per_user_patch_only_and_sort_reset_without_poll_writes(self):
        second = self.f._user('operations-other', 'grandmaster')
        self.assertEqual(neosubzero_operations_preferences(self.user,self.f.operation),dict(mode='outbound',layout='list',ramp='All'))
        set_neosubzero_operations_preferences(self.user,self.f.operation,dict(mode='pretreat',layout='visual',ramp='Bravo')); db.session.commit()
        pref = NeoSubZeroUserPreference.query.filter_by(user_id=self.user.id).one()
        stamp = pref.updated_at
        self.assertEqual(neosubzero_operations_preferences(second,self.f.operation)['ramp'],'All')
        next_sort = SortDateOperation(sort_date=self.f.operation.sort_date+timedelta(days=1), gateway_id=self.f.gateway.id,gateway_code='RFD',sort_name='night');db.session.add(next_sort);db.session.commit()
        self.assertEqual(neosubzero_operations_preferences(self.user,next_sort),dict(mode='pretreat',layout='visual',ramp='All'))
        self.assertEqual(pref.updated_at,stamp)
        set_neosubzero_operations_preferences(self.user,self.f.operation,{'layout':'list'});db.session.commit()
        self.assertEqual(neosubzero_operations_preferences(self.user,self.f.operation),dict(mode='pretreat',layout='list',ramp='Bravo'))
        self.assertTrue(pref.weather_animations_enabled)

    def test_preferences_endpoint_rejects_stale_sorts_and_invalid_choices(self):
        path='/neosubzero/operations/preferences'
        self.assertEqual(self.client.post(path,json=dict(operation_id=-1,ramp='Alpha')).status_code,409)
        self.assertEqual(self.client.post(path,json=dict(operation_id=self.f.operation.id,layout='fake')).status_code,400)
        result=self.client.post(path,json=dict(operation_id=self.f.operation.id,mode='pretreat',ramp='Echo'))
        self.assertEqual(result.status_code,200);self.assertEqual(result.json['preferences']['ramp'],'Echo')
        self.assertIn(b'data-mode="pretreat"',self.client.get('/neosubzero/operations').data)

    def test_two_pass_types_and_surfaces_no_invented_second_pass(self):
        event=self.f._deice_event(self.f.alpha,'deice_planned')
        event.treatment_plan='type_i_type_iv';event.pass1_surface_area='wings_tail';event.pass2_surface_area='entire_aircraft';db.session.commit()
        row=next(r for r in operations_context(self.f.gateway,self.f.operation,'outbound')['rows'] if r['mission_id']==self.f.alpha.id)
        self.assertEqual(row['primary_passes'],[dict(number=1,type='Type I',surface='Wings + Tail'),dict(number=2,type='Type IV',surface='Entire Aircraft')])
        event.treatment_plan='one_type_i';db.session.commit()
        self.assertIsNone(next(r for r in operations_context(self.f.gateway,self.f.operation,'outbound')['rows'] if r['mission_id']==self.f.alpha.id)['primary_passes'][1]['type'])

    def test_etd_full_timestamp_and_cleared_last(self):
        self.f.alpha.eta_datetime_utc=datetime(2026,9,2,0,5)
        self.f.remote.eta_datetime_utc=datetime(2026,9,1,23,55)
        event=self.f._deice_event(self.f.bravo,'cleared');db.session.commit()
        rows=operations_context(self.f.gateway,self.f.operation,'outbound')['rows']
        self.assertLess([r['mission_id'] for r in rows].index(self.f.remote.id),[r['mission_id'] for r in rows].index(self.f.alpha.id))
        self.assertEqual(rows[-1]['mission_id'],self.f.bravo.id)
        self.assertEqual(rows[-1]['visual_label'],'CLEARED')
        self.assertEqual(event.status,'cleared')

    def test_pretreat_eta_order_and_primary_fixed_pass_contract(self):
        for index, mission in enumerate((self.f.alpha,self.f.bravo)):
            db.session.add(SortDateMission(sort_date_operation_id=self.f.operation.id,sort_date=self.f.operation.sort_date,gateway_code='RFD',sort_name='night',origin='SDF',destination='RFD',mission_type='arrival',flight_number='ARR'+str(index),assigned_tail_number=mission.assigned_tail_number,
                eta_datetime_utc=datetime(2026,9,1,22,index),planned_datetime_utc=datetime(2026,9,1,20,index)))
        state=NeoSubZeroPretreatState(sort_date_operation_id=self.f.operation.id,tail_number=self.f.alpha.assigned_tail_number,pretreat_planned=True,pass1_surface_area='wings_only',pass2_surface_area='wings_tail');db.session.add(state);db.session.commit()
        rows=operations_context(self.f.gateway,self.f.operation,'pretreat')['rows']
        self.assertEqual([r['mission_id'] for r in rows[:2]],[self.f.alpha.id,self.f.bravo.id])
        self.assertEqual([p['type'] for p in rows[0]['primary_passes']],['Type I','Type IV'])

    def test_status_colors_derive_from_actual_milestones(self):
        event=self.f._deice_event(self.f.alpha,'deice_planned');db.session.commit()
        def state():return next(r for r in operations_context(self.f.gateway,self.f.operation,'outbound')['rows'] if r['mission_id']==self.f.alpha.id)['visual_state']
        self.assertEqual(state(),'planned')
        event.configured_at_utc=datetime(2026,9,1,1);event.status='configured';db.session.commit();self.assertEqual(state(),'configured')
        event.pass1_started_at_utc=datetime(2026,9,1,1,1);db.session.commit();self.assertEqual(state(),'deicing')
        for persisted, expected in (('finished','fin'),('negative','negative'),('not_sprayed','not-sprayed'),('cleared','cleared')):
            event.status=persisted;db.session.commit();self.assertEqual(state(),expected)

    def test_actual_pretreat_indicator_only(self):
        state=NeoSubZeroPretreatState(sort_date_operation_id=self.f.operation.id,tail_number=self.f.alpha.assigned_tail_number,pretreat_planned=True);db.session.add(state);db.session.commit()
        self.assertNotIn(b'class="ops-pretreated"',self.client.get('/neosubzero/operations').data)
        tail=SortDateTailState(sort_date=self.f.operation.sort_date,gateway_code='RFD',sort_name='night',tail_number=self.f.alpha.assigned_tail_number,pretreat_status=True);db.session.add(tail);db.session.commit()
        self.assertIn(b'class="ops-pretreated"',self.client.get('/neosubzero/operations').data)
        row=next(r for r in operations_context(self.f.gateway,self.f.operation,'outbound')['rows'] if r['mission_id']==self.f.alpha.id)
        self.assertEqual(row['visual_label'],'UNTREATED')
        tail.deice_status='configured';db.session.commit()
        row=next(r for r in operations_context(self.f.gateway,self.f.operation,'outbound')['rows'] if r['mission_id']==self.f.alpha.id)
        self.assertEqual(row['visual_label'],'CONFIGURED')

    def test_existing_handler_fin_clear_and_duplicate_submission(self):
        result=self.post(action='initial_contact');self.assertEqual(result.status_code,200,result.json)
        event=NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=self.f.alpha.id).one()
        version=entity_version(event)
        result=self.post(action='save_treatment',expected_version=version,treatment_plan='one_type_i',pass1_surface_area='wings_tail',pass1_start='2200',pass1_end='2205')
        self.assertEqual(result.status_code,200,result.json);self.assertEqual(event.status,'finished')
        self.assertEqual(self.post(action='save_treatment',expected_version=version,treatment_plan='one_type_i').status_code,409)
        result=self.post(action='clear',expected_version=entity_version(event));self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(event.status,'cleared');self.assertEqual(NeoSubZeroDepartureDeiceEvent.query.count(),1)

    def test_tail_swap_keeps_old_tail_evidence_and_rejects_stale_writes(self):
        self.post(action='initial_contact')
        event=NeoSubZeroDepartureDeiceEvent.query.one();old_tail=event.tail_number
        self.f.alpha.assigned_tail_number='REPLACEMENT';db.session.commit()
        row=next(r for r in operations_context(self.f.gateway,self.f.operation,'outbound')['rows'] if r['mission_id']==self.f.alpha.id)
        self.assertTrue(row['identity_conflict']);self.assertIsNone(row['primary_passes'][0]['type'])
        self.assertEqual(self.post(action='set_negative',expected_version=entity_version(event),physical_tail='REPLACEMENT').status_code,409)
        self.assertEqual(event.tail_number,old_tail);self.assertEqual(event.status,'deice_planned')
        self.assertEqual(self.post(action='set_negative',expected_version=entity_version(event),mode='pretreat').status_code,403)

    def test_stale_sort_and_readonly_permissions(self):
        self.assertEqual(self.post(action='initial_contact',operation_id=-1).status_code,409)
        watcher=self.f._user('operations-watch','watcher');self.f._login(self.client,watcher)
        self.assertEqual(self.client.get('/neosubzero/operations').status_code,200)
        self.assertEqual(self.post(action='initial_contact').status_code,403)
        self.assertEqual(NeoSubZeroDepartureDeiceEvent.query.count(),0)

    def test_concurrent_pretreat_creation_rejects_missing_original_version(self):
        state=NeoSubZeroPretreatState(sort_date_operation_id=self.f.operation.id,tail_number=self.f.alpha.assigned_tail_number,pretreat_planned=True,notes='Other operator evidence')
        db.session.add(state);db.session.commit()
        result=self.post(target='pretreat/mutate',mode='pretreat',action='save_treatment',expected_version='',notes='Stale draft')
        self.assertEqual(result.status_code,409)
        self.assertIn('draft is kept',result.json['error'])
        self.assertEqual(state.notes,'Other operator evidence')
