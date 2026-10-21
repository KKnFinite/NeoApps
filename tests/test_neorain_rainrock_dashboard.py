"""Approved Rainrock inputs/report, shared facts, defaults and safe history."""
from datetime import datetime, timedelta
from unittest import TestCase
from unittest.mock import patch
from sqlalchemy import inspect, text

from app.extensions import db
from app.models import NeoRainReportEntry, NeoRainDelayInfo, SortDateOperation, Gateway
from app.services.neorain_rainrock import dashboard_context, default_planned_fph, set_default_planned_fph
from app.services.sort_date_operations import create_sort_date_operation
from app.services.schema_sync import sync_local_sqlite_schema
from tests import test_neorain_daily_briefing as daily


class RainrockDashboardTest(TestCase):
    setUp=daily.DailyBriefingTest.setUp
    def post(self,values,versions=None,operation=None):
        return self.client.post('/neorain/rainrock/inputs?operation_id='+str((operation or self.operation).id),
            json={'values':values,'versions':versions or {key:0 for key in values}})

    def state(self,operation=None):
        with patch('app.services.neorain_reports.snapshot_current_google',return_value='Google unavailable'):
            result=self.client.get('/neorain/rainrock/state?operation_id='+str((operation or self.operation).id))
        self.assertEqual(result.status_code,200,result.get_data(as_text=True));return result.json

    def test_all_ten_inputs_autosave_to_existing_records_and_share_daily_briefing(self):
        values=dict(planned_volume='119,805',actual_volume='117540',smalls_percent='89.4',planned_fph='75000',actual_fph='72850',
            briefing_ramp_staffing_planned='316',briefing_ramp_staffing_actual='320',
            briefing_hub_staffing_planned='500',briefing_hub_staffing_actual='510',note_1='Ops note\nComplete second line')
        result=self.post(values);self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(NeoRainReportEntry.query.filter_by(data_source='manual').count(),10)
        text=self.client.get('/neorain/daily-briefing/state?operation_id='+str(self.operation.id)).json['briefing']
        for line in ('Volume Plan: 119,805','Volume Actual: 117,540','Ramp Staffing: 320/316','Hub Staffing: 510/500'):
            self.assertIn(line,text)
        shared=self.client.post('/neorain/daily-briefing/inputs?operation_id='+str(self.operation.id),
            json={'values':{'planned_volume':'123456','briefing_hub_staffing_actual':'520'},
                  'versions':{'planned_volume':1,'briefing_hub_staffing_actual':1}})
        self.assertEqual(shared.status_code,200)
        state=self.state();self.assertEqual(state['fields']['planned_volume']['display'],'123,456')
        self.assertEqual(state['fields']['briefing_hub_staffing_actual']['display'],'520')
        row=NeoRainReportEntry.query.filter_by(field_name='note_1',data_source='manual').one()
        self.assertEqual(row.updated_by_user_id,self.f.actor.id);self.assertEqual(row.value,values['note_1'])

    def test_default_snapshot_only_new_sorts_and_per_sort_override_is_immutable_from_setting(self):
        self.assertEqual(default_planned_fph(self.f.gateway),75000)
        self.assertIsNone(dashboard_context(self.operation)['inputs']['planned_fph']['value'])
        def new(offset):
            op=create_sort_date_operation(self.operation.sort_date+timedelta(days=offset),'RFD','night')
            db.session.add(op);db.session.commit();return op
        first=new(1)
        self.assertEqual(dashboard_context(first)['inputs']['planned_fph']['value'],'75000')
        set_default_planned_fph(self.f.gateway,'80000');db.session.commit()
        second=new(2)
        self.assertEqual(dashboard_context(second)['inputs']['planned_fph']['value'],'80000')
        self.assertEqual(dashboard_context(first)['inputs']['planned_fph']['value'],'75000')
        self.assertEqual(self.post({'planned_fph':'70000'},{'planned_fph':1},first).status_code,200)
        set_default_planned_fph(self.f.gateway,'90000');db.session.commit()
        self.assertEqual(dashboard_context(first)['inputs']['planned_fph']['value'],'70000')
        self.assertEqual(dashboard_context(second)['inputs']['planned_fph']['value'],'80000')
        self.assertIsNone(dashboard_context(self.operation)['inputs']['planned_fph']['value'])

    def test_invalid_and_stale_saves_are_atomic_zero_and_percent_are_real(self):
        self.assertEqual(self.post({'actual_fph':'0','smalls_percent':'0'}).status_code,200)
        for key,value in (('actual_fph','-1'),('actual_fph','1.5'),('smalls_percent','100.1'),('actual_volume','NaN'),('note_1','x'*2001)):
            self.assertEqual(self.post({key:value},{key:1 if key in ('actual_fph','smalls_percent') else 0}).status_code,400)
        stale=self.post({'actual_fph':'100','planned_volume':'10'},{'actual_fph':0,'planned_volume':0})
        self.assertEqual(stale.status_code,409)
        self.assertIsNone(NeoRainReportEntry.query.filter_by(field_name='planned_volume').first())
        self.assertEqual(self.state()['fields']['actual_fph']['display'],'0')
        self.assertEqual(self.state()['fields']['smalls_percent']['display'],'0%')

    def test_actual_fph_never_calculated_or_taken_from_google(self):
        db.session.add(NeoRainReportEntry(gateway_id=self.f.gateway.id,sort_date_operation_id=self.operation.id,
            sort_date=self.operation.sort_date,sort_name='night',field_name='actual_fph',value='99999',data_source='google',
            source_report_date=self.operation.sort_date));db.session.commit()
        self.assertEqual(self.state()['fields']['actual_fph']['display'],'—')
        self.assertEqual(self.post({'actual_fph':'12345'}).status_code,200)
        self.assertEqual(self.state()['fields']['actual_fph']['display'],'12,345')

    def test_all_delays_in_saved_order_early_excluded_and_missing_actual_are_visible(self):
        self.arrival.late_metrics_included_override=False
        self.arrival.actual_block_in_datetime_utc=self.arrival.planned_datetime_utc-timedelta(minutes=2)
        self.departure.actual_block_out_datetime_utc=None
        db.session.add_all([NeoRainDelayInfo(sort_date_mission_id=self.arrival.id,minutes=0,code='WX',notes='EARLY EXCLUDED NOTE'),
            NeoRainDelayInfo(sort_date_mission_id=self.departure.id,minutes=2,code='RS',notes='SECOND DELAY FULL NOTE\nNEXT LINE')])
        db.session.commit();state=self.state();html=state['html']
        self.assertIn('EARLY EXCLUDED NOTE',html);self.assertIn('SECOND DELAY FULL NOTE\nNEXT LINE',html)
        self.assertLess(html.index('Late inbound'),html.index('SECOND DELAY'))
        self.assertEqual(html.count('data-rainrock-flight='),2)
        self.assertEqual(html.count('data-rainrock-delays='),2)
        self.assertNotIn('rainrock-delay-row',html)
        self.assertIn('IN PROGRESS',html)
        self.assertNotIn('<input',html);self.assertNotIn('<button',html)

    def test_seven_column_same_row_delays_keep_codes_notes_order_and_mission_identity(self):
        import re
        db.session.add_all([
            NeoRainDelayInfo(sort_date_mission_id=self.departure.id,minutes=5,code='GL',notes='Gate note\nFull second line'),
            NeoRainDelayInfo(sort_date_mission_id=self.departure.id,minutes=0,code='WX',notes='')])
        db.session.commit();html=self.state()['html']
        for mission,delay in ((self.arrival,'—'),(self.departure,'11 RS, 5 GL, 0 WX - Late inbound; Gate note\nFull second line')):
            row=re.search(r'<tr data-rainrock-flight="'+str(mission.id)+r'">(.*?)</tr>',html,re.S).group(1)
            self.assertEqual(row.count('<td'),7)
            self.assertIn('class="rainrock-delay"',row)
            self.assertIn('>'+delay+'</td>',row)
            if mission.id==self.arrival.id:self.assertNotIn('Gate note',row)
        self.assertEqual(html.count('<th>Delay Details</th>'),2)
        self.assertEqual(html.count('<th>Scheduled</th>'),2)
        self.assertNotIn('rainrock-delay-row',html)

    def test_scheduled_actual_and_midnight_use_canonical_values_not_eta(self):
        self.arrival.planned_datetime_utc=datetime(2026,8,31,4,55)
        self.arrival.eta_datetime_utc=datetime(2026,8,31,6)
        self.arrival.actual_block_in_datetime_utc=datetime(2026,8,31,5,10)
        db.session.commit();report=dashboard_context(self.operation)
        self.assertEqual(report['inbound_rows'][0]['scheduled'],'23:55')
        self.assertEqual(report['inbound_rows'][0]['actual'],'00:10')
        self.assertEqual(report['inbound_rows'][0]['variance'],'+15')

    def test_missing_schedule_does_not_inflate_recorded_eligible_lateness_actuals(self):
        self.arrival.planned_datetime_utc=None;db.session.commit()
        report=dashboard_context(self.operation)
        self.assertEqual(report['eligible_inbound'],0);self.assertEqual(report['recorded_inbound'],0)
        self.assertTrue(report['inbound_rows'][0]['actual'])
        self.assertEqual(report['inbound_rows'][0]['variance'],'—')

    def test_historical_scope_gateway_and_permissions(self):
        historical=SortDateOperation(gateway_id=self.f.gateway.id,gateway_code='RFD',sort_name='night',
            sort_date=self.operation.sort_date-timedelta(days=1),archived_at_utc=datetime.utcnow())
        other_gateway=Gateway(code='ONT',name='ONT');db.session.add_all([historical,other_gateway]);db.session.flush()
        foreign=SortDateOperation(gateway_id=other_gateway.id,gateway_code='ONT',sort_name='night',sort_date=self.operation.sort_date)
        db.session.add(foreign);db.session.commit()
        self.assertEqual(self.post({'actual_volume':'55'},operation=historical).status_code,200)
        self.assertIsNone(self.state()['fields']['actual_volume']['value'])
        self.assertEqual(self.state(historical)['fields']['actual_volume']['value'],'55')
        self.assertEqual(self.client.get('/neorain/rainrock/state?operation_id='+str(foreign.id)).status_code,404)
        viewer=self.f.fixture._user('rainrock-viewer','watcher');self.f.fixture._login(viewer)
        self.assertEqual(self.post({'planned_volume':'99'}).status_code,403)
        self.assertEqual(self.client.get('/neorain/rainrock/state?operation_id='+str(self.operation.id)).status_code,200)

    def test_setting_route_permission_validation_and_persistence(self):
        self.assertEqual(self.client.post('/neorain/settings',data={'action':'save_planned_fph','default_planned_fph':'80000'}).status_code,403)
        admin=self.f.fixture._user('rainrock-admin','grandmaster');self.f.fixture._login(admin)
        self.assertEqual(self.client.post('/neorain/settings',data={'action':'save_planned_fph','default_planned_fph':'80000'}).status_code,302)
        self.assertEqual(default_planned_fph(self.f.gateway),80000)
        self.assertEqual(self.client.post('/neorain/settings',data={'action':'save_planned_fph','default_planned_fph':'-1'}).status_code,400)
        self.assertEqual(default_planned_fph(self.f.gateway),80000)

    def test_additive_sqlite_migration_repeat_run_preserves_existing_records(self):
        self.post({'planned_fph':'66000'})
        db.session.execute(text('ALTER TABLE neorain_operational_settings DROP COLUMN default_planned_fph'));db.session.commit()
        for _ in range(2):sync_local_sqlite_schema(self.app);db.session.commit()
        self.assertIn('default_planned_fph',{c['name'] for c in inspect(db.engine).get_columns('neorain_operational_settings')})
        self.assertEqual(NeoRainReportEntry.query.filter_by(field_name='planned_fph').one().value,'66000')

    def test_legacy_staffing_fallback_shared_without_rewriting_older_records(self):
        row=NeoRainReportEntry(gateway_id=self.f.gateway.id,sort_date_operation_id=self.operation.id,
            sort_date=self.operation.sort_date,sort_name='night',field_name='ramp_planned_payroll',value='315',data_source='manual')
        db.session.add(row);db.session.commit()
        self.assertEqual(self.state()['fields']['briefing_ramp_staffing_planned']['value'],'315')
        briefing=self.client.get('/neorain/daily-briefing/state?operation_id='+str(self.operation.id)).json
        self.assertIn('Ramp Staffing: UNAVAILABLE/315',briefing['briefing'])
        self.assertEqual(self.post({'briefing_ramp_staffing_planned':'320'}).status_code,200)
        self.assertEqual(row.value,'315')
        self.assertEqual(self.state()['fields']['briefing_ramp_staffing_planned']['value'],'320')
