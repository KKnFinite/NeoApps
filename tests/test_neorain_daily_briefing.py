"""Canonical briefing facts, independent saved inputs and selected-sort safety."""
from datetime import datetime, timedelta
from unittest import TestCase
from unittest.mock import patch

from app.extensions import db
from app.models import Gateway, NeoRainDelayInfo, NeoRainReportEntry, SortDateMission, SortDateOperation
from app.services.neorain_daily_briefing import (
    BriefingConflict, briefing_context, format_briefing, save_inputs,
)
from tests import test_neorain_overrides as fixtures


class DailyBriefingTest(TestCase):
    def setUp(self):
        self.f=fixtures.RainOverrideTest();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.operation,self.client,self.app=self.f.operation,self.f.client,self.f.app
        self.arrival,self.departure=self.f.arrival,self.f.departure
        self.arrival.origin='SDF';self.arrival.destination='RFD'
        self.arrival.wave=self.departure.wave='1'
        self.arrival.actual_block_in_datetime_utc=self.arrival.planned_datetime_utc+timedelta(minutes=15)
        self.departure.actual_block_out_datetime_utc=self.departure.planned_datetime_utc+timedelta(minutes=11)
        db.session.add(NeoRainDelayInfo(sort_date_mission_id=self.departure.id,minutes=11,code='RS',notes='Late inbound'))
        db.session.commit()

    def post(self,values,versions=None,operation=None):
        operation=operation or self.operation
        return self.client.post('/neorain/daily-briefing/inputs?operation_id='+str(operation.id),
            json={'values':values,'versions':versions or {key:0 for key in values}})

    def test_section_order_plain_text_facts_and_missing_values(self):
        from app.services.shell_metadata import _neorain_labels
        self.assertEqual(_neorain_labels('/neorain/daily-briefing'),('DAILY BRIEFING','DAILY BRIEFING'))
        text=format_briefing(briefing_context(self.operation))
        labels=['08/30/2026','RFD Night Update','Safety:','Incident:','Volume Plan:', 'Volume Actual:',
            'Ramp Staffing:','Hub Staffing:','INBOUND PERFORMANCE','SERVICE ISSUES:','OUTBOUND PERFORMANCE','SERVICE:']
        self.assertEqual([text.index(label) for label in labels],sorted(text.index(label) for label in labels))
        self.assertIn('1 out of 1 late for 15-minutes avg',text)
        self.assertIn('SDF-RFD UPS1487 N438UP +15',text)
        self.assertIn('RFD-SDF UPS948 N948UP +11 RS',text)
        self.assertIn('Volume Plan: UNAVAILABLE\nVolume Actual: UNAVAILABLE',text)
        self.assertIn('Safety: UNAVAILABLE\nIncident:',text)
        self.assertNotIn('\r',text)
        self.assertNotIn('ANC INFO',text);self.assertNotIn("ULD's",text)

    def test_saved_inputs_zero_multiline_actor_and_google_independence(self):
        db.session.add(NeoRainReportEntry(gateway_id=self.f.gateway.id,sort_date_operation_id=self.operation.id,
            sort_date=self.operation.sort_date,sort_name='Night',field_name='planned_volume',value='9999',data_source='google'))
        db.session.commit()
        with patch('app.services.neorain_reports.read_google_recap',side_effect=AssertionError('No Google I/O')):
            first=self.post({'briefing_safety':'No injuries\r\nWalkways inspected','actual_volume':'0',
                             'briefing_ramp_staffing_planned':'0','briefing_hub_staffing_actual':'4'})
            self.assertEqual(first.status_code,200,first.json)
            self.assertIn('Volume Plan: UNAVAILABLE\nVolume Actual: 0',first.json['briefing'])
            self.assertIn('No injuries\nWalkways inspected',first.json['briefing'])
            self.assertEqual(self.client.get('/neorain/daily-briefing').status_code,200)
            self.assertEqual(self.client.get('/neorain/daily-briefing/state').json['briefing'],first.json['briefing'])
        row=NeoRainReportEntry.query.filter_by(field_name='briefing_safety').one()
        self.assertEqual(row.updated_by_user_id,self.f.actor.id)
        self.assertEqual(row.version,1)
        cleared=self.post({'briefing_safety':''},{'briefing_safety':1})
        self.assertEqual(cleared.status_code,200)
        self.assertIn('Safety: UNAVAILABLE',cleared.json['briefing'])
        self.assertEqual(row.version,2)
        self.assertEqual(NeoRainReportEntry.query.filter_by(data_source='google').one().value,'9999')

    def test_exact_west_zone_copy_layout_and_issue_totals(self):
        values=dict(briefing_safety='None',briefing_incident='None',planned_volume='119,805',
            actual_volume='117540',briefing_ramp_staffing_actual='316',briefing_ramp_staffing_planned='320',
            briefing_hub_staffing_actual='500',briefing_hub_staffing_planned='520',
            briefing_inbound_missorts='252',briefing_libs='54',briefing_service='No service failures reported.',
            briefing_operational_notes='RFD: Recovery completed.\nFinal checks complete.')
        response=self.post(values);self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['briefing'],
            "********************\n08/30/2026\nRFD Night Update\n********************\n"
            "Safety: None\nIncident: None\nVolume Plan: 119,805\nVolume Actual: 117,540\nRamp Staffing: 316/320\nHub Staffing: 500/520\n\n"
            "INBOUND PERFORMANCE\n1 out of 1 late for 15-minutes avg\nSDF-RFD UPS1487 N438UP +15\n\n"
            "SERVICE ISSUES: 306\nInbound missorts: 252\nLIB's: 54\n\n"
            "OUTBOUND PERFORMANCE\n1 out of 1 late for 11-minutes avg\nRFD-SDF UPS948 N948UP +11 RS\n\n"
            "SERVICE:\nNo service failures reported.\n\nRFD: Recovery completed.\nFinal checks complete.")

    def test_service_total_unknown_until_both_counts_known_and_zero_is_real(self):
        response=self.post({'briefing_inbound_missorts':'0'})
        self.assertIn('SERVICE ISSUES: UNAVAILABLE',response.json['briefing'])
        response=self.post({'briefing_libs':'0'})
        self.assertIn("SERVICE ISSUES: 0\nInbound missorts: 0\nLIB's: 0",response.json['briefing'])

    def test_volume_reuses_only_dated_saved_rainrock_snapshot_with_manual_fallback(self):
        row=NeoRainReportEntry(gateway_id=self.f.gateway.id,sort_date_operation_id=self.operation.id,
            sort_date=self.operation.sort_date,sort_name='Night',field_name='planned_volume',value='119805',
            data_source='google',source_report_date=self.operation.sort_date)
        db.session.add(row);db.session.commit()
        self.assertIn('Volume Plan: 119,805',format_briefing(briefing_context(self.operation)))
        self.assertEqual(self.post({'planned_volume':'1000'}).status_code,200)
        self.assertIn('Volume Plan: 1,000',format_briefing(briefing_context(self.operation)))
        self.assertEqual(row.value,'119805')
        self.post({'planned_volume':''},{'planned_volume':1})
        row.source_report_date=self.operation.sort_date-timedelta(days=1);db.session.commit()
        self.assertIn('Volume Plan: UNAVAILABLE',format_briefing(briefing_context(self.operation)))

    def test_positive_subminute_is_not_a_zero_minute_late_flight_and_codes_are_ordered(self):
        self.arrival.actual_block_in_datetime_utc=self.arrival.planned_datetime_utc+timedelta(seconds=59)
        db.session.add_all([NeoRainDelayInfo(sort_date_mission_id=self.departure.id,minutes=0,code='WS'),
                            NeoRainDelayInfo(sort_date_mission_id=self.departure.id,minutes=1,code='RS')])
        db.session.commit()
        text=format_briefing(briefing_context(self.operation))
        self.assertIn('0 out of 1 late for 0-minutes avg',text)
        self.assertNotIn('N438UP',text);self.assertNotIn('+0',text)
        self.assertIn('+11 RS/WS',text)

    def test_invalid_or_stale_bulk_save_is_atomic_and_no_schema_change(self):
        self.assertEqual(self.post({'briefing_safety':'Initial'}).status_code,200)
        stale=self.post({'briefing_safety':'Overwrite','actual_volume':'100'})
        self.assertEqual(stale.status_code,409)
        self.assertEqual(NeoRainReportEntry.query.filter_by(data_source='manual').count(),1)
        for value in ('-1','NaN','1.5'):
            self.assertEqual(self.post({'actual_volume':value}).status_code,400)
        self.assertEqual(self.post({'briefing_safety':'x'*2001},{'briefing_safety':1}).status_code,400)
        self.assertEqual(self.post({'not_a_field':'x'}).status_code,400)
        self.assertEqual(self.post({'actual_volume':1}).status_code,400)
        self.assertEqual(self.post({'actual_volume':'1'},{'actual_volume':True}).status_code,400)
        self.assertEqual(NeoRainReportEntry.query.filter_by(data_source='manual').one().value,'Initial')

    def test_canonical_include_exclude_partial_and_midnight(self):
        self.departure.late_metrics_included_override=False
        self.arrival.actual_block_in_datetime_utc=None
        db.session.commit()
        context=briefing_context(self.operation)
        self.assertIsNone(context['inbound']['late_count'])
        self.assertIsNone(context['inbound']['average'])
        self.assertEqual(context['outbound']['details'],[])
        text=format_briefing(context)
        self.assertNotIn('N948UP',text)
        self.assertIn('actual timings recorded for 0 of 1',text)
        self.arrival.planned_datetime_utc=datetime(2026,8,31,4,55)
        self.arrival.actual_block_in_datetime_utc=datetime(2026,8,31,5,10)
        db.session.commit()
        context=briefing_context(self.operation)
        self.assertEqual(context['inbound']['late_minutes'],15)
        self.assertEqual(context['inbound']['average'],'15')
        self.assertTrue(format_briefing(context).startswith('********************\n08/30/2026\n'))

    def test_historical_sort_inputs_stay_independent_and_do_not_read_google(self):
        historical=SortDateOperation(gateway_id=self.f.gateway.id,gateway_code='RFD',
            sort_date=self.operation.sort_date-timedelta(days=1),sort_name='night',archived_at_utc=datetime.utcnow())
        db.session.add(historical);db.session.commit()
        self.assertEqual(self.post({'briefing_service':'Historical service'},operation=historical).status_code,200)
        self.assertEqual(self.post({'briefing_service':'Current service'}).status_code,200)
        with patch('app.services.neorain_reports.read_google_recap',side_effect=AssertionError('No Google I/O')):
            old=self.client.get('/neorain/daily-briefing/state?operation_id='+str(historical.id))
            self.assertIn('Historical service',old.json['briefing'])
            self.assertNotIn('Current service',old.json['briefing'])
            self.assertIn('Current service',self.client.get('/neorain/daily-briefing/state').json['briefing'])
        self.assertEqual(NeoRainReportEntry.query.count(),2)

    def test_permissions_and_gateway_scope(self):
        watcher=self.f.fixture._user('briefing_watcher','watcher',rain_role='watcher')
        db.session.commit();self.f.fixture._login(watcher)
        self.assertEqual(self.post({'briefing_safety':'Changed'}).status_code,403)
        self.assertEqual(self.client.get('/neorain/daily-briefing/state').status_code,200)
        html=self.client.get('/neorain/daily-briefing').get_data(as_text=True)
        self.assertNotIn('data-briefing-save',html)
        self.assertIn('data-briefing-field=',html)
        gateway=Gateway(code='SDF',name='Other');db.session.add(gateway);db.session.flush()
        operation=SortDateOperation(gateway_id=gateway.id,gateway_code='SDF',sort_date=self.operation.sort_date,sort_name='night')
        db.session.add(operation);db.session.commit();self.f.fixture._login(self.f.actor)
        self.assertEqual(self.post({'briefing_service':'Wrong gateway'},operation=operation).status_code,404)
        self.assertEqual(NeoRainReportEntry.query.count(),0)

    def test_existing_manual_sort_notes_retained_and_escaped(self):
        save_inputs(self.operation,{'briefing_operational_notes':'<script>not executable</script>'},
                    {'briefing_operational_notes':0},self.f.actor.id)
        db.session.add(NeoRainReportEntry(gateway_id=self.f.gateway.id,sort_date_operation_id=self.operation.id,
            sort_date=self.operation.sort_date,sort_name='Night',field_name='note_1',value='Earlier saved note',data_source='manual'))
        db.session.commit()
        html=self.client.get('/neorain/daily-briefing').get_data(as_text=True)
        self.assertIn('Earlier saved note',html)
        self.assertIn('&lt;script&gt;not executable&lt;/script&gt;',html)
        self.assertNotIn('<script>not executable</script>',html)
