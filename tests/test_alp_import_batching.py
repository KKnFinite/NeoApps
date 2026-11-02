"""ALP batch reads retain canonical authority, scoped identities and row locks."""
from datetime import date, datetime
import unittest

from app.extensions import db
from app.models import Gateway, SortDateMission, SortDateOperation, SortDateTailState
from app.services import alp_import
from tests.test_db_query_batching import sql_work
from tests import test_motherbrain_block_out_authority as authority


def departure_paste(flight="910", tail="N123UP", hour="08:00"):
    return f"11-AUG-2026\t{flight}\tLAX\t{tail}\tA1\tScheduled\t{hour} (S)"


class AlpAuthorityTest(unittest.TestCase):
    setUp = authority.MotherBrainBlockOutAuthorityTest.setUp
    tearDown = authority.MotherBrainBlockOutAuthorityTest.tearDown
    _mission = authority.MotherBrainBlockOutAuthorityTest._mission

    def test_actual_manual_writer_survives_alp_then_all_google_imports(self):
        mission = self._mission("UPS0910", "LAX")
        authority.manual_block_out(mission, self.operation, "2026-08-11 07:00")
        for _ in range(2):
            result = alp_import.apply_alp_paste(self.operation, "departure", departure_paste())
            self.assertEqual(result["applied_count"], 1)
            authority.apply_google_rain_departure_milestones(self.operation,
                rows=[dict(sheet_row=4, flight_number="UPS0910", block="2:45")])
            authority.apply_google_motherbrain_live_mission_batch(self.operation, "departure",
                [authority.primary_row(mission)], now=datetime(2026, 8, 11, 10))
            db.session.commit()
            self.assertEqual(mission.actual_block_out_datetime_utc, datetime(2026, 8, 11, 7))
            self.assertEqual(mission.actual_block_out_source, "manual")

    def test_alp_can_populate_and_revise_its_own_time_but_not_other_authorities(self):
        mission = self._mission("UPS0910", "LAX")
        for hour in ("08:00", "08:10"):
            alp_import.apply_alp_paste(self.operation, "departure", departure_paste(hour=hour))
            self.assertEqual(mission.actual_block_out_source, "alp")
        self.assertEqual(mission.actual_block_out_datetime_utc.minute, 10)
        for source in ("neo", "api", "neorain", "google_rain", "google_motherbrain"):
            mission.actual_block_out_source = source
            db.session.commit()
            alp_import.apply_alp_paste(self.operation, "departure", departure_paste())
            self.assertEqual(mission.actual_block_out_source, source)
            self.assertEqual(mission.actual_block_out_datetime_utc.minute, 10)

    def test_duplicate_rows_and_foreign_sort_cannot_modify_mission(self):
        mission = self._mission("UPS0910", "LAX")
        result = alp_import.apply_alp_paste(self.operation, "departure",
            departure_paste() + "\n" + departure_paste(tail="N456UP"))
        self.assertEqual(result["applied_count"], 0)
        self.assertIsNone(mission.actual_block_out_datetime_utc)
        other = SortDateOperation(gateway_id=self.operation.gateway_id, gateway_code="RFD",
            sort_date=date(2026, 8, 12), sort_name="night")
        db.session.add(other)
        db.session.commit()
        result = alp_import.apply_alp_paste(other, "departure", departure_paste())
        self.assertEqual(result["applied_count"], 0)
        self.assertEqual(mission.assigned_tail_number, "N123UP")

    def test_arrival_batch_query_floor_is_constant_and_tail_states_are_scoped(self):
        metrics = {}
        for count in (1, 6, 18, 50):
            operation = SortDateOperation(gateway_id=self.operation.gateway_id, gateway_code="RFD",
                sort_date=date(2026, 8, 11), sort_name=f"batch{count}")
            db.session.add(operation)
            db.session.flush()
            for i in range(count):
                db.session.add(SortDateMission(sort_date_operation_id=operation.id, gateway_code="RFD",
                    sort_date=operation.sort_date, sort_name=operation.sort_name, mission_type="arrival",
                    flight_number=f"UPS{i+1000}", origin="SDF", destination="RFD",
                    planned_datetime_utc=datetime(2026, 8, 11, 20)))
            db.session.commit()
            operation.id
            paste = "\n".join(f"11-AUG-2026\t{i+1000}\tSDF\tN{i+100}UP\tA1\tScheduled\t20:00" for i in range(count))
            with sql_work() as metric:
                result = alp_import.apply_alp_paste(operation, "arrival", paste)
            self.assertEqual(result["applied_count"], count)
            self.assertEqual(SortDateTailState.query.filter_by(sort_name=operation.sort_name).count(), count)
            metrics[count] = metric["SELECT"]
            # Initial match, refreshed locked match and existing authority-lock pass.
            self.assertEqual(sum("from sort_date_missions" in sql for sql in metric.select_sql), 3)
            self.assertEqual(metric["SELECT"], 6)
        self.assertEqual(len(set(metrics.values())), 1)
