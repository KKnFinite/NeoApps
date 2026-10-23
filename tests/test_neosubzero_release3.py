"""Actual spray reporting, authorized Driver entry and durable retry contracts."""
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock
from flask import g
from sqlalchemy import text, inspect
from tests import test_neosubzero_release2 as r2
from app.extensions import db
from app.models import (NeoSubZeroSprayRecord, NeoSubZeroArchiveDelivery, NeoSubZeroAuditEntry,
                        NeoSubZeroUccAssignment, SortDateOperation)
from app.services.neosubzero_departure_deice import mutate_departure_deice
from app.services.neosubzero_participation import participation_query,change_participation
from app.services.neosubzero_evidence import application_context,save_application_context,evidence_values
from app.services.neosubzero_reporting import actual_passes,report_text,archive_payload,learning_context
from app.services.neosubzero_archive import drain,status
from app.services.neosubzero_spray import set_neosubzero_spray_gallons
from app.services.live_collaboration import entity_version


class Release3Test(r2.Release2Test):
    def test_start_context_frozen_then_end_snapshots_official_crew(self):
        f=self.f
        save_application_context(f.operation,dict(active_precipitation='Snow',ambient_temperature='20 F',dew_point='18 F'),actor_id=None);db.session.commit()
        event,values=self.flow(end='')
        save_application_context(f.operation,dict(expected_version=application_context(f.operation)['version'],active_precipitation='Rain',ambient_temperature='40 F'),actor_id=None)
        values['pass1_end']='2205';mutate_departure_deice(f.operation,f.alpha,'save_treatment',values,event=event);db.session.commit()
        record=participation_query(event).one();row=actual_passes(f.operation)[0]
        self.assertEqual((record.active_precipitation,row['precipitation'],row['temperature']),('Snow','Snow','20 F'))
        self.assertEqual(row['weather_provenance'],'with_start')
        self.assertIsNotNone(record.official_at_utc)

    def test_end_only_context_is_not_mislabeled_pass_start_weather(self):
        f=self.f
        save_application_context(f.operation,dict(active_precipitation='Rain',ambient_temperature='40 F'),actor_id=None);db.session.commit()
        self.flow()
        row=actual_passes(f.operation)[0]
        self.assertIsNone(row['temperature']);self.assertIsNone(row['precipitation'])
        self.assertEqual(row['weather_provenance'],'with_end')
        self.assertEqual(row['records'][0].active_precipitation,'Rain') # Raw context retained.

    def test_report_groups_multiple_trucks_and_copy_exact_original_evidence(self):
        f=self.f;event,values=self.flow(end='')
        from app.services.neosubzero_departure_deice import neosubzero_fluid_settings
        change_participation(f.operation,f.alpha,event,'departure','add_truck',dict(pass_number=1,truck_number='T18'),neosubzero_fluid_settings(f.gateway),actor_id=None)
        values['pass1_end']='2205';mutate_departure_deice(f.operation,f.alpha,'save_treatment',values,event=event);db.session.commit()
        row=actual_passes(f.operation)[0]
        self.assertEqual(len(row['records']),2);self.assertEqual(row['missing_gallons'],2)
        output=report_text([row]);self.assertIn('TRUCK T12',output);self.assertIn('TRUCK T18',output);self.assertEqual(output.count('GALLONS NOT ENTERED'),2)
        self.assertIn('WINGS ONLY',output);self.assertIn(f.alpha.flight_number,output)
        old=row['tail'];f.alpha.assigned_tail_number='OTHER';f.alpha.flight_number='CHANGED';db.session.commit()
        row=actual_passes(f.operation,tail=old.lower())[0]
        self.assertEqual(row['tail'],old);self.assertNotIn('CHANGED',row['copy_text'])
        self.assertEqual(actual_passes(f.operation,tail='NO-MATCH'),[])

    def test_report_includes_pretreat_but_never_unsprayed_clear(self):
        f=self.f
        from app.neonodes.neosubzero.services import mutate_pretreat
        mutate_pretreat(f.operation,f.alpha,'save_treatment',dict(pass1_surface_area='wings_tail',pass2_surface_area='entire_aircraft',pass1_start='2100',pass1_end='2102',pass2_start='2103',pass2_end='2106'))
        mutate_departure_deice(f.operation,f.alpha,'clear',{});db.session.commit()
        rows=actual_passes(f.operation)
        self.assertEqual(len(rows),2);self.assertEqual({r['activity'] for r in rows},{'pretreat'})
        self.assertEqual(actual_passes(f.operation,activity='departure'),[])
        self.assertEqual(rows[1]['surface'],'ENTIRE AIRCRAFT')

    def test_in_progress_evidence_retained_without_fake_end_or_participants(self):
        event,_=self.flow(end='');row=actual_passes(self.f.operation)[0]
        self.assertIsNone(row['end_utc']);self.assertIn('END NOT RECORDED',row['copy_text'])
        self.assertEqual(row['records'],[])
        self.assertIsNone(archive_payload(row)['end_utc'])

    def test_empty_sort_rollover_changes_revision_without_frost_negative(self):
        from app.services.neosubzero_reporting import report_version
        f=self.f;other=SortDateOperation(sort_date=f.operation.sort_date+timedelta(days=1),gateway_id=f.gateway.id,gateway_code=f.gateway.code,sort_name=f.operation.sort_name)
        db.session.add(other);db.session.commit()
        self.assertNotEqual(report_version(f.operation,[]),report_version(other,[]))
        self.assertEqual(actual_passes(other),[])
        with patch('app.services.neoscorpion_learning_vault.export_subzero_record') as export:
            self.assertEqual(drain(f.gateway.id),0);export.assert_not_called()

    def test_deice_pad_location_uses_queue_not_parking_ten(self):
        f=self.f;f.alpha.actual_block_out_datetime_utc=datetime(2026,9,1,1);db.session.commit()
        event,_=self.flow(end='')
        self.assertEqual(event.pass_evidence['1']['parking'],'DEICE PAD')

    def test_missing_gallons_and_invalid_zero_do_not_block_clear(self):
        event,_=self.flow();record=participation_query(event).one()
        with self.assertRaises(ValueError):set_neosubzero_spray_gallons(self.f.operation,self.f.alpha,event,1,1,'0',record=record)
        self.assertIsNone(record.gallons)
        mutate_departure_deice(self.f.operation,self.f.alpha,'clear',{},event=event);db.session.commit()
        self.assertEqual(event.status,'cleared');self.assertEqual(actual_passes(self.f.operation)[0]['missing_gallons'],1)

    def test_ended_pass_surface_needs_audited_correction(self):
        f=self.f;event,values=self.flow('type_i_type_iv')
        values['pass1_surface_area']='entire_aircraft'
        with self.assertRaises(ValueError):mutate_departure_deice(f.operation,f.alpha,'save_treatment',values,event=event)
        db.session.rollback();self.assertEqual(participation_query(event).one().surface_area,'wings_only')

    def test_archive_outage_retries_idempotently_and_propagates_corrections(self):
        f=self.f;event,_=self.flow();record=participation_query(event).one()
        with patch('app.services.neoscorpion_learning_vault.export_subzero_record',side_effect=ValueError('outage')):
            self.assertEqual(drain(f.gateway.id),0)
        receipt=NeoSubZeroArchiveDelivery.query.one()
        self.assertIsNone(receipt.delivered_checksum);self.assertIsNotNone(receipt.last_error)
        receipt.retry_at=datetime.utcnow()-timedelta(seconds=1);db.session.commit()
        saved=[]
        def export(payload,**kwargs):
            saved.append((payload,kwargs));return dict(key='test/'+str(len(saved)))
        with patch('app.services.neoscorpion_learning_vault.export_subzero_record',side_effect=export):
            self.assertEqual(drain(f.gateway.id),1);self.assertEqual(drain(f.gateway.id),0)
            old=receipt.delivered_checksum
            set_neosubzero_spray_gallons(f.operation,f.alpha,event,1,1,'17.5',record=record,correction_reason='Actual reading');db.session.commit()
            self.assertEqual(drain(f.gateway.id),1)
        self.assertEqual(len(saved),4);self.assertNotEqual(receipt.delivered_checksum,old)
        self.assertEqual(NeoSubZeroArchiveDelivery.query.count(),1)
        self.assertEqual(saved[2][0]['participants'][0]['gallons'],'17.50')
        self.assertTrue(saved[2][0]['audit_provenance'])
        self.assertIsNone(saved[3][0]['frost_observed']);self.assertFalse(saved[3][0]['training_eligible'])
        self.assertEqual(status(f.operation)['pending'],0)

    def test_archive_partial_failure_does_not_mark_success(self):
        self.flow()
        with patch('app.services.neoscorpion_learning_vault.export_subzero_record',side_effect=[dict(key='written'),ValueError('learning unavailable')]):
            self.assertEqual(drain(self.f.gateway.id),0)
        self.assertIsNone(NeoSubZeroArchiveDelivery.query.one().delivered_checksum)

    def test_retracted_start_archives_correction_not_unsprayed_observation(self):
        f=self.f;event,values=self.flow(end='');exports=[]
        def export(payload,**kwargs):exports.append(payload);return dict(key='fixture')
        with patch('app.services.neoscorpion_learning_vault.export_subzero_record',side_effect=export):
            self.assertEqual(drain(f.gateway.id),1)
            values['pass1_start']='';mutate_departure_deice(f.operation,f.alpha,'save_treatment',values,event=event);db.session.commit()
            self.assertEqual(drain(f.gateway.id),1)
        self.assertEqual(exports[2]['record_type'],'spray_evidence_correction')
        self.assertEqual(exports[2]['state'],'start_retracted');self.assertTrue(exports[2]['audit_provenance'])
        self.assertEqual(actual_passes(f.operation),[])
        self.assertIsNone(exports[3]['frost_observed']);self.assertFalse(exports[3]['training_eligible'])

    def test_archive_provider_checksum_idempotency_and_unsprayed_rejection(self):
        self.flow();payload=archive_payload(actual_passes(self.f.operation)[0])
        from app.services.neoscorpion_learning_vault import export_subzero_record,_canonical_json
        import hashlib
        client=MagicMock();digest=hashlib.sha256(_canonical_json(payload)).hexdigest()
        client.head_object.return_value={'Metadata':{'checksum':digest}}
        with patch('app.services.neoscorpion_learning_vault._require_r2_config',return_value={'bucket':'fixture'}),patch('app.services.neoscorpion_learning_vault._r2_client',return_value=client):
            self.assertTrue(export_subzero_record(payload)['already_saved']);client.put_object.assert_not_called()
            with self.assertRaises(ValueError):export_subzero_record({**payload,'record_type':'negative'})
            with self.assertRaises(ValueError):export_subzero_record({**learning_context(payload),'frost_observed':False},learning=True)

    def test_archive_bootstrap_is_additive_repeatable_preserves_evidence(self):
        self.flow();before=[evidence_values(r) for r in NeoSubZeroSprayRecord.query.all()]
        db.session.remove()
        with db.engine.begin() as connection:connection.execute(text('DROP TABLE neosubzero_archive_deliveries'))
        from app.services.schema_sync import sync_database_schema
        sync_database_schema(self.f.app);db.session.commit()
        sync_database_schema(self.f.app);db.session.commit()
        from app.services.neosubzero_schema import verify_neosubzero_schema_contract
        verify_neosubzero_schema_contract(db.session.connection())
        self.assertTrue(inspect(db.engine).has_table('neosubzero_archive_deliveries'))
        self.assertEqual([evidence_values(r) for r in NeoSubZeroSprayRecord.query.all()],before)

    def test_historical_report_filters_and_gateway_permissions(self):
        f=self.f;self.flow();client=self.client()
        with patch('app.neonodes.neosubzero.routes.current_neosubzero_operation',return_value=None):
            self.assertIn(b'No current sort. Select a historical sort',client.get('/neosubzero/deice-log').data)
            response=client.get('/neosubzero/deice-log',query_string=dict(operation_id=f.operation.id,activity='departure',tail=f.alpha.assigned_tail_number))
            self.assertEqual(response.status_code,200)
            self.assertIn(b'COPY PASS',response.data);self.assertNotIn(b'SAVE GALLONS',response.data)
            self.assertEqual(client.get('/neosubzero/deice-log?operation_id=999999').status_code,404)
        self.assertIn(b'COPY REPORT',response.data)

    def test_legacy_sort_gateway_code_is_authoritative_without_rewriting_sort(self):
        f=self.f;self.flow();f.operation.gateway_id=None;db.session.commit()
        client=self.client()
        with patch('app.neonodes.neosubzero.routes.current_neosubzero_operation',return_value=None):
            response=client.get('/neosubzero/deice-log',query_string=dict(operation_id=f.operation.id))
            self.assertEqual(response.status_code,200)
        self.assertEqual(archive_payload(actual_passes(f.operation)[0])['gateway_id'],f.gateway.id)
        with patch('app.services.neoscorpion_learning_vault.export_subzero_record',return_value=dict(key='fixture')):
            self.assertEqual(drain(f.gateway.id),1)
        self.assertIsNone(f.operation.gateway_id)

    def test_driver_failed_save_preserves_record_and_stale_version(self):
        f=self.f;event,_=self.flow();record=participation_query(event).one();client=self.client('operator',f.permanent)
        token=entity_version(record)
        self.assertEqual(self.post(client,'spray-gallons',board='deicer',record_id=record.id,expected_version=token,gallons='NaN').status_code,409)
        self.assertIsNone(record.gallons)
        response=self.post(client,'spray-gallons',board='deicer',record_id=record.id,expected_version=token,gallons='12.5')
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.post(client,'spray-gallons',board='deicer',record_id=record.id,expected_version=token,gallons='13').status_code,409)
        self.assertEqual(record.gallons,Decimal('12.50'))
