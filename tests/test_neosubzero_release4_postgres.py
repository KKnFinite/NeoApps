"""One realistic, isolated overnight sort plus FROST transaction contracts."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from unittest.mock import patch
from flask import g
from tests import test_neosubzero_ucc_postgres as pg,test_neosubzero_release2 as helpers,test_frost_vault as contracts
from app.extensions import db
from app.models import (SortDateOperation,SortDateParkingAssignment,StaffingUnit,
    NeoSubZeroUccTruckAssignment,NeoSubZeroUccAssignment,NeoSubZeroSprayRecord,FrostDocument)
from app.services.neosubzero_ucc import set_neosubzero_ucc_assignment,ucc_staffing_version,neosubzero_ucc_context
from app.services.neosubzero_staffing import set_staffing_person_qualification,DEICE_QUALIFICATION_KEY
from app.services.neosubzero_departure_deice import mutate_departure_deice,neosubzero_fluid_settings
from app.services.neosubzero_participation import participation_query,change_participation
from app.services.neosubzero_evidence import save_application_context
from app.services.neosubzero_reporting import actual_passes,report_text
from app.services.live_collaboration import entity_version
from app.services import neosubzero_archive as archive,frost_vault as vault


@unittest.skipUnless(os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL'),'Requires isolated loopback PostgreSQL')
class Release4PostgresTest(contracts.FrostVaultTest):
    drop_schema=pg.UccPostgresTest.drop_schema
    post=helpers.Release2Test.post
    def setUp(self):
        pg.UccPostgresTest.setUp(self);self.f=self.fixture

    def test_complete_multi_aircraft_sort_and_archive_handoff(self):
        f=self.f;op=f.operation
        work_area=StaffingUnit.query.filter_by(name='Deice').one()
        ramps=['Alpha','Bravo','Charlie','Delta','Echo','Remote']
        missions=[f.alpha]
        for index,ramp in enumerate(ramps[1:5],1):
            mission=f._departure('UPS-R4-'+ramp,'N-R4-'+ramp,'DEN',3)
            db.session.add(SortDateParkingAssignment(sort_date_operation_id=op.id,
                tail_number=mission.assigned_tail_number,ramp_code='ABCDE'[index],position_code='ABCDE'[index]+'03'))
            missions.append(mission)
        missions.append(f.remote)
        for index,ramp in enumerate(ramps):
            for role in ('driver','flyer'):
                if ramp=='Alpha' and role=='driver':person=f.permanent
                else:
                    person=f._person('R4-'+ramp+role,ramp,role,work_area)
                    set_staffing_person_qualification(person,DEICE_QUALIFICATION_KEY,True)
                set_neosubzero_ucc_assignment(op,ramp,1,role,person)
            db.session.add(NeoSubZeroUccTruckAssignment(sort_date_operation_id=op.id,ramp=ramp,
                position_number=1,truck_number='T-'+ramp))
        save_application_context(op,dict(active_precipitation='None',ambient_temperature='25 F',dew_point='24 F',reason_for_application='Frost'),actor_id=None)
        from app.neonodes.neosubzero.services import mutate_pretreat,pretreat_context
        pretreat=mutate_pretreat(op,f.alpha,'save_treatment',dict(pass1_surface_area='wings_tail',pass2_surface_area='entire_aircraft',
            pass1_start='2100',pass1_end='2102',pass2_start='2103',pass2_end='2105'))
        db.session.commit()
        self.assertTrue(next(row for row in pretreat_context(f.gateway,op)['rows'] if row['tail']==f.alpha.assigned_tail_number)['completed'])
        waiting=[]
        for index in range(3):
            mission=f._departure('UPS-QUEUE-'+str(index),'N-Q'+str(index),'SDF',3)
            db.session.add(SortDateParkingAssignment(sort_date_operation_id=op.id,tail_number=mission.assigned_tail_number,ramp_code='A',position_code='A0'+str(4+index)))
            mission.actual_block_out_datetime_utc=datetime(2026,9,1,1,index+1)
            mutate_departure_deice(op,mission,'initial_contact',{});waiting.append(mission)
        for mission in missions:mission.actual_block_out_datetime_utc=datetime(2026,9,1,1)
        db.session.commit()
        coordinator=self.client('simulator');events=[]
        for index,(ramp,mission) in enumerate(zip(ramps,missions)):
            event=mutate_departure_deice(op,mission,'initial_contact',{});db.session.commit()
            values=dict(treatment_plan='type_i_type_iv',pass1_surface_area='wings_only',pass2_surface_area='wings_tail',pass1_start='2200')
            mutate_departure_deice(op,mission,'save_treatment',values,event=event);db.session.commit()
            if ramp=='Alpha':
                change_participation(op,mission,event,'departure','add_truck',dict(pass_number=1,truck_number='T-Bravo'),neosubzero_fluid_settings(f.gateway),actor_id=None)
            values['pass1_end']='2205';mutate_departure_deice(op,mission,'save_treatment',values,event=event);db.session.commit()
            frozen=[(r.id,r.truck_number_snapshot,r.driver_person_id,r.started_at_utc,r.ended_at_utc) for r in participation_query(event).all()]
            if ramp=='Alpha':
                flyer=NeoSubZeroUccAssignment.query.filter_by(sort_date_operation_id=op.id,ramp='Alpha',position_number=1,team_role='flyer').one()
                result=self.post(coordinator,'team',action='change_deicers',ramp='Alpha',position_number=1,
                    driver_person_id=f.callout.id,flyer_person_id=flyer.person_id,staffing_version=ucc_staffing_version(op))
                self.assertEqual(result.status_code,200,result.data)
                result=self.post(coordinator,'team',action='change_truck',ramp='Alpha',position_number=1,
                    truck_number='T-REPLACEMENT',staffing_version=ucc_staffing_version(op))
                self.assertEqual(result.status_code,200,result.data)
            values.update(pass2_start='2206',pass2_end='2209')
            mutate_departure_deice(op,mission,'save_treatment',values,event=event);db.session.commit()
            self.assertEqual(event.status,'finished')
            self.assertEqual([(r.id,r.truck_number_snapshot,r.driver_person_id,r.started_at_utc,r.ended_at_utc)
                for r in participation_query(event).filter_by(pass_number=1).all()],frozen)
            mutate_departure_deice(op,mission,'clear',{},event=event);db.session.commit();events.append(event)
        self.assertEqual(len(actual_passes(op)),14)
        self.assertEqual(len(participation_query(events[0]).filter_by(pass_number=1).all()),2)
        second=participation_query(events[0]).filter_by(pass_number=2).one()
        self.assertEqual((second.truck_number_snapshot,second.driver_person_id),('T-REPLACEMENT',f.callout.id))
        self.assertEqual(second.concentration_percent_snapshot,100)
        alpha_ramp=neosubzero_ucc_context(f.gateway,op)['ramps'][0]
        self.assertEqual(alpha_ramp['pad']['mission_id'],waiting[0].id)
        for index,mission in enumerate(waiting):
            event=mutate_departure_deice(op,mission,'set_negative' if index==0 else 'manual_not_sprayed',{})
            self.assertNotEqual(event.status,'cleared')
            mutate_departure_deice(op,mission,'clear',{},event=event);db.session.commit()
        self.assertIsNone(neosubzero_ucc_context(f.gateway,op)['ramps'][0]['pad'])
        # Driver is no longer assigned to Alpha, but retains their documented pass.
        driver=self.client('operator',f.permanent)
        first=participation_query(events[0]).filter_by(pass_number=1,truck_number_snapshot='T-Alpha').one()
        self.assertIsNone(first.gallons)
        response=self.post(driver,'spray-gallons',board='deicer',record_id=first.id,expected_version=entity_version(first),gallons='17.5')
        self.assertEqual(response.status_code,200,response.data)
        clear_time=events[0].cleared_at_utc
        response=self.post(coordinator,'participation',action='correct_participant',pass_number=1,
            record_id=first.id,record_version=entity_version(first),expected_version=entity_version(events[0]),
            truck_number='T-Alpha',crew_confirmed='yes',driver_person_id=f.permanent.id,correction_reason='Confirmed original crew')
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(events[0].cleared_at_utc,clear_time)
        output=report_text(actual_passes(op));self.assertIn('GALLONS 17.50',output)
        self.assertIn('DEICE PAD',output);self.assertIn('PRETREAT',output)
        # Swap effective flight tails after CLEAR: original report ownership stays.
        original=events[0].tail_number
        f.alpha.assigned_tail_number,missions[1].assigned_tail_number=missions[1].assigned_tail_number,f.alpha.assigned_tail_number
        db.session.commit()
        self.assertEqual(events[0].tail_number,original)
        self.assertEqual(pretreat.tail_number,original)
        self.storage()
        self.assertEqual(archive.drain(f.gateway.id,limit=100),14)
        from app.models import NeoSubZeroArchiveDelivery
        receipts=NeoSubZeroArchiveDelivery.query.all()
        self.assertTrue(all(row.vault_key.startswith(vault.PREFIX) for row in receipts))
        for receipt in receipts:
            evidence=vault.read_json(receipt.vault_key,f.gateway.id)
            self.assertEqual(evidence['id'],receipt.source_id)
            self.assertTrue(evidence['audit_provenance'])
        tomorrow=SortDateOperation(gateway_id=f.gateway.id,gateway_code='RFD',sort_date=op.sort_date+timedelta(days=1),sort_name='night')
        db.session.add(tomorrow);db.session.commit()
        self.assertEqual(actual_passes(tomorrow),[]);self.assertEqual(len(actual_passes(op)),14)

    def test_concurrent_duplicate_document_requests_create_only_one_catalog_row(self):
        client=self.client('master')
        with client.session_transaction() as session:cookie=dict(session)
        from threading import Barrier
        import io
        barrier=Barrier(2);raw=contracts.sample()
        def upload(index):
            client=self.f.app.test_client()
            with client.session_transaction() as session:session.update(cookie)
            barrier.wait(timeout=5)
            return client.post('/neosubzero/frost-vault',data={'document':(io.BytesIO(raw),'same.csv')},headers={'X-Requested-With':'XMLHttpRequest'}).status_code
        with ThreadPoolExecutor(2) as pool:
            self.assertCountEqual(list(pool.map(upload,range(2))),[200,409])
        db.session.expire_all();self.assertEqual(FrostDocument.query.count(),1)
