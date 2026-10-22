"""Dispatcher attestation uses shared measured data, never physical work."""
import re
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from app.extensions import db
from app.models import (NeoScorpionDirectEntry, NeoScorpionFuelWorkState,
    NeoScorpionFuelingEvent, NeoScorpionFuelAuditEntry, NeoScorpionTruckInventoryAudit)
from app.services import neoscorpion as fuel, neoscorpion_direct_entry as direct
from tests import test_neoscorpion_fueler_submission as base


class DirectEntryTest(unittest.TestCase):
    setUp=base.SubmissionTest.setUp
    prepare=base.SubmissionTest.prepare
    save=base.SubmissionTest.save
    off=base.SubmissionTest.off
    final_data=base.SubmissionTest.final_data
    baseline=base.SubmissionTest.baseline
    row=base.SubmissionTest.row
    submit=base.SubmissionTest.submit
    login=base.SubmissionTest.login

    def form(self, **values):
        return dict(direct_entry_token=direct.token_for(self.operation,self.mission,self.dispatcher),
            actual_left='14',actual_ctr='0',actual_right='14',**values)

    @property
    def url(self):return f'/neoscorpion/fuel-dispatch/missions/{self.mission.id}/direct-entry'

    def complete(self,form=None):
        result=direct.complete_entry(self.gateway,self.dispatcher,self.mission.id,form or self.form())
        db.session.commit();return result

    def test_actual_only_completes_without_physical_or_inventory_evidence(self):
        before=NeoScorpionTruckInventoryAudit.query.count()
        record,changed=self.complete()
        self.assertTrue(changed);self.assertEqual(record.completed_by_user_id,self.dispatcher.id)
        self.assertEqual(record.tail_number,'N491UP');self.assertEqual(self.mission.fuel_status,'complete')
        self.assertEqual(record.completed_at_utc,self.mission.fuel_completed_at_utc)
        self.assertIsNone(self.assignment.assigned_truck_id);self.assertIsNone(self.assignment.assigned_fueler_user_id)
        self.assertIsNone(self.assignment.completed_at_utc)
        for model in (NeoScorpionFuelWorkState,NeoScorpionFuelingEvent,NeoScorpionFuelAuditEntry):self.assertEqual(model.query.count(),0)
        self.assertEqual(NeoScorpionTruckInventoryAudit.query.count(),before)
        row=self.row();self.assertEqual(row['dispatch_status_key'],'complete')
        self.assertEqual(row['actual_total_lbs'],28000);self.assertIsNone(row['remaining_total_lbs'])
        self.assertIsNone(row['neo_fuel_lbs']);self.assertIsNone(row['apu_allowance_lbs'])
        self.assertEqual(row['neo_fuel_display'],'UNKNOWN');self.assertEqual(row['apu_allowance_display'],'UNKNOWN')
        self.assertIsNone(record.snapshot['planned_lbs_by_tank']);self.assertIsNone(record.snapshot['transfer_fuel_gallons'])

    def test_optional_fields_use_canonical_calculations_and_freeze_evidence(self):
        record,_=self.complete(self.form(remaining_left='6',remaining_ctr='0',remaining_right='6',
            apu_running='yes',apu_source_tank_code='left',apu_override_enabled='1',
            apu_override_allowance='0.5',transfer_fuel_gallons='1200',notes='Measured before sort'))
        row=self.row();self.assertEqual(row['neo_fuel_lbs'],27500)
        self.assertEqual(row['transfer_fuel_gallons'],1200);self.assertEqual(row['fueler_notes'],'Measured before sort')
        self.assertIn('NEO > 27.5',row['load_planning_output'])
        original=record.snapshot.copy()
        from app.models import NeoScorpionAircraftFuelSetting
        db.session.add(NeoScorpionAircraftFuelSetting(gateway_id=self.gateway.id,aircraft_type='B757',
            apu_rate_thousand_lbs_per_hour='0.9',max_lateral_imbalance_lbs=200))
        db.session.commit()
        self.assertEqual(self.row()['planned_total_display'],row['planned_total_display'])
        self.assertEqual(self.row()['neo_fuel_lbs'],27500)
        self.assertEqual(record.snapshot,original)
        record.snapshot={'changed':True}
        with self.assertRaisesRegex(ValueError,'immutable'):db.session.commit()
        db.session.rollback()

    def test_invalid_inputs_are_validation_errors_and_leave_no_partial_record(self):
        for changes in ({'actual_left':''},{'actual_ctr':'NaN'},{'actual_right':'Infinity'},
                {'actual_left':'-1'},{'actual_left':'1e3'},{'actual_fake':'1'},
                {'apu_running':'yes'},{'transfer_fuel_gallons':'-1'},
                {'apu_running':'yes','apu_source_tank_code':'left','apu_override_enabled':'1','apu_override_allowance':'99'}):
            with self.subTest(changes=changes):
                form=self.form();form.update(changes)
                with self.assertRaises(ValueError):self.complete(form)
                db.session.rollback();self.assertEqual(NeoScorpionDirectEntry.query.count(),0)
                self.assertNotEqual(self.mission.fuel_status,'complete')

    def test_duplicate_is_idempotent_but_changed_retry_or_second_dispatcher_is_conflict(self):
        form=self.form();record,_=self.complete(form)
        duplicate,changed=self.complete(form);self.assertFalse(changed);self.assertEqual(record.id,duplicate.id)
        for payload in ({**form,'actual_left':'15'},self.form()):
            with self.assertRaises(direct.DirectEntryConflict):self.complete(payload)
            db.session.rollback()
        self.assertEqual(NeoScorpionDirectEntry.query.count(),1)

    def test_stale_tail_target_sort_and_invalid_token_rejected(self):
        for field,value in (('assigned_tail_number','N492UP'),('planned_fuel_load',40000)):
            form=self.form();old=getattr(self.mission,field);setattr(self.mission,field,value);db.session.commit()
            with self.assertRaises(direct.DirectEntryConflict):self.complete(form)
            db.session.rollback();setattr(self.mission,field,old);db.session.commit()
        with self.assertRaises(direct.DirectEntryConflict):self.complete({**self.form(),'direct_entry_token':'bad'})
        db.session.rollback()

    def test_assigned_started_submitted_and_completed_work_cannot_be_bypassed(self):
        form=self.form();self.prepare()
        with self.assertRaises(direct.DirectEntryConflict):self.complete(form)
        db.session.rollback();self.assertEqual(NeoScorpionDirectEntry.query.count(),0)
        self.save(self.start+timedelta(minutes=1),**self.final_data());self.off();self.submit()
        with self.assertRaises(direct.DirectEntryConflict):self.complete(form)
        db.session.rollback()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        with self.assertRaises(direct.DirectEntryConflict):self.complete(form)
        db.session.rollback();self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_original_tail_snapshot_remains_viewable_after_replacement(self):
        record,_=self.complete(self.form(apu_running='no'))
        self.mission.assigned_tail_number='N492UP';db.session.commit()
        row=self.row();self.assertEqual(row['dispatch_status_key'],'review')
        self.assertIsNone(row['actual_total_lbs'])
        self.login(self.dispatcher);response=self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        self.assertIn('Recorded aircraft N491UP',response.json['html'])
        self.assertIn('Current mission aircraft is N492UP',response.json['html'])
        self.assertIn('28.0',response.json['html']);self.assertNotIn('data-direct-entry-form',response.json['html'])

    def test_cleared_resource_history_does_not_advertise_direct_entry(self):
        fuel._new_fuel_audit(self.operation,self.assignment,self.dispatcher,'swap_truck','assigned_truck_id',
            '20',None,'Prior dispatched resources were cleared.')
        db.session.commit()
        self.assertFalse(self.row()['direct_entry_available'])
        self.login(self.dispatcher);self.assertEqual(self.client.get(self.url).status_code,409)
        with self.assertRaises(direct.DirectEntryConflict):self.complete(self.form())
        db.session.rollback()

    def test_changed_configuration_and_operational_sort_reject_stale_draft(self):
        form=self.form()
        from app.models import NeoScorpionAircraftFuelSetting
        db.session.add(NeoScorpionAircraftFuelSetting(gateway_id=self.gateway.id,aircraft_type='B757',apu_rate_thousand_lbs_per_hour='0.9'))
        db.session.commit()
        with self.assertRaises(direct.DirectEntryConflict):self.complete(form)
        db.session.rollback();form=self.form()
        with patch('app.services.neoscorpion.current_sort_operation',return_value=None):
            with self.assertRaises(direct.DirectEntryConflict):self.complete(form)
        db.session.rollback();self.assertEqual(NeoScorpionDirectEntry.query.count(),0)

    def test_every_configured_aircraft_requires_each_applicable_actual_tank(self):
        for index,tail in enumerate(('N123UP','N456UP','N345UP','N567UP','N678UP')):
            with self.subTest(tail=tail):
                mission=self.fixture._mission('UPS-DIRECT-'+str(index),tail,25000,1);db.session.commit()
                layout=fuel.tank_layout_for_tail(tail)
                form=dict(direct_entry_token=direct.token_for(self.operation,mission,self.dispatcher),apu_running='no',
                    **{'actual_'+code:'5' for code,_ in layout})
                missing=dict(form);missing.pop('actual_'+layout[-1][0])
                with self.assertRaisesRegex(ValueError,'Actual'):direct.complete_entry(self.gateway,self.dispatcher,mission.id,missing)
                db.session.rollback()
                record,_=direct.complete_entry(self.gateway,self.dispatcher,mission.id,form);db.session.commit()
                self.assertEqual(len(record.snapshot['tanks']),len(layout))
                self.assertEqual(direct.completed_values(record)[0],len(layout)*5000)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),0)

    def test_canonical_completed_reader_recognizes_attestation_without_transfer_event(self):
        from app.services.neorain_fuel_authority import completed_scorpion_fuel_by_mission,scorpion_board_fuel_by_mission
        self.complete(self.form(apu_running='no'))
        final=completed_scorpion_fuel_by_mission(self.operation)[self.mission.id]
        self.assertEqual(str(final['neo_fuel']).replace(',',''),'28000')
        board=scorpion_board_fuel_by_mission(self.operation,[self.mission])[self.mission.id]
        self.assertTrue(board['scorpion_fuel_final']);self.assertEqual(board['scorpion_fuel_display'],'28.0')
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_http_permissions_validation_persistence_and_shared_current_layout(self):
        self.login(self.fueler);self.assertEqual(self.client.get(self.url).status_code,403)
        self.assertEqual(self.client.post(self.url,data=self.form()).status_code,403)
        self.login(self.dispatcher)
        response=self.client.get(self.url);self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        html=response.json['html'];self.assertIn('data-screen-mode="dispatcher_direct_entry"',html)
        self.assertIn('scorpion-fueler-mission',html);self.assertIn('scorpion-fueler-apu',html)
        self.assertNotIn('data-truck-on-form',html);self.assertNotIn('data-fueler-submit-form',html)
        token=re.search('name="direct_entry_token" value="([^"]+)"',html).group(1)
        data={**self.form(),'direct_entry_token':token}
        invalid=self.client.post(self.url,data={**data,'actual_left':''})
        self.assertEqual(invalid.status_code,400);self.assertIn('Actual',invalid.json['error'])
        result=self.client.post(self.url,data=data);self.assertEqual(result.status_code,200,result.get_data(as_text=True))
        self.assertTrue(result.json['direct_entry_receipt']['id']);self.assertNotIn('data-direct-entry-form',result.json['html'])
        self.assertIn('28.0',result.json['html']);self.assertIn('NOT CONFIRMED',result.json['html'])
        self.assertEqual(self.client.post(self.url,data=data).status_code,200)
        html=self.client.get('/neoscorpion/fuel-dispatch').get_data(as_text=True)
        self.assertIn('data-direct-entry-evidence',html);self.assertNotIn('>ENTER AND COMPLETE</button>',html)
        self.assertIn(self.url,html)

    def test_dispatcher_open_and_fueler_use_same_layout_but_not_operational_permissions(self):
        self.prepare();self.save(self.start+timedelta(minutes=1),**self.final_data())
        self.login(self.dispatcher)
        response=self.client.get(f'/neoscorpion/fuel-dispatch/fueler-data/{self.assignment.id}')
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        html=response.json['html']
        self.assertIn('scorpion-fueler-mission',html);self.assertIn('scorpion-fueler-apu',html)
        self.assertNotIn('data-truck-on-form',html);self.assertNotIn('data-fueler-submit-form',html)
        self.assertNotIn('data-apu-editor-form',html);self.assertIn('Save Fuel Entry',html)
        self.assertLess(html.index('neoscorpion-fuel-tank-grid'),html.index('scorpion-fueler-apu'))
        self.off();self.submit();fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        response=self.client.get(f'/neoscorpion/fuel-dispatch/fueler-data/{self.assignment.id}')
        self.assertEqual(response.status_code,200);self.assertIn('30.0',response.json['html'])
        self.assertNotIn('Save Fuel Entry',response.json['html'])
