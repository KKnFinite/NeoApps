"""Dispatch-only ordering; operational enrichment retains its original input."""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as Item
from unittest.mock import patch

from app.neonodes.neoscorpion.routes import _dispatch_presentation_context, _dispatch_presentation_key
from tests import test_neoscorpion_operational_timeline as timeline
from app.services import neoscorpion as service
from app.extensions import db


def row(identifier, arrival=None, *, status="assigned", present="Scheduled", etd=None, done=None):
    return dict(mission=Item(id=identifier, flight_number=f"UPS{identifier}", eta_datetime_utc=None,
        planned_datetime_utc=etd, fuel_completed_at_utc=None), arrival_mission=arrival,
        assignment=Item(completed_at_utc=done, fuel_on_board_at_utc=done if status=="fob" else None),
        dispatch_status_key=status, arrival_status=present)


class DispatchOrderTest(unittest.TestCase):
    def ordered(self, rows):
        return [r["mission"].id for r in sorted(rows, key=_dispatch_presentation_key)]

    def test_completed_and_fob_last_by_etd_not_completion_time(self):
        at=datetime(2026,10,14,4)
        rows=[row(1,status="complete",done=at,etd=at+timedelta(hours=2)),row(2),
              row(3,status="fob",done=at+timedelta(minutes=1),etd=at),row(4,status="complete",done=at)]
        self.assertEqual(self.ordered(rows),[2,3,1,4])

    def test_etd_mode_midnight_missing_and_completed_last(self):
        at=datetime(2026,10,13,23,55)
        rows=[row(1,etd=at+timedelta(minutes=10)),row(2,etd=at),row(3),
              row(4,status="fob",etd=at-timedelta(hours=1)),row(5,status="complete")]
        self.assertEqual([r["mission"].id for r in sorted(rows,key=lambda r:_dispatch_presentation_key(r,"etd"))],
                         [2,1,3,4,5])

    def test_actual_then_eta_then_schedule_and_midnight_not_etd(self):
        at=datetime(2026,10,13,23,55)
        arrivals=[Item(actual_block_in_datetime_utc=at,eta_datetime_utc=at+timedelta(hours=3),planned_datetime_utc=at),
                  Item(actual_block_in_datetime_utc=None,eta_datetime_utc=at+timedelta(minutes=10),planned_datetime_utc=at-timedelta(hours=3)),
                  Item(actual_block_in_datetime_utc=None,eta_datetime_utc=None,planned_datetime_utc=at+timedelta(minutes=20))]
        rows=[row(i+1,a,etd=at+timedelta(hours=6-i)) for i,a in enumerate(arrivals)]
        self.assertEqual(self.ordered([rows[2],row(4),rows[1],rows[0]]),[1,2,3,4])
        rows[0]["arrival_mission"].actual_block_in_datetime_utc=at.replace(tzinfo=timezone.utc)
        self.assertEqual(self.ordered(rows),[1,2,3])

    def test_confirmed_present_priority_but_unknown_after_known(self):
        at=datetime(2026,10,14,1)
        known=lambda t: Item(actual_block_in_datetime_utc=None,eta_datetime_utc=t,planned_datetime_utc=None)
        self.assertEqual(self.ordered([row(1,known(at)),row(2,known(at+timedelta(minutes=1)),present="Arrived"),
                                     row(3,present="On Ground")]),[2,1,3])

    def test_etd_flight_id_tiebreakers_and_reactivation(self):
        at=datetime(2026,10,14,1)
        arrival=Item(actual_block_in_datetime_utc=None,eta_datetime_utc=at,planned_datetime_utc=None)
        rows=[row(4,arrival,etd=at+timedelta(hours=2)),row(3,arrival,etd=at+timedelta(hours=1)),
              row(2,arrival,etd=at+timedelta(hours=1)),row(1,arrival,status="complete",done=at,etd=at)]
        rows[1]["mission"].flight_number=rows[2]["mission"].flight_number
        self.assertEqual(self.ordered(rows),[2,3,4,1])
        rows[3]["dispatch_status_key"]="assigned"
        rows[3]["assignment"].completed_at_utc=None
        self.assertEqual(self.ordered(rows),[1,2,3,4])

    def test_only_final_presentation_copies_rows_not_projection_or_plan(self):
        at=datetime(2026,10,14,1)
        rows=[row(1),row(2,Item(actual_block_in_datetime_utc=at))]
        context=dict(rows=rows,truck_visuals=object(),spear_plan=object())
        with patch("app.neonodes.neoscorpion.routes.fuel_dispatch_context",return_value=context) as core:
            result=_dispatch_presentation_context(object(),include_asset_choices=True)
        self.assertEqual([r["mission"].id for r in result["rows"]],[2,1])
        self.assertEqual([r["mission"].id for r in context["rows"]],[1,2])
        self.assertNotIn("dispatch_arrival_rank",context["rows"][0])
        self.assertEqual([r["dispatch_arrival_rank"] for r in result["rows"]],[0,1])
        self.assertEqual([r["dispatch_etd_rank"] for r in result["rows"]],[1,0])
        self.assertIs(result["truck_visuals"],context["truck_visuals"])
        self.assertIs(result["spear_plan"],context["spear_plan"])
        core.assert_called_once()


class DispatchReactivationTest(unittest.TestCase):
    setUp=timeline.OperationalTimelineTest.setUp
    prepare=timeline.OperationalTimelineTest.prepare
    save=timeline.OperationalTimelineTest.save
    off=timeline.OperationalTimelineTest.off
    final_data=timeline.OperationalTimelineTest.final_data

    def test_valid_followup_returns_completed_mission_to_unfinished(self):
        self.prepare(); self.save(self.start+timedelta(minutes=10),**self.final_data()); self.off()
        service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id)
        db.session.commit()
        other=self.fixture._mission("UPS902","N492UP",25000,2)
        db.session.commit()
        finished=_dispatch_presentation_context(self.gateway)["rows"]
        self.assertEqual([r["mission"].id for r in finished],[other.id,self.mission.id])
        service.start_follow_up_fuel_cycle(self.gateway,self.dispatcher,self.assignment.id,"uplift","35",None,None,
            now_utc=self.start+timedelta(minutes=20),expected_cycle=1,expected_tail="N491UP")
        db.session.commit()
        self.arrival.actual_block_in_datetime_utc=self.start-timedelta(hours=1)
        db.session.commit()
        active=_dispatch_presentation_context(self.gateway)["rows"]
        self.assertEqual(active[0]["mission"].id,self.mission.id)
        self.assertNotIn(active[0]["dispatch_status_key"],{"complete","fob"})
