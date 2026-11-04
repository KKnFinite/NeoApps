"""Read-only physical OFF estimates reuse SPEAR's pump component."""
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from jinja2 import Environment, FileSystemLoader

from app.services.neoscorpion import _attach_dispatch_off_estimates
from app.services.neoscorpion_spear import active_fueler_off, active_fueling_finish
from app.services.neoscorpion_spear_forecasts import physical_off_forecast
from tests import test_neoscorpion_spear as fixtures
from tests import test_neoscorpion_fueling_status as http_fixtures
from tests.html_contracts import document
from app.extensions import db
from app.models import NeoScorpionFuelingEvent, NeoScorpionSortTruck
from app.services import neoscorpion as service


NOW = datetime(2026, 10, 14, 4, 34)


class OffProjectionTest(unittest.TestCase):
    def row(self, *, demand=1200, aircraft="B757"):
        row = fixtures._row(demand=demand)
        row.update(dispatch_status_key="fueling", dispatch_status_label="FUELING",
            # A complete read-only trusted v3 projection, not a physical mutation.
            # Canonical v4 evidence/participation is exercised by the HTTP integration below.
            assignment=SimpleNamespace(id=1,current_cycle_number=1,assigned_fueler_user_id=1,assigned_truck_id=10,
                transfer_fuel_gallons=None),
            fuel_work_state=SimpleNamespace(id=1,on_at_utc=NOW,off_at_utc=None,ended_early_at_utc=None,
                performance_context=dict(version=3,pump_rate='200' if aircraft=='A300' else '100',setup_allowance='0')),
            fueling_effective_on_utc=NOW, fueling_on_trusted=True,
            detailed_aircraft_type=aircraft, fuel_status_secondary="", cycle_type="fuel")
        return row

    def planning(self, **rates):
        planning = fixtures._PlanningSettings()
        planning.pump_rates_gallons_per_minute = {"B757": Decimal(100), "A300": Decimal(200), **rates}
        planning.pump_rate_for = lambda kind: planning.pump_rates_gallons_per_minute.get(kind)
        return planning

    def attach(self, row, *, mode="observe", profiles=None, calibrations=None, now=NOW):
        context = dict(rows=[row], operation=SimpleNamespace(id=1),
            spear_settings=SimpleNamespace(live_calibration_mode=mode), fueling_performance=profiles or {})
        row['physical_off_forecast'] = physical_off_forecast(row, self.planning(), mode=mode)
        with patch("app.services.neoscorpion.gateway_timezone", return_value="America/Chicago"):
            _attach_dispatch_off_estimates(context, SimpleNamespace(id=1), self.planning(),
                                          calibrations or {}, now_utc=now)
        return row

    def test_physical_off_excludes_pre_on_setup_and_post_off_completion(self):
        row = self.row()
        off = active_fueler_off(row, SimpleNamespace(id=1), self.planning())
        self.assertEqual(off, NOW+timedelta(minutes=12))
        complete = active_fueling_finish(row, SimpleNamespace(id=1), self.planning())
        self.assertEqual(complete, off)  # One shared physical OFF forecast; no post-OFF wrap-up.
        before = dict(row)
        self.attach(row)
        self.assertEqual(row["fueler_off_estimate_display"], "EST OFF 23:46 (in 12m) 0m PROF · BASELINE")
        self.assertEqual(row["fueler_off_estimate_utc"], "2026-10-14T04:46:00Z")
        for key, value in before.items():
            self.assertEqual(row[key], value)

    def test_different_aircraft_rates_and_missing_rate_or_demand(self):
        for aircraft, minutes in (("B757", 12), ("A300", 6)):
            self.assertEqual(active_fueler_off(self.row(aircraft=aircraft), SimpleNamespace(), self.planning()),
                             NOW+timedelta(minutes=minutes))
        for rate in (None, Decimal(0), Decimal(-1)):
            row = self.row()
            row['fuel_work_state'].performance_context['pump_rate'] = rate
            self.assertIsNone(active_fueler_off(row, SimpleNamespace(), self.planning()))
        # Today's missing/different rate cannot rewrite a retained valid snapshot.
        self.assertEqual(active_fueler_off(self.row(), SimpleNamespace(), self.planning(B757=None)), NOW+timedelta(minutes=12))
        self.assertIsNone(active_fueler_off(self.row(demand=None), SimpleNamespace(), self.planning()))

    def test_recorded_on_anchors_physical_timing_without_etd_or_arrival_timestamp(self):
        row = self.row()
        row["mission"].planned_datetime_utc = None
        self.assertEqual(active_fueler_off(row, SimpleNamespace(), self.planning()), NOW+timedelta(minutes=12))
        row["arrival_mission"] = None
        self.assertEqual(active_fueler_off(row, SimpleNamespace(), self.planning()), NOW+timedelta(minutes=12))
        row['fueling_effective_on_utc'] = None
        row['truck_start_reason'] = 'TRUCK LOCATION UNKNOWN'
        row['fueling_on_trusted'] = False
        self.assertIsNone(active_fueler_off(row, SimpleNamespace(), self.planning()))
        self.attach(row)
        self.assertEqual(row["fueler_off_estimate_display"], "TIMING UNKNOWN")

    def test_observe_apply_aircraft_calibration_and_profiles_do_not_multiply_adjustments(self):
        calibrated = {("pump_rate", "B757"): SimpleNamespace(active=True, effective=Decimal(200))}
        for mode, profiles in (
            ("observe", {}), ("apply", {}),
            ("observe", {"fuelers": [{"id":1,"jobs":3,"prof":50}]}),
            ("apply", {"fuelers": [{"id":1,"jobs":2,"prof":50}]}),
            ("apply", {"fuelers": [{"id":1,"jobs":3,"prof":None}]}),
            ("apply", {"fuelers": [{"id":1,"jobs":3,"prof":0}]}),
            ("apply", {"fuelers": [{"id":1,"jobs":3,"prof":50}]}),
            ("apply", {"trucks": [{"id":10,"jobs":3,"prof":50}]}),
            ("apply", {"fuelers": [{"id":1,"jobs":3,"prof":200}],
                       "trucks": [{"id":10,"jobs":3,"prof":50}]}),
        ):
            with self.subTest(mode=mode, profiles=profiles):
                row = self.attach(self.row(), mode=mode, profiles=profiles, calibrations=calibrated)
                # Aggregate legacy profile counts and today's calibration do not
                # replace retained policy or qualified event-level service history.
                self.assertEqual(row['fueler_off_estimate_utc'], (NOW+timedelta(minutes=12)).isoformat()+'Z')
                self.assertEqual(row['physical_off_forecast']['samples'], 0)
                self.assertEqual(row['physical_off_forecast']['source'], 'BASELINE')

    def test_overdue_zero_valid_unknown_and_off_remove_projection(self):
        row = self.attach(self.row(), now=NOW+timedelta(minutes=12))
        self.assertIn("overdue 0m", row["fueler_off_estimate_display"])
        self.assertNotIn("-", row["fueler_off_estimate_display"])
        self.assertEqual(self.attach(self.row(demand=0))["fueler_off_estimate_display"], "TIMING UNKNOWN")
        row["fuel_work_state"].off_at_utc = NOW+timedelta(minutes=12)
        row["dispatch_status_key"] = "off"
        self.assertIsNone(physical_off_forecast(row, self.planning())["likely"])
        self.attach(row)
        self.assertEqual(row["fueler_off_estimate_utc"], "")

    def test_template_only_dispatch_fueling_one_line_warning_priority_and_actual_off(self):
        env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[1]/"app/templates"))
        template = env.from_string('{% from "neonodes/neoscorpion/_fuel_status.html" import fuel_status %}{{ fuel_status(row, dispatcher=dispatcher, can_ack=true) }}')
        row = self.attach(self.row())
        html = template.render(row=row, dispatcher=True)
        self.assertIn("EST OFF 23:46 (in 12m) 0m PROF · BASELINE", html)
        self.assertEqual(html.count("data-fuel-status-secondary"), 1)
        self.assertNotIn("data-fueler-off-estimate", template.render(row=row, dispatcher=False))
        for warning in ("Fuel direction discrepancy", "Dispatcher review requested", "FOB CHECK", "FOB LIKELY"):
            row["fuel_status_secondary"] = warning
            self.assertNotIn("data-fueler-off-estimate", template.render(row=row, dispatcher=True))
        row["call_dispatch_alert"] = True
        self.assertIn("CALL DISPATCH", template.render(row=row, dispatcher=True))
        self.assertNotIn("data-fueler-off-estimate", template.render(row=row, dispatcher=True))
        row.update(call_dispatch_alert=None, fuel_status_secondary="", dispatch_status_key="off", off_time="23:46")
        html = template.render(row=row, dispatcher=True)
        self.assertIn("TRUCK OFF", html)
        self.assertIn("23:46", html)
        self.assertNotIn("data-fueler-off-estimate", html)
        self.assertNotIn("OFF 23:46", template.render(row=row, dispatcher=False))


class OffProjectionHttpTest(unittest.TestCase):
    setUp = http_fixtures.FuelingStatusIntegrationTest.setUp
    login = http_fixtures.FuelingStatusIntegrationTest.login
    work = http_fixtures.FuelingStatusIntegrationTest.work
    row = http_fixtures.FuelingStatusIntegrationTest.row

    def test_live_initial_unknown_off_and_canonical_performance_apply(self):
        from tests.neoscorpion_evidence_fixture import FuelCycle,transfer_readings
        from app.services.neoscorpion_prof import load_observation_jobs
        import copy
        truck=self.fixture._nightly_truck()
        for index,tail in enumerate(('N492UP','N493UP','N494UP')):
            start=datetime(2026,8,19,index,1)
            cycle=FuelCycle.create(self.gateway,self.operation,self.fueler,self.dispatcher,truck,
                flight=f'UPS-H{index}',tail=tail)
            cycle.park(start-timedelta(minutes=1),'A',f'A0{index+1}')
            cycle.record_arrival(start-timedelta(minutes=2))
            cycle.assign(start-timedelta(minutes=1))
            cycle.finish(start,start+timedelta(minutes=30),**transfer_readings(gallons='1500'))
        current=FuelCycle(self.gateway,self.operation,self.mission,self.fueler,self.dispatcher,truck,assignment=self.assignment)
        start=datetime(2026,8,19,3,1)
        current.assign(start-timedelta(minutes=1));current.on(start)
        current.save(start+timedelta(minutes=1),apu_running='no',remaining_left='6',remaining_ctr='0',remaining_right='6')
        work=current.work
        self.settings.spear_live_calibration_mode='apply';db.session.commit()
        snapshots=[copy.deepcopy(e.performance_snapshot) for e in NeoScorpionFuelingEvent.query.order_by(NeoScorpionFuelingEvent.id)]
        context=service.fuel_dispatch_context(self.gateway)
        profile=next(p for p in context['fueling_performance']['fuelers'] if p['id']==self.fueler.id)
        self.assertEqual(profile['jobs'],3)
        self.assertIsNotNone(profile['prof'])
        row=next(r for r in context['rows'] if r['mission'].id==self.mission.id)
        forecast=row['physical_off_forecast']
        self.assertEqual(forecast['samples'],3)
        self.assertEqual(forecast['source'],'QUALIFIED SERVICE HISTORY')
        self.assertGreater(forecast['likely'],forecast['goal'])
        self.assertEqual(datetime.fromisoformat(row['fueler_off_estimate_utc'].removesuffix('Z')),forecast['likely'])
        jobs=load_observation_jobs(self.gateway.id,operation_id=self.operation.id)
        independent=physical_off_forecast(row,service.assignment_planning_settings(self.gateway,settings=self.settings),jobs=jobs,mode='apply')
        self.assertEqual(forecast['likely'],independent['likely'])
        self.assertEqual(active_fueling_finish(row,self.operation,service.assignment_planning_settings(self.gateway,settings=self.settings)),forecast['likely'])
        self.settings.spear_live_calibration_mode='observe';db.session.commit()
        observe=self.row()
        self.assertEqual(observe['physical_off_forecast']['likely'],observe['physical_off_forecast']['goal'])
        self.assertNotEqual(observe['fueler_off_estimate_utc'],row['fueler_off_estimate_utc'])
        self.assertIsNone(self.assignment.transfer_fuel_gallons)
        self.assertIsNone(self.assignment.completed_at_utc);self.assertIsNone(work.off_at_utc)
        self.login(self.dispatcher)
        response=self.client.get('/neoscorpion/fuel-dispatch')
        active=document(response).one(**{'data-dispatch-mission-id':str(self.mission.id)})
        self.assertEqual(len(active.findall('td')),16)
        self.assertEqual(active.one(**{'data-fueler-off-estimate':None}).attrs['data-fueler-off-estimate'],observe['fueler_off_estimate_utc'])
        live=self.client.get('/neoscorpion/fuel-dispatch/live-panel')
        self.assertEqual(live.status_code,200);self.assertIn(observe['fueler_off_estimate_utc'],live.json['html'])
        # Removing current demand produces uncertainty, never a fabricated estimate.
        self.mission.planned_fuel_load=None;db.session.commit()
        unknown=next(r for r in service.fuel_dispatch_context(self.gateway)['rows'] if r['mission'].id==self.mission.id)
        self.assertIsNone(unknown['physical_off_forecast']['likely'])
        current.off(start+timedelta(minutes=25))
        off=document(self.client.get('/neoscorpion/fuel-dispatch')).one(**{'data-dispatch-mission-id':str(self.mission.id)})
        self.assertFalse(off.findall(**{'data-fueler-off-estimate':None}))
        self.assertIn('TRUCK OFF',off.text)
        self.assertEqual([e.performance_snapshot for e in NeoScorpionFuelingEvent.query.order_by(NeoScorpionFuelingEvent.id)],snapshots)
        self.assertEqual(NeoScorpionSortTruck.query.one().current_gallons,5500)
