"""Two real capture transactions with isolated PostgreSQL, archive and provider transports."""
import io
from datetime import date, datetime, time, timedelta, timezone
from unittest import TestCase, skipUnless
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import event

import app.neonodes.neosubzero
from app.extensions import db
from app.models import FrostHistoryTask as Task
from app.services import frost_auto as auto, frost_ducc, frost_hrrr_point, frost_vault
from app.services.frost_schedule import Schedule
from tests import test_frost_phase13 as captures, test_frost_phase14 as native
from tests.frost_schedule_fixture import configure

UTC = timezone.utc
OPEN = datetime(2026, 11, 24, 3, tzinfo=UTC)
HOUR = timedelta(hours=1)


class HourlyClockTest(TestCase):
    def test_overnight_transitions_use_elapsed_hours_and_both_folds(self):
        from app.services.frost_schedule import hourly_targets
        from app.services.neosubzero_weather import _include_committed_hours
        zone = ZoneInfo('America/Chicago')
        schedule = Schedule(zone, [('saturday', time(21), time(5))])
        for day, count in [(date(2026, 3, 7), 7), (date(2026, 10, 31), 9)]:
            opening, middle = schedule.cycles(day)
            targets = list(hourly_targets(opening.start, opening.sort_end))
            self.assertEqual(len(targets), count)
            self.assertEqual(len(list(hourly_targets(middle.start, middle.sort_end))), count//2+1)
            self.assertLess(targets[-1], opening.sort_end)
            self.assertFalse(list(hourly_targets(opening.sort_end, opening.sort_end)))
            card = dict(hours=[])
            _include_committed_hours(card, set(targets), zone)
            labels = [row['time'] for row in card['hours']]
            self.assertEqual(len(set(labels)), count)
            if count == 9:
                self.assertIn('0100 CDT', labels)
                self.assertIn('0100 CST', labels)
            else:
                self.assertNotIn('0200', labels)


    def test_coincident_midpoint_and_opening_across_local_dates_share_one_capture(self):
        zone = ZoneInfo('America/Chicago')
        schedule = Schedule(zone, [('monday', time(21), time(5)), ('tuesday', time(1), time(7))])
        middle = OPEN+4*HOUR
        active = schedule.capture_cycles(middle)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].windows, ((OPEN, OPEN+8*HOUR), (middle, OPEN+10*HOUR)))
        self.assertEqual(active[0].sort_end, OPEN+10*HOUR)
        self.assertEqual(schedule.next_capture(middle), middle+3*HOUR)


class HourlyEvidenceTest(TestCase):
    def test_missing_stale_and_future_guidance_never_become_an_assessment(self):
        from app.services import frost_atmosphere, frost_forecast, frost_inference
        from app.services.frost_schedule import Cycle, hourly_targets
        at = OPEN+timedelta(minutes=7)
        cycle = Cycle(OPEN, OPEN+HOUR, OPEN, OPEN+2*HOUR)
        metars, _, grid = weather_bodies(at)
        project = frost_forecast.nws_grid_projector(grid, cutoff=at)
        rows = [project(t) for t in hourly_targets(at, cycle.sort_end)]
        observations = frost_inference.normalize_metars(metars)
        for source_rows, source_obs, expected in (
                (rows, observations, 'possible'),
                (rows, [], 'unknown'),
                ([], observations, 'unknown'),
                ([dict(r,issued_at_utc=(at-25*HOUR).isoformat()) for r in rows], observations, 'unknown'),
                ([dict(r,issued_at_utc=(at+HOUR).isoformat()) for r in rows], observations, 'unknown')):
            source = dict(cutoff_at_utc=at.isoformat(), nws_hourly=source_rows, observations=source_obs)
            hours = frost_atmosphere.hourly_for_sort(source, cycle)['hours']
            self.assertEqual([row['state'] for row in hours], [expected]*2)
            self.assertTrue(all('score' not in row for row in hours))

    def test_acquisition_stops_before_next_download_if_window_closes(self):
        metars, _, _ = weather_bodies(OPEN)
        calls = []
        def fetch(url, _maximum):
            calls.append(url)
            return frost_vault.canonical(metars), dict(retrieved_at_utc=OPEN.isoformat())
        with auto.priority.cooperate(), patch.object(auto, 'fetch', side_effect=fetch):
            auto.priority.limit_to_window(lambda: not calls)
            with self.assertRaises(auto.priority.Deferred):
                auto.acquire()
        self.assertEqual(len(calls), 1)

    def test_model_target_beyond_fractional_close_never_attaches_to_last_tile(self):
        from app.services import frost_atmosphere, frost_research
        from app.services.frost_schedule import hourly_targets
        from tests.test_frost_ducc import capture_payload
        at = OPEN+timedelta(minutes=20)
        end = OPEN+HOUR+timedelta(minutes=10)
        payload = capture_payload(1, at)
        payload['atmosphere'] = dict(method=frost_atmosphere.VERSION, horizons={}, hourly=dict(
            sort_start_utc=OPEN.isoformat(), sort_end_utc=end.isoformat(),
            hours=[dict(target_at_utc=t.isoformat(), state='mixed', reason='Mixed guidance.')
                   for t in hourly_targets(at, end)]))
        display = frost_ducc.display_summary(payload)
        _, hours = frost_ducc._verified(display, 1, at+timedelta(minutes=1), frost_research.prospective_summary())
        self.assertEqual(len(hours), 2)
        self.assertEqual(len(hours[OPEN]['scores']), 2)
        self.assertEqual(hours[OPEN+HOUR]['scores'], {})
        other = (OPEN+timedelta(minutes=5), end)
        self.assertIsNone(frost_ducc._verified(display, 1, at+timedelta(minutes=1),
                                               frost_research.prospective_summary(), other))
        display['hourly']['sort_windows'] = [(OPEN.isoformat(), end.isoformat()),
                                              (other[0].isoformat(), other[1].isoformat())]
        self.assertIsNotNone(frost_ducc._verified(display, 1, at+timedelta(minutes=1),
                                                  frost_research.prospective_summary(), other))


def weather_bodies(at, missing_hour=None):
    """Real provider formats. Optionally omit one temperature without erasing other hours."""
    first = at.replace(minute=0, second=0, microsecond=0)
    def quantity(unit, value, gaps=()):
        return dict(uom='wmoUnit:'+unit, values=[
            dict(validTime=(first+i*HOUR).isoformat()+'/PT1H', value=value)
            for i in range(12) if i not in gaps])
    grid = dict(geometry=dict(type='Polygon', coordinates=[
        [[-89.10,42.18],[-89.07,42.18],[-89.07,42.21],[-89.10,42.21],[-89.10,42.18]]]),
        properties=dict(updateTime=(at-timedelta(minutes=5)).isoformat(),
            temperature=quantity('degC', 1, (missing_hour,)), dewpoint=quantity('degC', 0),
            relativeHumidity=quantity('percent', 93), windSpeed=quantity('km_h-1', 3),
            skyCover=quantity('percent', 8), minTemperature=quantity('degC', 0)))
    points = dict(geometry=dict(coordinates=[-89.0876,42.1915]),
                  properties=dict(forecastGridData='https://api.weather.gov/gridpoints/LOT/25,85'))
    metars = [dict(icaoId='KRFD', reportTime=(at-i*HOUR).isoformat(), temp=1, dewp=0, wspd=2,
                   wdir=180, visib=10, clouds=[dict(cover='CLR')]) for i in range(14)]
    return metars, points, grid


class CaptureFixture:
    """Drive baseline and candidate with identical bytes and clock."""
    def __init__(self, test):
        case = captures.PostgresAutoTest()
        case.setUp()
        test.addCleanup(case.doCleanups)
        self.case = case
        self.gateway = case.f.gateway
        case.t = OPEN
        configure(self.gateway, days=('monday',), start=time(21), end=time(5))
        def receipt_clock(_mapper, _conn, row):
            if row.retry_at is None:
                row.retry_at = auto.naive(case.t)
        event.listen(Task, 'before_insert', receipt_clock)
        test.addCleanup(event.remove, Task, 'before_insert', receipt_clock)
        helper = case.model_fixture()
        test.enterContext(patch.object(auto.frost_research, 'prospective_summary', return_value=dict(
            baseline=helper.baseline['fingerprint'], models=helper.baseline['models'])))
        for entry in helper.baseline['models']:
            raw = (helper.frozen.parent/'artifacts'/(entry['sha256']+'.json')).read_bytes()
            auto.register_research(self.gateway.id, case.fixture.actor.id, raw)
        db.session.commit()
        case.acquire.stop()
        self.forecast_inputs = {}
        self.native_inputs = {}
        for offset in (0, 4):
            at = OPEN+offset*HOUR+timedelta(minutes=2)
            self.forecast_inputs[at] = weather_bodies(at, missing_hour=2 if offset else None)
            self.native_inputs[at] = native.native_fixture(at)[2]
        def fetch(url, _maximum):
            metars, points, grid = self.forecast_inputs[case.t]
            value = metars if 'aviationweather' in url else points if '/points/' in url else grid
            raw = frost_vault.canonical(value)
            return raw, dict(url=url, retrieved_at_utc=case.t.isoformat(),
                             sha256=frost_vault.digest(raw), bytes=len(raw))
        def reply(request, **_kwargs):
            data = b'' if request.method=='HEAD' else self.native_inputs[case.t][request.full_url]
            response = io.BytesIO(data)
            response.status = 200
            response.headers = {'Last-Modified': (case.t-HOUR).strftime('%a, %d %b %Y %H:%M:%S GMT')}
            return response
        self.fetch = test.enterContext(patch.object(auto, 'fetch', side_effect=fetch))
        opener = test.enterContext(patch.object(frost_hrrr_point, 'build_opener'))
        self.http = opener.return_value.open
        self.http.side_effect = reply
        self.scores = test.enterContext(patch.object(auto, 'predictions', wraps=auto.predictions))
        test.enterContext(patch.object(frost_ducc.frost_intelligence, 'read',
                                     return_value=dict(operational_predictions=True, revision=1)))

    def tick(self, at):
        self.case.t = at
        return self.case.tick()

    def view(self):
        return frost_ducc.project(self.gateway, now=self.case.t+timedelta(seconds=1))[1]


@skipUnless(captures.pg.URI, 'Requires disposable loopback PostgreSQL')
class HourlyReceiptTest(TestCase):
    def test_two_captures_replace_remaining_hours_and_preserve_committed_history(self):
        fixture = CaptureFixture(self)
        self.assertEqual(fixture.tick(OPEN-timedelta(seconds=1)), 0)
        self.assertEqual(fixture.fetch.call_count, 0)
        self.assertEqual(fixture.tick(OPEN+timedelta(minutes=2)), 1)
        first = Task.query.filter_by(kind='auto_capture').one()
        digest, key = first.input_hash, first.result_key
        original = frost_vault.read_json(key, fixture.gateway.id)
        self.assertEqual(sum(p['state']=='SCORED' for p in original['predictions']), 8)
        hours = fixture.view()
        self.assertEqual(set(hours), {OPEN+i*HOUR for i in range(8)})
        self.assertTrue(all(v['outlook']!='Frost assessment unavailable' for v in hours.values()))
        self.assertEqual([i for i in range(8) if hours[OPEN+i*HOUR]['text']], [0, 1, 3, 6])
        self.assertEqual(original['atmosphere']['hourly']['hours'][-1]['guidance'], 'NWS')
        provider_calls = fixture.http.call_count
        self.assertGreater(provider_calls, 0)
        self.assertEqual(fixture.tick(OPEN+2*HOUR), 0)
        midpoint = OPEN+4*HOUR+timedelta(minutes=2)
        self.assertEqual(fixture.tick(midpoint), 1)
        self.assertEqual(fixture.tick(midpoint), 0)
        self.assertEqual(fixture.tick(OPEN+8*HOUR), 0)
        self.assertEqual(fixture.tick(OPEN+9*HOUR), 0)
        self.assertEqual(fixture.scores.call_count, 2)
        self.assertEqual((fixture.fetch.call_count, fixture.http.call_count), (6, 2*provider_calls))
        self.assertEqual(Task.query.filter_by(kind='auto_capture', status='ready').count(), 2)
        self.assertEqual(first.input_hash, digest)
        self.assertEqual(frost_vault.read_json(key, fixture.gateway.id), original)
        fixture.case.t = midpoint
        with patch.object(frost_vault, 'read_json', side_effect=AssertionError('Unexpected archive read')):
            updated = fixture.view()
        self.assertEqual(len(updated), 8)
        for i in range(8):
            item = updated[OPEN+i*HOUR]
            if i < 4:
                self.assertEqual(item, hours[OPEN+i*HOUR])
            else:
                self.assertEqual(item['captured_cutoff_utc'], midpoint.isoformat())
        self.assertEqual(updated[OPEN+6*HOUR]['outlook'], 'Frost assessment unavailable')
        self.assertIsNone(updated[OPEN+6*HOUR]['text'])
