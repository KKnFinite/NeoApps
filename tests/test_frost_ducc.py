"""Committed historical-model display only; synthetic captures, no inference."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

import app.neonodes.neosubzero  # Canonical route/service import order for standalone runs.

from app.extensions import db
from app.models import FrostHistoryTask as Task, Gateway, SortDateOperation
from app.services import frost_ducc as display, frost_research, frost_vault as vault
from tests import test_frost_intelligence as fixtures

UTC=timezone.utc
AT=datetime(2026,11,19,3,20,tzinfo=UTC)


def capture_payload(gateway_id,cutoff=AT):
    baseline=frost_research.prospective_summary()
    return dict(schema_version='frost-auto-v1',gateway_id=gateway_id,baseline=baseline['baseline'],
        source=dict(cutoff_at_utc=cutoff.isoformat()),committed_at_utc=(cutoff+timedelta(seconds=1)).isoformat(),
        predictions=[dict(model_version=m['model_version'],model_sha256=m['sha256'],hours=m['hours'],
            target_at_utc=(cutoff+timedelta(hours=m['hours'])).isoformat(),state='SCORED',
            score=.12 if m['kind']=='logistic' else .34) for m in baseline['models']])


def store_capture(gateway_id,cutoff=AT,*,compact=True,scheduled_at=None,**changes):
    payload=capture_payload(gateway_id,cutoff);payload.update(changes)
    row=Task(gateway_id=gateway_id,kind='auto_capture',version='frost-auto-v1',
        identity=(scheduled_at or cutoff.replace(minute=0,second=0,microsecond=0)).strftime('%Y%m%dT%H%M%SZ'),status='ready',
        input_hash=vault.digest(vault.canonical(payload)),result_key='synthetic-archive',
        summary=dict(prediction_display=display.display_summary(payload)) if compact else {})
    db.session.add(row);db.session.commit()
    return row,payload


class DuccDisplayTest(TestCase):
    setUp=fixtures.IntelligenceTest.setUp
    tearDown=fixtures.IntelligenceTest.tearDown
    login=fixtures.IntelligenceTest.login
    owner=fixtures.IntelligenceTest.owner
    post=fixtures.IntelligenceTest.post
    path=fixtures.IntelligenceTest.path

    def activate(self):
        self.owner()
        response=self.client.post(self.path,json=dict(expected_revision=0,confirm_operational=True,
            learn_from_live_operations=False,automatic_retraining=False,operational_predictions=True))
        self.assertEqual(response.status_code,200)

    def test_off_on_off_is_fresh_read_only_and_no_acquisition_or_scoring(self):
        self.activate();row,_=store_capture(self.gateway.id)
        with patch.object(vault,'read_json',side_effect=AssertionError('Compact reads require no R2')):
            before=Task.query.count()
            version,hours=display.project(self.gateway,now=AT+timedelta(minutes=1))
            self.assertEqual(len(hours),4)
            self.assertTrue(all(v['text']=='FROST 12% / 34%' for v in hours.values()))
            self.assertEqual(version,display.revision(self.gateway,now=AT+timedelta(minutes=1)))
            self.assertEqual(Task.query.count(),before)
            self.assertFalse(db.session.dirty)
            self.assertEqual(self.post(revision=1).status_code,200)
            off,hidden=display.project(self.gateway,now=AT+timedelta(minutes=1))
            self.assertFalse(hidden);self.assertNotEqual(off,version)

    def test_missing_unavailable_stale_foreign_or_corrupt_evidence_never_becomes_a_score(self):
        self.activate();baseline=frost_research.prospective_summary();valid=display.display_summary(capture_payload(self.gateway.id))
        now=AT+timedelta(minutes=1)
        for key,value in [('gateway_id',999),('baseline','a'*64),('committed_at_utc',(now+timedelta(hours=1)).isoformat()),
                ('cutoff_at_utc',(AT-timedelta(days=1)).isoformat()),('predictions',[])]:
            with self.subTest(key=key):
                bad=dict(valid,**{key:value});self.assertIsNone(display._verified(bad,self.gateway.id,now,baseline))
        for key,value in [('state','GUESSED'),('score',float('nan')),('score',1.01),('score',True),
                ('model_sha256','b'*64),('model_version','foreign'),('hours',4),('target_at_utc',(AT+timedelta(hours=1)).isoformat())]:
            with self.subTest(key=key,value=value):
                bad=deepcopy(valid);bad['predictions'][1][key]=value
                self.assertIsNone(display._verified(bad,self.gateway.id,now,baseline))
        unavailable=deepcopy(valid)
        for p in unavailable['predictions']:p.update(state='UNAVAILABLE',score=None)
        self.assertEqual(display._verified(unavailable,self.gateway.id,now,baseline)[1],{})
        self.assertEqual(display.project(self.gateway,now=now)[1],{})
        store_capture(self.gateway.id,AT-timedelta(hours=8))
        self.assertEqual(display.project(self.gateway,now=now)[1],{})
        foreign=Gateway(code='FOREIGN',name='Foreign',is_active=True);db.session.add(foreign);db.session.commit()
        store_capture(foreign.id)
        self.assertEqual(display.project(self.gateway,now=now)[1],{})

    def test_newest_real_capture_wins_with_partial_unavailability_no_model_mixing(self):
        self.activate();store_capture(self.gateway.id,AT-timedelta(hours=1))
        payload=capture_payload(self.gateway.id)
        payload['predictions'][1].update(state='UNAVAILABLE',score=None)
        store_capture(self.gateway.id,predictions=payload['predictions'])
        _,hours=display.project(self.gateway,now=AT+timedelta(minutes=1))
        self.assertEqual(hours[AT.replace(minute=0)]['text'],'FROST 12% / —')
        self.assertIn('H0 logistic 12%',hours[AT.replace(minute=0)]['accessible'])

    def test_legacy_archive_is_verified_once_bounded_and_not_read_by_revision_or_off(self):
        self.activate();row,payload=store_capture(self.gateway.id,compact=False)
        with patch.object(vault,'read_json',return_value=payload) as archive, \
                patch('app.services.frost_history.memory_allows_work',return_value=True):
            now=AT+timedelta(minutes=1)
            display.revision(self.gateway,now=now);archive.assert_not_called()
            self.assertEqual(len(display.project(self.gateway,now=now)[1]),4)
            self.assertEqual(len(display.project(self.gateway,now=now)[1]),4)
            archive.assert_called_once()
            self.assertEqual(self.post(revision=1).status_code,200)
            self.assertEqual(display.project(self.gateway,now=now)[1],{})
            archive.assert_called_once()

    def test_legacy_failures_and_memory_deferrals_remain_unavailable_without_retry_storm(self):
        self.activate();row,payload=store_capture(self.gateway.id,compact=False)
        with patch.object(vault,'read_json',return_value=dict(payload,baseline='c'*64)) as archive, \
                patch('app.services.frost_history.memory_allows_work',return_value=True):
            for _ in range(2):self.assertEqual(display.project(self.gateway,now=AT+timedelta(minutes=1))[1],{})
            archive.assert_called_once()
        self.app.extensions.pop('frost_ducc_legacy',None)
        with patch.object(vault,'read_json',side_effect=AssertionError('Memory guard')), \
                patch('app.services.frost_history.memory_allows_work',return_value=False):
            self.assertEqual(display.project(self.gateway,now=AT+timedelta(minutes=1))[1],{})

    def test_decorates_matching_forecast_hour_without_altering_weather_or_unrelated_hours(self):
        self.activate();store_capture(self.gateway.id)
        version,hours=display.project(self.gateway,now=AT+timedelta(minutes=1))
        cards=[dict(hours=[dict(start=AT.replace(minute=0),condition='Clear',temperature='30°F'),
            dict(start=AT+timedelta(hours=2),condition='Snow',temperature='29°F')])]
        display.decorate(cards,hours)
        self.assertEqual(cards[0]['hours'][0]['condition'],'Clear')
        self.assertEqual(cards[0]['hours'][0]['frost_predictions']['text'],'FROST 12% / 34%')
        self.assertNotIn('frost_predictions',cards[0]['hours'][1])
        self.assertNotEqual(version,display.revision(self.gateway,now=AT+timedelta(hours=1)))

    def test_real_weather_context_and_lightweight_revision_agree_when_toggle_changes(self):
        from app.services import neosubzero_weather as weather
        self.activate();store_capture(self.gateway.id)
        snapshot=dict(metar=dict(value=[],stale=False),forecast=dict(value={},stale=False))
        cards=[dict(sort_date=AT.date(),hours=[dict(start=AT.replace(minute=0),condition='Clear')])]
        now=AT+timedelta(minutes=1)
        with patch.object(weather,'_weather_snapshot',return_value=snapshot), \
                patch.object(weather,'_forecast_cards',side_effect=lambda *a,**k:deepcopy(cards)):
            on=weather.neosubzero_weather_context(self.gateway,now=now)
            self.assertEqual(on['revision'],weather.neosubzero_weather_revision(gateway=self.gateway,now=now))
            self.assertIn('frost_predictions',on['future_forecast'][0]['hours'][0])
            self.assertEqual(self.post(revision=1).status_code,200)
            off=weather.neosubzero_weather_context(self.gateway,now=now)
            self.assertEqual(off['revision'],weather.neosubzero_weather_revision(gateway=self.gateway,now=now))
            self.assertNotEqual(on['revision'],off['revision'])
            self.assertNotIn('frost_predictions',off['future_forecast'][0]['hours'][0])

    def test_non_hour_cycle_and_delayed_capture_keep_actual_h6_forecast_visible(self):
        self.activate()
        cutoff=AT.replace(minute=10)+timedelta(hours=1)
        # Scheduled opening and actual capture are distinct facts.
        store_capture(self.gateway.id,cutoff,scheduled_at=AT.replace(minute=15))
        now=AT+timedelta(hours=6,minutes=40)
        _,hours=display.project(self.gateway,now=now)
        target=(cutoff+timedelta(hours=6)).replace(minute=0)
        self.assertEqual(hours[target]['text'],'FROST 12% / 34%')

    def test_configured_forecast_and_live_refresh_need_no_operational_sort_record(self):
        from datetime import time
        from sqlalchemy import event
        from app.services import neosubzero_weather as weather
        from tests.frost_schedule_fixture import configure
        self.activate()
        configure(self.gateway,days=('wednesday',),start=time(21),end=time(5))
        store_capture(self.gateway.id)
        periods=[]
        for i in range(8):
            start=AT.replace(minute=0)+timedelta(hours=i)
            periods.append(dict(startTime=start.isoformat(),endTime=(start+timedelta(hours=1)).isoformat(),
                temperature=30,temperatureUnit='F',shortForecast='Clear'))
        snapshot=dict(metar=dict(value=[],stale=False),forecast=dict(value=dict(hourly=dict(properties=dict(periods=periods))),stale=False))
        self.assertEqual(SortDateOperation.query.count(),0)
        statements=[]
        def observed(_c,_cursor,sql,*_):statements.append(sql.split()[0].upper())
        event.listen(db.engine,'before_cursor_execute',observed)
        try:
            with patch.object(weather,'_weather_snapshot',return_value=snapshot):
                for now in (AT+timedelta(minutes=1),AT+timedelta(hours=4)):
                    value=weather.neosubzero_weather_context(self.gateway,None,now=now)
                    self.assertIsNotNone(value['current_sort'])
                    self.assertEqual(value['current_sort']['sort_date'],AT.date()-timedelta(days=1))
                    self.assertTrue(any(h.get('frost_predictions') for h in value['current_sort']['hours']))
                from app.neonodes.neosubzero import routes
                with patch.object(routes,'neosubzero_weather_context',return_value=value):
                    response=self.client.get('/neosubzero/ucc?fragment=1')
                    self.assertEqual(response.status_code,200)
                    self.assertIn('data-frost-predictions',response.text)
                revision=self.client.get('/neosubzero/ucc/revision')
                self.assertEqual(revision.status_code,200)
                self.assertTrue(revision.json['refresh']['auto_refresh_enabled'])
        finally:event.remove(db.engine,'before_cursor_execute',observed)
        self.assertFalse(set(statements)&{'INSERT','UPDATE','DELETE'},statements)
        self.assertEqual(SortDateOperation.query.count(),0)

    def test_weather_only_refresh_preserves_screen_off_and_callout_refresh_policy(self):
        self.activate()
        from app.services.live_screen_refresh import save_live_screen_refresh_override
        from app.neonodes.neosubzero import routes
        save_live_screen_refresh_override(self.gateway,'neosubzero.ucc','0');db.session.commit()
        status=routes._deice_staffing_refresh(self.gateway,None,weather_only=True)
        self.assertFalse(status['auto_refresh_enabled'])
        self.assertEqual(status['reason'],'disabled')
        with patch.object(routes,'subzero_refresh_status',return_value=dict(auto_refresh_enabled=False,reason='no_current_sort')) as existing:
            self.assertFalse(routes._deice_staffing_refresh(self.gateway,None)['auto_refresh_enabled'])
            existing.assert_called_once()

    def test_weather_refresh_ignores_historical_planning_record_without_activating_it(self):
        self.activate()
        from types import SimpleNamespace
        from app.neonodes.neosubzero import routes
        operation=SimpleNamespace(id=94,is_manual_current=False)
        with patch.object(routes,'subzero_refresh_status',return_value=dict(auto_refresh_enabled=False,reason='historical_sort')) as operational:
            status=routes._deice_staffing_refresh(self.gateway,operation,weather_only=True)
            self.assertTrue(status['auto_refresh_enabled'])
            operational.assert_not_called()
            self.assertFalse(operation.is_manual_current)
            self.assertFalse(routes._deice_staffing_refresh(self.gateway,operation)['auto_refresh_enabled'])

class AutoCaptureProjectionTest(TestCase):
    def test_auto_capture_commits_projection_without_extra_scoring_or_rewriting_archive(self):
        from tests.test_frost_phase13 import AutoTest
        case=AutoTest();case.setUp();self.addCleanup(case.doCleanups)
        self.assertEqual(case.tick(),1)
        row=Task.query.filter_by(kind='auto_capture').one()
        payload=vault.read_json(row.result_key,case.f.gateway.id)
        self.assertEqual(row.summary['prediction_display'],display.display_summary(payload))
        self.assertEqual(len(row.summary['prediction_display']['predictions']),8)
        self.assertEqual(case.tick(),0);case.provider.assert_called_once()
