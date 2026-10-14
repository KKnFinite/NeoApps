import unittest
from datetime import datetime
from unittest.mock import patch, Mock
from tests import test_neorain_outbound as fixtures
from app.extensions import db
from app.models import NeoRainMissionOverride, NeoRainOverrideAudit, SortDateGoogleMissionLink
from app.services.live_collaboration import entity_version
from app.services.neorain_overrides import edit_override, observe_import, FIELDS
from app.services.neorain_google_overrides import attempt_once, validate_manual_cell, GoogleOverrideBlocked, google_value, match_row


class RainOverrideTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.NeoRainOutboundTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.app,self.client,self.gateway = self.fixture.app,self.fixture.client,self.fixture.gateway
        self.operation = self.fixture._operation()
        self.arrival = self.fixture._mission(self.operation,'UPS1487','RFD',mission_type='arrival',planned=datetime(2026,8,30,6),tail='N438UP')
        self.departure = self.fixture._mission(self.operation,'UPS948','SDF',planned=datetime(2026,8,31,7),tail='N948UP')
        self.actor = self.fixture._user('rain_override_operator','operator',rain_role='operator')
        db.session.commit()
        self.fixture._login(self.actor)
        self.scope = patch('app.neonodes.neorain.routes.current_neorain_outbound_operation',return_value=self.operation)
        self.scope.start(); self.addCleanup(self.scope.stop)

    def edit(self, mission, field, value=None, **extra):
        return self.client.post('/neorain/override',json={'mission_id':mission.id,'field':field,'value':value,
            'expected_version':entity_version(mission),**extra})

    def test_sta_and_etd_separate_from_eta_std_and_midnight(self):
        self.arrival.eta_datetime_utc = datetime(2026,8,31,9)
        db.session.commit()
        for raw,display in [('0000','00:00'),('0235','02:35'),('2359','23:59')]:
            response = self.edit(self.arrival,'sta',raw)
            self.assertEqual(response.status_code,200,response.get_data(as_text=True))
            self.assertEqual(response.json['value'],display)
            self.assertEqual(self.arrival.eta_datetime_utc,datetime(2026,8,31,9))
        original_std = self.departure.planned_datetime_utc
        response = self.edit(self.departure,'etd','1430')
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        self.assertEqual(response.json['value'],'14:30')
        self.assertEqual(self.departure.planned_datetime_utc,original_std)
        self.assertEqual(response.json['sync_status'],'NEO_ONLY')

    def test_invalid_blank_incomplete_and_stale_do_not_write(self):
        for raw in ('','0','023','2460','2400','2360','ABCD'):
            response = self.edit(self.arrival,'sta',raw)
            self.assertIn(response.status_code,(200,400))
        self.assertEqual(NeoRainMissionOverride.query.count(),0)
        version = entity_version(self.arrival)
        self.assertEqual(self.edit(self.arrival,'sta','0235').status_code,200)
        response = self.client.post('/neorain/override',json={'mission_id':self.arrival.id,'field':'sta','value':'0335','expected_version':version})
        self.assertEqual(response.status_code,409)
        self.assertEqual(NeoRainOverrideAudit.query.filter_by(action='EDIT').count(),1)

    def test_google_import_cannot_overwrite_active_and_reset_accepts_new_google(self):
        self.arrival.actual_block_in_datetime_utc=datetime(2026,8,31,7)
        self.arrival.actual_block_in_source='google_motherbrain'; db.session.commit()
        self.assertEqual(self.edit(self.arrival,'block_in','0235').status_code,200)
        actual=self.arrival.actual_block_in_datetime_utc
        incoming={'actual_block_in_datetime_utc':datetime(2026,8,31,8),'actual_block_in_source':'google_motherbrain'}
        self.assertTrue(observe_import(self.arrival,'block_in',incoming)); db.session.commit()
        self.arrival.actual_block_in_datetime_utc=datetime(2026,8,31,9)
        self.arrival.actual_block_in_source='google_motherbrain'; db.session.commit()
        self.assertEqual(self.arrival.actual_block_in_datetime_utc,actual)
        self.assertEqual(self.edit(self.arrival,'block_in',action='reset').status_code,200)
        self.arrival.actual_block_in_datetime_utc=datetime(2026,8,31,10)
        self.arrival.actual_block_in_source='google_motherbrain'; db.session.commit()
        self.assertEqual(self.arrival.actual_block_in_datetime_utc,datetime(2026,8,31,10))
        self.assertFalse(NeoRainMissionOverride.query.filter_by(field='block_in').one().active)

    def test_explicit_false_is_not_unset_and_reset_never_replays(self):
        self.assertEqual(self.edit(self.departure,'included',False).status_code,200)
        row=NeoRainMissionOverride.query.filter_by(field='included').one()
        self.assertTrue(row.active); self.assertIs(self.departure.late_metrics_included_override,False)
        old_generation=row.generation
        self.assertEqual(self.edit(self.departure,'included',action='reset').status_code,200)
        attempt_once(row.id,old_generation)
        self.assertFalse(row.active)
        self.assertEqual(NeoRainOverrideAudit.query.filter_by(action='SYNC').count(),1)

    def test_permissions_historical_scope_and_missing_version(self):
        watcher=self.fixture._user('rain_override_watcher','watcher',rain_role='watcher');db.session.commit();self.fixture._login(watcher)
        self.assertEqual(self.edit(self.arrival,'sta','0235').status_code,403)
        self.fixture._login(self.actor)
        with patch('app.neonodes.neorain.routes.current_neorain_outbound_operation',return_value=None):
            self.assertEqual(self.edit(self.arrival,'sta','0235').status_code,409)
        self.assertEqual(self.client.post('/neorain/override',json={'mission_id':self.arrival.id,'field':'sta','value':'0235'}).status_code,400)

    def test_formula_spill_ownership_and_checkbox_inverse(self):
        for cell,spill in [({'userEnteredValue':{'formulaValue':'=1'}},False),({},True),({'effectiveValue':{'numberValue':1}},False)]:
            with self.assertRaises(GoogleOverrideBlocked): validate_manual_cell(cell,spill_owned=spill)
        validate_manual_cell({'userEnteredValue':{'boolValue':False}})
        self.assertIs(google_value(self.departure,'included',{'late_metrics_included_override':False}),True)
        with self.assertRaises(GoogleOverrideBlocked):google_value(self.departure,'official_block_out',{'actual_block_out_datetime_utc':None})

    def test_unmatched_flight_no_google_io_or_replay(self):
        with patch('app.services.neorain_google_overrides.certified_write') as writer:
            self.assertEqual(self.edit(self.arrival,'sta','0235').status_code,200)
            writer.assert_not_called()
            row=NeoRainMissionOverride.query.filter_by(field='sta').one()
            db.session.add(SortDateGoogleMissionLink(sort_date_operation_id=self.operation.id,
                sort_date_mission_id=self.arrival.id,mission_type='arrival',source_sheet='Inbound',source_row=4))
            db.session.commit();attempt_once(row.id,row.generation);writer.assert_not_called()

    def test_google_failure_retains_neo_and_never_retries(self):
        db.session.add(SortDateGoogleMissionLink(sort_date_operation_id=self.operation.id,
            sort_date_mission_id=self.arrival.id,mission_type='arrival',source_sheet='Inbound',source_row=4));db.session.commit()
        with patch('app.services.neorain_google_overrides.certified_write',side_effect=TimeoutError) as writer:
            response=self.edit(self.arrival,'sta','0235')
            self.assertEqual(response.status_code,200)
            row=NeoRainMissionOverride.query.filter_by(field='sta').one()
            self.assertTrue(row.active);self.assertEqual(row.sync_status,'FAILED')
            attempt_once(row.id,row.generation);self.assertEqual(writer.call_count,1)

    def test_no_return_prerequisites_confirmed_correction_preserves_milestones(self):
        self.assertEqual(self.edit(self.departure,'no_return',True).status_code,400)
        for field in ('elmac','ramp_load_complete','crew_load_complete','official_block_out'):
            self.assertEqual(self.edit(self.departure,field,'0235').status_code,200)
        times=[self.departure.elmac_completed_at_utc,self.departure.ramp_load_completed_at_utc,self.departure.crew_load_completed_at_utc,self.departure.actual_block_out_datetime_utc]
        self.assertEqual(self.edit(self.departure,'no_return',True).status_code,200)
        self.assertEqual(self.edit(self.departure,'no_return',False).status_code,400)
        self.assertEqual(self.edit(self.departure,'no_return',False,confirmed=True).status_code,200)
        self.assertEqual(self.departure.departure_status,'blocked_out')
        self.assertEqual(times,[self.departure.elmac_completed_at_utc,self.departure.ramp_load_completed_at_utc,self.departure.crew_load_completed_at_utc,self.departure.actual_block_out_datetime_utc])

    def test_reset_reads_current_upstream_not_old_edit_and_does_not_write(self):
        db.session.add(SortDateGoogleMissionLink(sort_date_operation_id=self.operation.id,sort_date_mission_id=self.arrival.id,mission_type='arrival',source_sheet='Inbound',source_row=4));db.session.commit()
        with patch('app.services.neorain_google_overrides.certified_write',return_value='Inbound!AB4') as writer:
            self.assertEqual(self.edit(self.arrival,'sta','0235').status_code,200)
            fresh={'planned_datetime_utc':datetime(2026,8,31,9),'planned_datetime_local':datetime(2026,8,31,4),'planned_source':'google_motherbrain'}
            self.app.config['NEORAIN_FRESH_UPSTREAM_READER']=lambda mission,field:fresh
            response=self.edit(self.arrival,'sta',action='reset')
            self.assertEqual(response.status_code,200);self.assertEqual(response.json['value'],'04:00')
            self.assertEqual(writer.call_count,1)

    def test_real_rain_import_shields_only_active_fields(self):
        from app.services.google_rain_live_milestones import apply_google_rain_departure_milestones
        self.assertEqual(self.edit(self.departure,'ramp_load_complete','0235').status_code,200)
        self.assertEqual(self.edit(self.departure,'included',False).status_code,200)
        protected=self.departure.ramp_load_completed_at_utc
        result=apply_google_rain_departure_milestones(self.operation,rows=[{'flight_number':self.departure.flight_number,'destination':'SDF','tail':self.departure.assigned_tail_number,'ramp_load_complete':'8/31 03:00','crew_load_complete':'8/31 03:10','removed':False}])
        db.session.commit()
        self.assertEqual(result['applied_count'],1)
        self.assertEqual(self.departure.ramp_load_completed_at_utc,protected)
        self.assertEqual(self.departure.crew_load_completed_at_utc,datetime(2026,8,31,8,10))
        self.assertIs(self.departure.late_metrics_included_override,False)

    def test_certified_adapter_rechecks_identity_formula_and_one_write(self):
        from app.services.neorain_google_overrides import certified_write
        row={'flight':'UPS0948','direction':'departure','gateway':'RFD','sort_date':self.operation.sort_date.isoformat(),'sort_name':self.operation.sort_name,'place':'SDF','tail':self.departure.assigned_tail_number,'row':17}
        adapter=Mock(certified_fields={'included'})
        adapter.permission_verified.return_value=True
        adapter.read_identities.return_value=[row]
        adapter.inspect_input.return_value={'manual_contract_verified':True,'spill_owned':False,'cell':{'userEnteredValue':{'boolValue':False}}}
        adapter.read_input.return_value=True
        self.app.config['NEORAIN_CERTIFIED_GOOGLE_ADAPTER']=adapter
        certified_write(self.departure,'included',{'late_metrics_included_override':False})
        adapter.write_input_once.assert_called_once_with('13ub-NCOHzUpgAvxzKHK_noRTficTSFqufkNNOKKfPcM','Outbound','Q17',True)
        adapter.write_input_once.reset_mock()
        adapter.read_identities.side_effect=[[row],[{**row,'row':18}]]
        with self.assertRaises(GoogleOverrideBlocked):certified_write(self.departure,'included',{'late_metrics_included_override':False})
        adapter.write_input_once.assert_not_called()

    def test_google_identity_rejects_missing_duplicate_wrong_sort_tail_route_and_bounds(self):
        from app.services.neorain_google_overrides import cell_number
        row={'flight':'UPS0948','direction':'departure','gateway':'RFD','sort_date':self.operation.sort_date.isoformat(),'sort_name':self.operation.sort_name,'place':'SDF','tail':self.departure.assigned_tail_number,'row':3}
        self.assertEqual(match_row(self.departure,[row]),row)
        for rows in ([],[row,row],[{**row,'sort_date':'2020-01-01'}],[{**row,'direction':'arrival'}],[{**row,'gateway':'SDF'}],[{**row,'tail':'N999UP'}],[{**row,'place':'LAX'}]):
            with self.assertRaises(GoogleOverrideBlocked):match_row(self.departure,rows)
        self.assertEqual(cell_number('sta',50),'AB50')
        with self.assertRaises(GoogleOverrideBlocked):cell_number('sta',51)
        with self.assertRaises(GoogleOverrideBlocked):cell_number('block_in',54)
