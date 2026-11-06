"""T/F entry boundaries, pre-write rejection and canonical save/completion."""
import json
import unittest
from unittest.mock import patch

import pytest
from sqlalchemy import event
from tests import test_neoscorpion_fueler_submission as fixtures
from tests import test_neoscorpion_direct_entry as direct_fixtures
from app.extensions import db
from app.models import NeoScorpionDirectEntry, NeoScorpionFuelingEvent
from app.services import neoscorpion as fuel, neoscorpion_direct_entry as direct
from app.services.neoscorpion_quick_fuel import calculate_quick_fuel
from app.services.neoscorpion_transfer_fuel import parse_transfer_fuel_gallons, TransferFuelValidationError


OVERSIZED = '985925558080'  # Actual rejected assignment #750 input in Render logs.
INVALID = ('-1','1.5','1.0','1e3','9,999','garbage','17001',OVERSIZED)


@pytest.mark.parametrize('value,expected', [(None,None),('',None),('0',0),(0,0),('1',1),('9999',9999),('17000',17000),('00017000',17000)])
def test_transfer_fuel_accepts_exact_whole_gallon_boundaries(value, expected):
    assert parse_transfer_fuel_gallons(value) == expected


@pytest.mark.parametrize('value', [*INVALID,True,1.0,'NaN','Infinity','١','9'*5000])
def test_transfer_fuel_rejects_without_rounding_or_overflow(value):
    with pytest.raises(TransferFuelValidationError):
        parse_transfer_fuel_gallons(value)


class TransferFuelPersistenceTest(unittest.TestCase):
    setUp = fixtures.SubmissionTest.setUp
    prepare = fixtures.SubmissionTest.prepare
    save = fixtures.SubmissionTest.save
    off = fixtures.SubmissionTest.off
    final_data = fixtures.SubmissionTest.final_data
    baseline = fixtures.SubmissionTest.baseline
    submit = fixtures.SubmissionTest.submit
    login = fixtures.SubmissionTest.login

    def test_invalid_service_inputs_cannot_lock_write_or_invoke_spear(self):
        self.prepare()
        writes = []
        def record(_conn,_cursor,statement,_parameters,_context,_many):
            if statement.lstrip().split(None,1)[0].upper() in ('INSERT','UPDATE','DELETE'):
                writes.append(statement)
        event.listen(db.engine,'before_cursor_execute',record)
        try:
            with patch.object(fuel,'lock_nightly_asset_scope_for_mutation',side_effect=AssertionError('Invalid entry acquired mutation lock')), \
                    patch.object(fuel,'build_spear_plan',side_effect=AssertionError('Invalid entry reached SPEAR')):
                for value in INVALID:
                    with self.subTest(value=value), self.assertRaises(TransferFuelValidationError):
                        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
                            transfer_fuel_gallons=value,notes='Must not persist'))
            self.assertEqual(writes,[])
            self.assertIsNone(self.assignment.transfer_fuel_gallons)
            self.assertIsNone(self.work.physical_transfer_gallons)
            self.assertNotEqual(self.work.notes,'Must not persist')
        finally:
            event.remove(db.engine,'before_cursor_execute',record)

    def test_regular_and_borrowed_vendor_trucks_use_the_same_limit(self):
        self.prepare()
        for vendor_name in ('','BORROWED TEST VENDOR'):
            self.truck.vendor_driver_name=vendor_name
            self.truck.capacity_gallons=20_000
            db.session.commit()
            for raw,expected in (('',None),('0',0),('9999',9999),('17000',17000)):
                with self.subTest(vendor=vendor_name,raw=raw):
                    result=fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,transfer_fuel_gallons=raw))
                    db.session.commit()
                    self.assertEqual(self.assignment.transfer_fuel_gallons,expected)
                    self.assertEqual(result.fuel_work_state.physical_transfer_gallons,expected)
            for raw in ('17001',OVERSIZED):
                with self.assertRaises(TransferFuelValidationError):
                    fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,transfer_fuel_gallons=raw))
                self.assertEqual(self.assignment.transfer_fuel_gallons,17000)

    def test_fueler_autosave_dispatcher_and_legacy_http_reject_with_400(self):
        self.prepare()
        self.save(self.start,**self.final_data())
        baseline=json.dumps(self.baseline())
        for actor,path in ((self.fueler,f'/neoscorpion/fueler/assignments/{self.assignment.id}'),
                (self.dispatcher,f'/neoscorpion/fuel-dispatch/fueler-data/{self.assignment.id}'),
                (self.fueler,'/neoscorpion/fueler')):
            self.login(actor)
            with patch.object(fuel,'build_spear_plan',side_effect=AssertionError('Invalid entry reached SPEAR')):
                for raw in INVALID:
                    with self.subTest(path=path,raw=raw):
                        response=self.client.post(path,data=dict(assignment_id=self.assignment.id,expected=baseline,
                            transfer_fuel_gallons=raw,notes='Must not persist'),headers={'Accept':'application/json'})
                        self.assertEqual(response.status_code,400,response.get_data(as_text=True))
                        self.assertFalse(response.json['ok'])
                        self.assertIn('T/F',response.json['error'])
                        db.session.expire_all()
                        self.assertEqual(self.assignment.transfer_fuel_gallons,3000)
                        self.assertEqual(self.work.physical_transfer_gallons,3000)
                        self.assertNotEqual(self.work.notes,'Must not persist')
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_normal_save_submission_and_completion_still_post_once(self):
        self.prepare()
        self.save(self.start,**self.final_data())
        self.off();self.submit()
        result=fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id)
        db.session.commit()
        self.assertTrue(result.changed)
        self.assertEqual(NeoScorpionFuelingEvent.query.one().transfer_fuel_gallons,3000)
        self.assertEqual(self.selection.current_gallons,7000)
        self.assertFalse(fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id).changed)

    def test_quick_calculator_has_the_same_pre_calculation_limit(self):
        with patch('app.services.neoscorpion_quick_fuel.detailed_aircraft_type_for_tail',side_effect=AssertionError('Invalid T/F reached calculations')):
            for raw in INVALID:
                with self.subTest(raw=raw), self.assertRaises(TransferFuelValidationError):
                    calculate_quick_fuel(self.gateway,dict(tail_number='N491UP',transfer_fuel_gallons=raw))
        self.login(self.fueler)
        for raw in ('17001',OVERSIZED):
            response=self.client.post('/neoscorpion/quick-fuel/calculate',data=dict(tail_number='N491UP',transfer_fuel_gallons=raw))
            self.assertEqual(response.status_code,400)
        for raw,expected in (('',None),('0',0),('9999',9999),('17000',17000)):
            result=calculate_quick_fuel(self.gateway,dict(tail_number='N491UP',transfer_fuel_gallons=raw))
            self.assertEqual(result['totals']['transfer_gallons'],expected)


class TransferFuelDirectEntryTest(unittest.TestCase):
    setUp = direct_fixtures.DirectEntryTest.setUp
    login = direct_fixtures.DirectEntryTest.login
    form = direct_fixtures.DirectEntryTest.form

    def test_invalid_direct_entry_is_400_before_lock_or_persistence(self):
        self.login(self.dispatcher)
        form=self.form()
        with patch.object(direct,'_locked_scope',side_effect=AssertionError('Invalid T/F acquired mutation scope')):
            for raw in INVALID:
                with self.subTest(raw=raw):
                    response=self.client.post(f'/neoscorpion/fuel-dispatch/missions/{self.mission.id}/direct-entry',
                        data={**form,'transfer_fuel_gallons':raw})
                    self.assertEqual(response.status_code,400,response.get_data(as_text=True))
        self.assertEqual(NeoScorpionDirectEntry.query.count(),0)
        self.assertNotEqual(self.mission.fuel_status,'complete')

    def test_direct_entry_validates_and_persists_17000_without_transfer_event(self):
        record,changed=direct.complete_entry(self.gateway,self.dispatcher,self.mission.id,self.form(transfer_fuel_gallons='17000'))
        db.session.commit()
        self.assertTrue(changed)
        self.assertEqual(record.snapshot['transfer_fuel_gallons'],17000)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
