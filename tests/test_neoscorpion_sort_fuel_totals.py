"""Current sort totals share canonical calculations and the physical event ledger."""
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace
import unittest

from pypdf import PdfReader
from sqlalchemy import event as sql_event

from app.extensions import db
from app.models import (NeoScorpionFuelCycleHistory, NeoScorpionFuelingEvent,
                        NeoScorpionFuelTankState, NeoScorpionFuelWorkState, SortDateOperation)
from app.services.neoscorpion import (
    _manual_fuel_dispatch_context, _sort_fuel_totals, fuel_dispatch_context, fuel_report_context,
)
from app.services.neoscorpion_fuel_report_pdf import fuel_report_pdf
from tests import test_neoscorpion_dispatch_planning as dispatch_fixture


class SortFuelTotalsAggregationTest(unittest.TestCase):
    def test_canonical_identity_and_physical_event_identity_not_volume(self):
        first = {"mission": SimpleNamespace(id=1), "estimated_fuel_gallons": 1000,
                 "required_fuel_lbs": 25_400}
        second = {"mission": SimpleNamespace(id=2), "estimated_fuel_gallons": 2000,
                  "required_fuel_lbs": 32_100}
        events = [SimpleNamespace(id=index, transfer_fuel_gallons=500) for index in (1, 2)]
        totals = _sort_fuel_totals([first, first, second], [*events, events[0]])
        self.assertEqual(totals["estimated"]["display"], "3,000")
        self.assertEqual(totals["transfer"]["display"], "1,000")
        self.assertEqual(totals["required"]["display"], "57.5")
        self.assertEqual([totals[key]["unit"] for key in totals], ["GAL", "GAL", "K LBS"])

    def test_unknown_zero_partial_and_empty_are_distinct(self):
        def row(identifier, value):
            return {"mission": SimpleNamespace(id=identifier), "estimated_fuel_gallons": value,
                    "required_fuel_lbs": value}
        unknown = _sort_fuel_totals([row(1, None)],
                                   [SimpleNamespace(id=1, transfer_fuel_gallons=None)])
        self.assertTrue(all(stat["value"] is None and stat["display"] == "—"
                            and stat["incomplete"] for stat in unknown.values()))
        zero = _sort_fuel_totals([row(1, 0)], [SimpleNamespace(id=1, transfer_fuel_gallons=0)])
        self.assertTrue(all(stat["value"] == 0 and not stat["incomplete"] for stat in zero.values()))
        partial = _sort_fuel_totals([row(1, 0), row(2, None)],
                                   [SimpleNamespace(id=1, transfer_fuel_gallons=1200),
                                    SimpleNamespace(id=2, transfer_fuel_gallons=None)])
        self.assertEqual(partial["transfer"]["display"], "1,200*")
        self.assertEqual(partial["estimated"]["display"], "0*")
        self.assertIn("Known subtotal", partial["required"]["title"])
        self.assertTrue(all(stat["value"] == 0 and not stat["incomplete"]
                            for stat in _sort_fuel_totals([], []).values()))


class SortFuelTotalsIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = dispatch_fixture.NeoScorpionDispatchPlanningTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.gateway, self.operation = self.fixture.gateway, self.fixture.operation
        self.truck = self.fixture._nightly_truck()

    def _event(self, assignment, sequence, gallons, *, cycle=1, kind="fuel", operation=None):
        work = NeoScorpionFuelWorkState.query.filter_by(fuel_assignment_id=assignment.id).first()
        if not work:
            work = NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id,
                                            tail_number=assignment.confirmed_tail_number)
            db.session.add(work)
            db.session.flush()
        item = NeoScorpionFuelingEvent(sort_date_operation_id=(operation or self.operation).id,
            fuel_assignment_id=assignment.id, fuel_work_state_id=work.id,
            tail_number=assignment.confirmed_tail_number, fuel_truck_id=self.truck.id,
            sequence_number=sequence, cycle_number=cycle, event_type=kind,
            transfer_fuel_gallons=gallons, required_fuel_lbs=999_999)
        db.session.add(item)
        return item

    def test_all_event_types_and_cycles_count_once_without_assignment_or_history_mirrors(self):
        first = self.fixture._mission("UPS901", "N491UP", 25_400, 1)
        second = self.fixture._mission("UPS901", "N492UP", 32_100, 2)
        self.fixture._tail_fuel(first, inbound_lbs=12_000)
        tail = self.fixture._tail_fuel(second, inbound_lbs=12_000)
        tail.fob_lbs = 18_700
        a = self.fixture._assignment(first, self.truck, transfer=1_500)
        b = self.fixture._assignment(second, self.truck, transfer=800)
        b.current_cycle_number = 3
        self._event(a, 1, 1_500)
        self._event(b, 1, 500, cycle=1)
        self._event(b, 2, 500, cycle=2, kind="uplift")
        self._event(b, 3, 800, cycle=3, kind="defuel")
        self._event(b, 4, 0, cycle=3)
        db.session.add(NeoScorpionFuelCycleHistory(sort_date_operation_id=self.operation.id,
            fuel_assignment_id=b.id, mission_id=second.id, cycle_number=1, label="COMPLETED",
            snapshot={"required_fuel_lbs": 999_999, "estimated_fuel_gallons": 999_999,
                      "transfer_fuel_gallons": 999_999}))
        prior = SortDateOperation(generated_by_user_id=self.operation.generated_by_user_id,
            gateway_id=self.gateway.id, gateway_code=self.gateway.code,
            sort_date=self.operation.sort_date - timedelta(days=1), sort_name="night", window_minutes=60)
        db.session.add(prior)
        db.session.flush()
        self._event(b, 5, 999_999, operation=prior)
        db.session.commit()
        dispatch = fuel_dispatch_context(self.gateway)
        totals = dispatch["sort_fuel_totals"]
        self.assertEqual(totals["estimated"]["value"], 4000)
        self.assertEqual(totals["required"]["value"], 57_500)
        self.assertEqual(totals["transfer"]["value"], 3300)
        report = fuel_report_context(self.gateway)
        self.assertEqual(report["sort_fuel_totals"], totals)
        self.assertEqual(report["fuel_report_summary"]["event_count"], 5)
        self.assertEqual(report["fuel_report_summary"]["total_transfer_gallons"], 3300)
        self.assertEqual(len(dispatch["rows"][1]["history"]), 1)
        # Report loads current values, rather than summing per-event frozen required loads.
        self.assertEqual(report["fuel_report_rows"][0]["required_fuel_display"], "1000.0")
        pdf = PdfReader(BytesIO(fuel_report_pdf(report).getvalue()))
        text = "".join(page.extract_text() for page in pdf.pages)
        self.assertIn("TOTAL EST FUEL: 4,000 GAL", text)
        self.assertIn("TOTAL T/F: 3,300 GAL", text)
        self.assertIn("TOTAL REQUIRED FUEL: 57.5 K LBS", text)

    def test_missing_current_load_and_unclosed_assignment_do_not_become_zero_or_event(self):
        mission = self.fixture._mission("UPS900", "N490UP", None, 1)
        assignment = self.fixture._assignment(mission, self.truck, transfer=1200)
        self._event(assignment, 1, None)
        db.session.commit()
        totals = fuel_dispatch_context(self.gateway)["sort_fuel_totals"]
        self.assertTrue(all(stat["value"] is None for stat in totals.values()))
        self.assertEqual(fuel_report_context(self.gateway)["sort_fuel_totals"], totals)
        self.fixture._login_user("totals_reader", "simulator")
        for path in ("/neoscorpion/fuel-dispatch/live-panel", "/neoscorpion/reports/fuel"):
            response = self.fixture.client.get(path)
            self.assertEqual(response.status_code, 200)
            html = response.get_json()["html"] if response.is_json else response.get_data(as_text=True)
            self.assertEqual(html.count('data-total-incomplete'), 3)
            self.assertIn("— GAL", html)
        self.assertTrue(self.fixture.client.get("/neoscorpion/reports/fuel?format=pdf").data.startswith(b"%PDF-"))

    def test_report_matches_measured_remaining_zero_and_current_mission_scope(self):
        mission = self.fixture._mission("UPS901", "N491UP", 25_400, 1)
        cancelled = self.fixture._mission("UPS901", "N492UP", 999_999, 2)
        cancelled.departure_status = "cancelled"
        arrival = self.fixture._mission("UPS901", "N493UP", 999_999, 3)
        arrival.mission_type = "arrival"
        assignment = self.fixture._assignment(mission, self.truck)
        work = NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id,
                                        tail_number=mission.assigned_tail_number)
        db.session.add(work)
        db.session.flush()
        tanks = [NeoScorpionFuelTankState(fuel_work_state_id=work.id, tank_code=code, remaining_lbs=0)
                 for code in ("left", "right")]
        db.session.add_all(tanks)
        db.session.commit()
        for cycle, readings in ((1, (0, 0)), (2, (6000, 6000)), (2, (None, 6000))):
            assignment.current_cycle_number = cycle
            for tank, lbs in zip(tanks, readings):
                tank.remaining_lbs = lbs
            db.session.commit()
            totals = fuel_dispatch_context(self.gateway)["sort_fuel_totals"]
            self.assertEqual(totals["required"]["value"], 25_400)
            self.assertEqual(totals["transfer"]["value"], 0)
            self.assertEqual(fuel_report_context(self.gateway)["sort_fuel_totals"], totals)
            if readings[0] is None:
                self.assertIsNone(totals["estimated"]["value"])

    def test_empty_current_sort_has_confirmed_zero_totals_and_valid_report(self):
        db.session.commit()
        totals = fuel_dispatch_context(self.gateway)["sort_fuel_totals"]
        self.assertEqual(fuel_report_context(self.gateway)["sort_fuel_totals"], totals)
        self.assertTrue(all(stat["value"] == 0 and not stat["incomplete"] for stat in totals.values()))

    def test_live_panel_recalculates_current_values_and_physical_events(self):
        mission = self.fixture._mission("UPS901", "N491UP", 25_400, 1)
        assignment = self.fixture._assignment(mission, self.truck)
        self._event(assignment, 1, 1000)
        db.session.commit()
        self.fixture._login_user("totals_dispatch", "simulator")
        first = self.fixture.client.get("/neoscorpion/fuel-dispatch/live-panel")
        self.assertIn(b"2,000 GAL", first.data)
        self.assertIn(b"1,000 GAL", first.data)
        mission.planned_fuel_load = 32_100
        self._event(assignment, 2, 500, cycle=2, kind="uplift")
        db.session.commit()
        changed = self.fixture.client.get("/neoscorpion/fuel-dispatch/live-panel")
        self.assertIn(b"3,000 GAL", changed.data)
        self.assertIn(b"1,500 GAL", changed.data)
        self.assertIn(b"32.1 K LBS", changed.data)

    def test_dispatch_reuses_single_cycle_event_query_and_report_reads_are_batched(self):
        for index in range(2):
            mission = self.fixture._mission("UPS901", f"N49{index}UP", 25_400, index)
            self._event(self.fixture._assignment(mission, self.truck), 1, 500)
        db.session.commit()

        def reads(call):
            db.session.expire_all()
            statements = []
            def capture(_conn, _cursor, statement, *_args):
                if statement.lstrip().upper().startswith("SELECT"):
                    statements.append(statement)
            sql_event.listen(db.engine, "before_cursor_execute", capture)
            try:
                call()
            finally:
                sql_event.remove(db.engine, "before_cursor_execute", capture)
            return statements

        statements = reads(lambda: _manual_fuel_dispatch_context(self.gateway))
        ledger_reads = [s for s in statements if "FROM neoscorpion_fueling_events" in s]
        self.assertEqual(len(ledger_reads), 1)
        self.assertIn("transfer_fuel_gallons", ledger_reads[0])
        small = len(reads(lambda: fuel_report_context(self.gateway)))
        for index in range(2, 12):
            mission = self.fixture._mission("UPS901", f"N49{index}UP", 25_400, index)
            self._event(self.fixture._assignment(mission, self.truck), 1, 500)
        db.session.commit()
        self.assertEqual(len(reads(lambda: fuel_report_context(self.gateway))), small)
