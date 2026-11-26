"""Exact Master Schedule identity from lineup through canonical pull writes."""
from datetime import datetime, time
import unittest
from unittest.mock import patch
from flask import g
from app.extensions import db
from app.models import MasterFlightSchedule, NeoErmacBuildingLineup, NeoErmacDoorPull, SortDateMission
from app.services.neoermac_building_lineup import (get_building_lineup_rows, lineup_field_name,
    lineup_original, slot_flight_value, save_building_lineup_destination, save_building_lineup,
    building_lineup_state_payload, backfill_lineup_flight_identities, LineupConflict)
from app.services.neoermac_door_view import door_view_operational_state, _destination_cards_for_door, save_single_door_pull, DoorPullConflict
from app.services.neoermac_dashboard import neoermac_dashboard_context
from app.services.neoermac_upcoming_pulls import fill_upcoming_pull
from tests import test_neoermac_routes as fixtures


class FlightIdentityTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.NeoErmacRoutesTest(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.gateway = self.f.gateway
        self.a = self.add_flight('UPS101', 'N101UP', 'A01', time(23, 40), time(0, 10))
        self.b = self.add_flight('UPS202', 'N202UP', 'B06', time(0, 20), time(0, 40))
        self.operation = self.a.sort_date_operation
        rows = get_building_lineup_rows(self.gateway)
        self.row = next(row for row in rows if row.runout_key == 'runout_9')
        self.field = 'west_destination_1'  # D32
        self.assign(self.b)
        self.f._login_approved_user(role='grandmaster')
        db.session.commit()

    def add_flight(self, number, tail, parking, pure, mix):
        master = MasterFlightSchedule(gateway_id=self.gateway.id, gateway_code=self.gateway.code,
            sort_name='night', mission_type='departure', flight_number=number, origin=self.gateway.code,
            destination='LAX', active=True, active_days='monday,tuesday,wednesday,thursday,friday,saturday,sunday',
            planned_time_local=time(2), pure_pull_time_local=pure, mix_pull_time_local=mix)
        db.session.add(master); db.session.flush()
        mission = self.f._add_operation_departure(number, 'LAX', tail=tail, parking=parking,
            pure_pull_time_local=pure, mix_pull_time_local=mix)
        mission.master_flight_schedule_id = master.id
        db.session.add(SortDateMission(sort_date_operation_id=mission.sort_date_operation_id,
            sort_date=mission.sort_date, gateway_code=self.gateway.code, sort_name='night',
            mission_type='arrival', mission_source='manual', flight_number='ARR'+number,
            origin='LAX', destination=self.gateway.code, assigned_tail_number=tail,
            arrival_status='on_ground', actual_block_in_datetime_utc=datetime(2026,6,11,5)))
        db.session.flush()
        return mission

    def assign(self, mission, row=None, field=None):
        row, field = row or self.row, field or self.field
        token = lineup_field_name(row, field)
        return save_building_lineup_destination(self.gateway, token, str(mission.master_flight_schedule_id),
            expected_original=lineup_original(self.gateway, token, slot_flight_value(row, field)))

    def bundle(self):
        return door_view_operational_state(self.gateway, operation=self.operation, initialize_lineup=False)

    def cards(self, door='D32'):
        return _destination_cards_for_door(self.gateway, door, self.operation, bundle=self.bundle())

    def write(self, card, key='pure', value='00:25'):
        return save_single_door_pull(self.gateway, 'D32', 'LAX', key, value, False,
            expected_operation_id=self.operation.id, expected_mission_id=card['mission_id'],
            expected_original=card['original'][key])

    def test_two_lax_choices_exact_tail_parking_times_and_no_advance(self):
        state = building_lineup_state_payload(self.gateway, operation=self.operation)
        self.assertEqual([c['label'] for c in state['destination_choices']], ['UPS101 \u2014 1st LAX','UPS202 \u2014 2nd LAX'])
        self.assertEqual(state['pull_times'][str(self.b.master_flight_schedule_id)],dict(pure='00:20',mix='00:40'))
        card = self.cards()[0]
        self.assertEqual((card['mission_id'],card['flight_number'],card['tail'],card['parking']),
            (self.b.id,'UPS202','N202UP','B06'))
        self.assertEqual(card['planned'],dict(pure='00:20',mix='00:40'))
        for status in ('last_uld_enroute','departed'):
            self.a.departure_status=status;db.session.flush()
            self.assertEqual(self.cards()[0]['mission_id'],self.b.id)
        self.b.departure_status='departed';db.session.flush()
        self.assertEqual(self.cards()[0]['mission_id'],self.b.id)

    def test_pull_save_never_updates_unassigned_flight(self):
        self.write(self.cards()[0]);db.session.flush()
        self.assertEqual(NeoErmacDoorPull.query.one().sort_date_mission_id,self.b.id)
        self.assertEqual(self.b.actual_pure_pull_time_local,time(0,25))
        self.assertIsNone(self.a.actual_pure_pull_time_local)
        self.assertEqual(self.cards()[0]['actual']['pure'],'00:25')

    def test_upcoming_only_assigned_incomplete_and_gap_fill_preserved(self):
        other = next(r for r in get_building_lineup_rows(self.gateway) if r.runout_key=='runout_10')
        self.assign(self.b,other,'east_destination_1')  # same D32, deduplicated
        board = neoermac_dashboard_context(self.gateway, operation=self.operation)
        self.assertEqual({r['mission_id'] for r in board['both']},{self.b.id})
        with patch('app.services.neoermac_upcoming_pulls.locked_door_pull_operation',return_value=self.operation):
            self.assertEqual(fill_upcoming_pull(self.gateway,operation_id=self.operation.id,mission_id=self.b.id,
                destination='LAX',pull_key='pure',actual_pull='00:26'),1)
            self.assertEqual(fill_upcoming_pull(self.gateway,operation_id=self.operation.id,mission_id=self.b.id,
                destination='LAX',pull_key='pure',actual_pull='00:29'),0)
            with self.assertRaises(DoorPullConflict):
                fill_upcoming_pull(self.gateway,operation_id=self.operation.id,mission_id=self.a.id,
                    destination='LAX',pull_key='pure',actual_pull='00:29')
        board=neoermac_dashboard_context(self.gateway,operation=self.operation)
        self.assertEqual([r['pull_key'] for r in board['both']],['mix'])
        self.assertIsNone(self.a.actual_pure_pull_time_local)

    def test_repeated_destinations_same_door_and_other_doors_remain_independent(self):
        self.assign(self.a,self.row,'west_destination_2')
        cards={c['mission_id']:c for c in self.cards()}
        self.assertEqual(set(cards),{self.a.id,self.b.id})
        self.write(cards[self.b.id]);db.session.flush()
        self.assertIsNone(self.a.actual_pure_pull_time_local)
        self.assertEqual({c['mission_id']:c['actual']['pure'] for c in self.cards()},
                         {self.a.id:'',self.b.id:'00:25'})

    def test_same_destination_labels_alerts_and_outbound_assignments_use_mission_identity(self):
        from app.services.neoermac_view_outbound import view_outbound_context
        self.a.destination = self.b.destination = "EWR"
        self.a.master_flight_schedule.destination = self.b.master_flight_schedule.destination = "EWR"
        self.a.planned_datetime_utc = datetime(2026, 6, 11, 4, 50)
        self.b.planned_datetime_utc = datetime(2026, 6, 11, 5, 15)
        self.a.pure_pull_time_local = self.b.pure_pull_time_local = time(0, 20)
        self.assign(self.a, self.row, "west_destination_2")
        self.assign(self.a, self.row, "east_destination_1")
        db.session.commit()
        cards = {card["mission_id"]: card for card in self.cards()}
        self.assertEqual([cards[m.id]["destination_label"] for m in (self.a, self.b)], ["1st EWR", "2nd EWR"])
        keys = [cards[m.id]["pull_alerts"]["pure"]["key"] for m in (self.a, self.b)]
        self.assertTrue(all(keys))
        self.assertNotEqual(*keys)
        rows = {row["mission_id"]: row for row in view_outbound_context(self.gateway, operation=self.operation)["rows"]}
        self.assertEqual([rows[m.id]["destination"] for m in (self.a, self.b)], ["1st EWR", "2nd EWR"])
        self.assertNotEqual(rows[self.a.id]["assigned_doors"], rows[self.b.id]["assigned_doors"])

    def test_tied_and_retimed_departures_keep_their_flight_identity(self):
        from app.services.departure_labels import departure_occurrences, destination_label
        third = self.add_flight("UPS303", "N303UP", "C03", time(1), time(1, 20))
        departures = [third, self.b, self.a]
        for mission in departures:
            mission.eta_datetime_utc = datetime(2026, 6, 11, 6, 10)
        def labels():
            ranks = departure_occurrences(departures, self.operation)
            return {m.flight_number: destination_label(m.destination, ranks.get(m.id)) for m in departures}
        self.assertEqual(labels(), {"UPS101": "1st LAX", "UPS202": "2nd LAX", "UPS303": "3rd LAX"})
        third.eta_datetime_utc = datetime(2026, 6, 11, 5, 59)
        self.assertEqual(labels(), {"UPS303": "1st LAX", "UPS101": "2nd LAX", "UPS202": "3rd LAX"})
        self.assertEqual({m.destination for m in departures}, {"LAX"})

    def test_stale_pull_and_stale_lineup_cannot_rebind_to_other_lax_flight(self):
        card=self.cards()[0]
        token=lineup_field_name(self.row,self.field)
        old=lineup_original(self.gateway,token,slot_flight_value(self.row,self.field))
        self.assign(self.a)
        with self.assertRaises(DoorPullConflict):self.write(card)
        with self.assertRaises(LineupConflict):
            save_building_lineup_destination(self.gateway,token,str(self.b.master_flight_schedule_id),expected_original=old)
        self.assertEqual(NeoErmacDoorPull.query.count(),0)

    def test_missing_current_mission_does_not_substitute_same_destination(self):
        self.b.master_flight_schedule_id=None;db.session.flush()
        card=self.cards()[0]
        self.assertIsNone(card['mission_id']);self.assertEqual(card['flight_number'],'UPS202')
        self.assertEqual(card['actual'],dict(pure='',mix=''))
        self.assertEqual(neoermac_dashboard_context(self.gateway,operation=self.operation)['both'],[])

    def test_legacy_backfill_unambiguous_once_ambiguous_requires_selection(self):
        self.row.flight_assignments=None;self.row.west_destination_1='LAX'
        unique=MasterFlightSchedule(gateway_id=self.gateway.id,gateway_code=self.gateway.code,sort_name='night',
            mission_type='departure',flight_number='UPS333',origin=self.gateway.code,destination='OAK',
            active=True,active_days='monday',planned_time_local=time(2))
        db.session.add(unique);db.session.flush();self.row.east_destination_1='OAK';db.session.flush()
        backfill_lineup_flight_identities()
        self.assertIsNone(self.row.flight_assignments['west_destination_1'])
        self.assertEqual(self.row.flight_assignments['east_destination_1'],unique.id)
        self.a.master_flight_schedule.active=False;db.session.flush()
        backfill_lineup_flight_identities()
        self.assertIsNone(self.row.flight_assignments['west_destination_1'])
        self.assertEqual(self.cards(),[])
        self.assertEqual(self.row.west_destination_1,'LAX')

    def test_retired_master_keeps_selected_identity_and_rejects_new_selection(self):
        self.b.master_flight_schedule.active=False;db.session.flush()
        self.assertEqual(self.cards()[0]['mission_id'],self.b.id)
        state=building_lineup_state_payload(self.gateway,operation=self.operation)
        self.assertNotIn(str(self.b.master_flight_schedule_id),[c['value'] for c in state['destination_choices']])
        self.assign(self.a)
        with self.assertRaises(ValueError):self.assign(self.b)

    def test_linked_doors_require_same_flight_not_just_destination(self):
        from app.services.neoermac_building_lineup import get_linked_building_lineup_doors
        self.assign(self.a,self.row,'east_destination_1')  # D29 opposite D32
        self.assertEqual(get_linked_building_lineup_doors(self.gateway,'D32','LAX',
            master_flight_schedule_id=self.b.master_flight_schedule_id),())
        self.assign(self.b,self.row,'east_destination_1')
        self.assertEqual(get_linked_building_lineup_doors(self.gateway,'D32','LAX',
            master_flight_schedule_id=self.b.master_flight_schedule_id),('D29',))

    def test_render_autosave_full_save_live_state_and_midnight(self):
        client=self.f.client
        html=client.get('/neoermac/building-lineup').get_data(as_text=True)
        self.assertIn('UPS101',html);self.assertIn('UPS202',html)
        self.assertIn('value="'+str(self.b.master_flight_schedule_id)+'" selected',html)
        self.assertEqual(client.get('/neoermac/door-view?door=D32').status_code,200)
        token=lineup_field_name(self.row,self.field)
        original=lineup_original(self.gateway,token,slot_flight_value(self.row,self.field))
        response=client.post('/neoermac/building-lineup/destination',data=dict(field=token,
            destination=str(self.a.master_flight_schedule_id),original=original))
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        save_building_lineup(self.gateway,{token:str(self.b.master_flight_schedule_id),
            'original_'+token:lineup_original(self.gateway,token,str(self.a.master_flight_schedule_id))})
        db.session.commit();g.__dict__.clear()
        response=client.get('/neoermac/door-view/state?door=D32').json
        self.assertEqual(response['state']['destinations'][0]['mission_id'],self.b.id)
        from app.services.night_sorting import sort_datetime_for_local_time
        self.assertGreater(sort_datetime_for_local_time(self.operation.sort_date,'night',time(0,20)),
                           sort_datetime_for_local_time(self.operation.sort_date,'night',time(23,40)))

    def test_duplicate_current_missions_for_one_master_are_not_guessed(self):
        extra=self.f._add_operation_departure('UPS202','LAX',tail='N303UP')
        extra.master_flight_schedule_id=self.b.master_flight_schedule_id;db.session.flush()
        self.assertIsNone(self.cards()[0]['mission_id'])
        self.assertEqual(neoermac_dashboard_context(self.gateway,operation=self.operation)['both'],[])

    def test_retirement_preserves_pull_history_through_lineup_changes(self):
        from app.services.neoermac_pull_aggregation import recompute_current_sort_door_pull_aggregates
        self.write(self.cards()[0])
        self.b.master_retirement = {"status": "scheduled"}
        self.b.departure_status = "cancelled"
        db.session.flush()
        self.assign(self.a, self.row, "west_destination_2")
        results = recompute_current_sort_door_pull_aggregates(self.gateway, operation=self.operation)
        self.assertNotIn(self.b.id, results)
        self.assertEqual(self.b.actual_pure_pull_time_local, time(0, 25))
        self.assertEqual(NeoErmacDoorPull.query.one().sort_date_mission_id, self.b.id)

    def test_same_destination_on_separate_doors_has_independent_requirements(self):
        other=next(r for r in get_building_lineup_rows(self.gateway) if r.runout_key=='green_runout')
        self.assign(self.a,other,'east_destination_1')
        self.write(self.cards()[0]);db.session.flush()
        self.assertEqual(self.cards('D1')[0]['mission_id'],self.a.id)
        self.assertEqual(self.cards('D1')[0]['actual']['pure'],'')
        board=neoermac_dashboard_context(self.gateway,operation=self.operation)
        self.assertEqual({r['mission_id'] for r in board['east']},{self.a.id})
        self.assertEqual({r['mission_id'] for r in board['west']},{self.b.id})

    def test_view_outbound_retains_unassigned_missions_and_history_is_untouched(self):
        self.a.actual_pure_pull_time_local=time(23,55);db.session.flush()
        self.assign(self.b);db.session.flush()
        self.assertEqual(self.a.actual_pure_pull_time_local,time(23,55))
        response=self.f.client.get('/neoermac/view-outbound')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'UPS101',response.data);self.assertIn(b'UPS202',response.data)

    def test_outbound_completion_matches_raiden_after_door_changes(self):
        from app.neonodes.neoraiden.ermac import completed_pulls
        from app.services.neoermac_view_outbound import view_outbound_context
        self.f.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime.combine(
            self.operation.sort_date, time(22),
        )
        self.assign(self.b, self.row, "east_destination_1")  # D29, also assigned to UPS202.
        self.assign(self.a, self.row, "west_destination_2")  # D32, shared with UPS202.

        def record(door, mission=None, **evidence):
            row = NeoErmacDoorPull(
                gateway_id=self.gateway.id, sort_date_operation_id=self.operation.id,
                sort_date_mission_id=mission.id if mission else None, door=door,
                destination="LAX", **evidence,
            )
            db.session.add(row)
            return row

        def check(first, second, extra=None):
            db.session.flush()
            departures = [self.a, self.b] if extra is None else [self.a, self.b, extra]
            _, raiden = completed_pulls(self.gateway, self.operation, departures)
            ermac = {
                row["mission_id"]: tuple(
                    "NO " + key.upper() if row["no_pulls"][key] else row["actual_pulls"][key] or None
                    for key in ("pure", "mix")
                )
                for row in view_outbound_context(self.gateway, operation=self.operation)["rows"]
            }
            for mission, expected in ((self.a, first), (self.b, second)):
                self.assertEqual(ermac[mission.id], expected)
                self.assertEqual(raiden.get(mission.id, (None, None)), expected)

        west = record("D32", self.b, actual_pure_pull_time_local=time(23, 59), no_mix_pull=True)
        record("D29", actual_pure_pull_time_local=time(0, 45), no_mix_pull=True)
        self.b.actual_pure_pull_time_local = time(23, 59)
        check((None, None), (None, None))

        east = record("D29", self.b, actual_pure_pull_time_local=time(0, 4))
        check((None, None), ("00:04", None))
        east.no_mix_pull = True
        check((None, None), ("00:04", "NO MIX"))
        east.no_pure_pull = True  # Explicit No Pull supersedes even a stale actual on that record.
        check((None, None), ("23:59", "NO MIX"))
        west.no_pure_pull = True
        check((None, None), ("NO PURE", "NO MIX"))
        west.no_pure_pull = False
        west.actual_pure_pull_time_local = None
        check((None, None), (None, "NO MIX"))
        west.no_mix_pull = False
        west.actual_mix_pull_time_local = time(0, 20)
        check((None, None), (None, "00:20"))

        east.actual_pure_pull_time_local = time(0, 5)
        east.no_pure_pull = False
        west.actual_pure_pull_time_local = time(23, 58)
        record("D32", self.a, actual_pure_pull_time_local=time(23, 12), no_mix_pull=True)
        check(("23:12", "NO MIX"), ("00:05", "00:20"))

        self.assign(self.a, self.row, "east_destination_1")  # A previous result must become incomplete.
        check((None, None), ("23:58", "00:20"))
        self.row.east_destination_2 = "LAX"  # Legacy slot without authoritative mission identity.
        check((None, None), (None, None))
        self.row.east_destination_2 = None

        extra = self.f._add_operation_departure("UPS202", "LAX", tail="N303UP")
        extra.master_flight_schedule_id = self.b.master_flight_schedule_id
        check((None, None), (None, None), extra)

    def test_unique_legacy_evidence_never_overrides_canonical_door_record(self):
        from app.services.neoermac_view_outbound import view_outbound_context

        self.f.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime.combine(
            self.operation.sort_date, time(22),
        )
        self.a.destination = "OAK"
        for mission_id, actual, updated in ((self.b.id, time(0, 15), 1), (None, time(0, 30), 2)):
            db.session.add(NeoErmacDoorPull(
                gateway_id=self.gateway.id, sort_date_operation_id=self.operation.id,
                sort_date_mission_id=mission_id, door="D32", destination="LAX",
                actual_pure_pull_time_local=actual, updated_at=datetime(2026, 6, 11, updated),
            ))
        db.session.flush()

        def actual():
            rows = view_outbound_context(self.gateway, operation=self.operation)["rows"]
            return next(row for row in rows if row["mission_id"] == self.b.id)["actual_pulls"]["pure"]

        self.assertEqual(actual(), "00:15")
        db.session.delete(NeoErmacDoorPull.query.filter_by(sort_date_mission_id=self.b.id).one())
        db.session.flush()
        self.assertEqual(actual(), "00:30")
        self.a.destination = "LAX"
        self.a.master_retirement = {"status": "scheduled"}
        db.session.flush()
        self.assertEqual(actual(), "")

    def test_outbound_rejects_mutable_yesterday_roster_without_a_physical_window(self):
        import re
        from datetime import timedelta
        from tests.test_neorain_sort_refresh import configure_sort_window

        setting, opening = configure_sort_window(self.gateway, self.operation.sort_date)
        self.f.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = opening + timedelta(hours=4)
        self.write(self.cards()[0])
        db.session.commit()
        page = self.f.client.get("/neoermac/view-outbound")
        self.assertIn(b'class="neoermac-outbound-actual is-pull-on-time">00:25', page.data)
        revision = re.search(rb'data-outbound-revision="([a-f0-9]+)"', page.data).group(1).decode()

        setting.sort_window_start_local = None  # Planning still keeps the operation in scope.
        db.session.commit()
        response = self.f.client.get(f"/neoermac/view-outbound/state?revision={revision}")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["changed"])
        self.assertIn("UPS202", response.json["content_html"])  # The lifecycle is still open.
        self.assertNotIn("00:25", response.json["content_html"])
        self.assertEqual(NeoErmacDoorPull.query.one().actual_pure_pull_time_local, time(0, 25))
