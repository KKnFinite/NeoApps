import json
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from flask import Flask
from app.services.neorain_google_runtime import RuntimeGoogleAdapter, protection_at, column_index, RACE_REASON
from app.services.neorain_google_overrides import APPROVED, MB, RAIN, REPTILE, GoogleOverrideBlocked, GoogleWriteUncertain, attempt_once
from app.services.google_motherbrain_sheets import GOOGLE_MOTHERBRAIN_WRITE_SCOPES
from tests import test_neorain_overrides as contracts
from app.extensions import db
from app.models import NeoRainMissionOverride, NeoRainOverrideAudit, SortDateGoogleMissionLink


class RuntimeAdapterTest(unittest.TestCase):
    def setUp(self):
        self.credentials={'client_email':'existing@test.invalid','private_key':'PRIVATE-DO-NOT-PRINT','token_uri':'https://oauth2.googleapis.com/token'}
        self.writer=Mock();self.writer.http_client.auth.scopes=GOOGLE_MOTHERBRAIN_WRITE_SCOPES
        self.meta=Mock();self.meta.http_client.request.return_value.json.return_value={'id':RAIN,'capabilities':{'canEdit':True}}
        self.patches=[patch('app.services.neorain_google_runtime._create_gspread_writer',return_value=self.writer),patch('app.services.neorain_google_runtime._create_gspread',return_value=self.meta)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.config={'GOOGLE_SERVICE_ACCOUNT_JSON':json.dumps(self.credentials)}
        self.adapter=RuntimeGoogleAdapter(self.config)

    def fixture(self,field,cell=None):
        book,tab,col,start,end,_=APPROVED[field]
        metadata={'spreadsheetId':book,'sheets':[{'properties':{'title':tab,'sheetId':1}}]}
        data={'sheets':[{'data':[{'startRow':start-1,'startColumn':column_index(col),'rowData':[{'values':[cell or {'userEnteredValue':{'boolValue':False}}]}]}]}]}
        self.writer.open_by_key.return_value.fetch_sheet_metadata.side_effect=[metadata,data]
        return book,tab,f'{col}{start}'

    def test_shared_fallback_dedicated_and_no_secret_output(self):
        self.assertEqual(self.adapter.credential_source,'fallback')
        self.assertTrue(self.adapter.fallback_matches)
        dedicated={**self.credentials,'client_email':'dedicated@test.invalid'}
        adapter=RuntimeGoogleAdapter({**self.config,'GOOGLE_MOTHERBRAIN_SERVICE_ACCOUNT_JSON':json.dumps(dedicated)})
        self.assertEqual(adapter.credential_source,'dedicated');self.assertFalse(adapter.fallback_matches)
        self.assertTrue(adapter.write_scope_requested)
        self.fixture('included')
        output=self.adapter.diagnose('included')
        self.assertNotIn(self.credentials['private_key'],json.dumps(output))
        self.assertTrue(output['effective_workbook_write_permission'])
        self.assertEqual(output['status'],'BLOCKED');self.assertFalse(output['race_contract_verified'])

    def test_all_approved_inputs_inspected_but_never_enabled(self):
        for field in APPROVED:
            args=self.fixture(field)
            self.assertTrue(self.adapter.inspect_input(*args)['manual_contract_verified'])
            with self.assertRaises(GoogleOverrideBlocked):self.adapter.write_input_once(*args,False)
        self.assertEqual(self.adapter.certified_fields,())
        self.writer.open_by_key.return_value.values_update.assert_not_called()
        self.assertNotIn('etd',APPROVED)

    def test_formula_generated_and_blank_are_not_proven_manual(self):
        for cell in ({'userEnteredValue':{'formulaValue':'=1'}},{'effectiveValue':{'numberValue':0}},{}):
            args=self.fixture('included',cell=cell or {'effectiveValue':{}})
            self.assertFalse(self.adapter.inspect_input(*args)['manual_contract_verified'])

    def test_effective_permission_and_workbook_allowlist(self):
        self.assertTrue(self.adapter.permission_verified(RAIN))
        self.meta.http_client.request.return_value.json.return_value={'id':RAIN,'capabilities':{'canEdit':False}}
        self.assertFalse(self.adapter.permission_verified(RAIN))
        with self.assertRaises(GoogleOverrideBlocked):self.adapter.workbook('unapproved')
        with self.assertRaises(GoogleOverrideBlocked):self.adapter.inspect_input(MB,'Inbound','AB51')
        self.assertFalse(self.adapter.permission_verified('unapproved'))

    def test_named_protection_sheet_holes_and_unknown(self):
        area={'sheetId':1,'startRowIndex':0,'endRowIndex':50,'startColumnIndex':0,'endColumnIndex':20}
        meta={'namedRanges':[{'namedRangeId':'n','range':area}]}
        sheet={'properties':{'sheetId':1},'protectedRanges':[{'namedRangeId':'n','requestingUserCanEdit':False}]}
        self.assertEqual(protection_at(meta,sheet,3,16),'DENIED')
        sheet['protectedRanges'][0]['unprotectedRanges']=[area]
        self.assertEqual(protection_at(meta,sheet,3,16),'UNPROTECTED')
        sheet['protectedRanges']=[{'tableId':'unknown'}]
        self.assertEqual(protection_at(meta,sheet,3,16),'UNKNOWN')

    def test_normalized_reordered_identity_and_stale_date(self):
        manifest=[['UPS0948','N438UP','SDF','A1',46309.1],['UPS910','N910UP','LAX','B2',46309.2]]
        target=[['UPS910','LAX','N910UP','B2',46309.2],['UPS948','SDF','N438UP','A1',46309.1]]
        def open_book(book):
            wb=Mock()
            def read(area,params):
                if area=='Inbound!H2':return {'values':[[46308]]}
                if area=='Outbound!P4:T100':return {'values':manifest}
                return {'values':target}
            wb.values_get.side_effect=read
            return wb
        self.writer.open_by_key.side_effect=open_book
        rows=self.adapter.read_identities(REPTILE,'Sim Load')
        self.assertEqual([r['flight'] for r in rows],['UPS910','UPS948'])
        self.assertEqual([r['row'] for r in rows],[4,5])
        target[0][4]=46000
        with self.assertRaises(GoogleOverrideBlocked):self.adapter.read_identities(REPTILE,'Sim Load')
        target[0][4]=46309.2;manifest.append(manifest[0])
        with self.assertRaises(GoogleOverrideBlocked):self.adapter.read_identities(REPTILE,'Sim Load')

    def test_existing_sektor_connection_and_input_writer_unchanged(self):
        from types import SimpleNamespace
        from app.services import neosektor_sheets_compat as sektor
        worksheet=Mock()
        with patch.object(sektor,'sheets_credentials_configured',return_value=True),patch.object(sektor,'_get_worksheet',return_value=worksheet),patch.object(sektor,'_merge_cached_google_values'):
            sektor._write_google_operational_values(SimpleNamespace(id=1),{'B2':5})
        worksheet.update_acell.assert_called_once_with('B2',5)

    def test_real_gspread_transport_diagnostic_is_get_only(self):
        import gspread
        from types import SimpleNamespace
        transport=Mock()
        resource={'spreadsheetId':RAIN,'properties':{'title':'Rain','id':RAIN},
            'sheets':[{'properties':{'title':'Outbound','sheetId':1},'data':[{'startRow':2,'startColumn':16,
                'rowData':[{'values':[{'userEnteredValue':{'boolValue':False}}]}]}]}]}
        def request(method,url,**kwargs):
            response=Mock(ok=True)
            response.json.return_value=({'id':RAIN,'capabilities':{'canEdit':True}} if '/drive/' in url else resource)
            return response
        transport.request.side_effect=request
        client=gspread.Client(auth=None,session=transport)
        client.http_client.auth=SimpleNamespace(scopes=GOOGLE_MOTHERBRAIN_WRITE_SCOPES)
        client.set_timeout(5)
        with patch('app.services.neorain_google_runtime._create_gspread_writer',return_value=client),patch('app.services.neorain_google_runtime._create_gspread',return_value=client):
            adapter=RuntimeGoogleAdapter(self.config)
            result=adapter.diagnose('included')
        self.assertTrue(result['authenticated_read_ok']);self.assertEqual(result['cells']['manual_constants'],1)
        self.assertTrue(result['effective_workbook_write_permission'])
        self.assertTrue(all((call.args[0] if call.args else call.kwargs['method']).lower()=='get' for call in transport.request.call_args_list))
        self.assertTrue(all(call.kwargs['timeout']==5 for call in transport.request.call_args_list))


class DiagnosticRouteTest(unittest.TestCase):
    def setUp(self):
        self.contract=contracts.RainOverrideTest();self.contract.setUp();self.addCleanup(self.contract.doCleanups)
        self.client=self.contract.client

    def test_permissions_get_only_cache_and_no_secret_exceptions(self):
        with patch('app.neonodes.neorain.certification_routes.RuntimeGoogleAdapter') as factory:
            for role in ('watcher','operator','simulator'):
                self.contract.actor.role=role;db.session.commit()
                self.assertEqual(self.client.get('/neorain/google-certification?field=sta').status_code,403)
            factory.assert_not_called()
            self.contract.actor.role='grandmaster';db.session.commit()
            self.assertEqual(self.client.get('/neorain/google-certification').status_code,200)
            self.assertEqual(self.client.get('/neorain/google-certification?field=etd').status_code,400)
            self.assertEqual(self.client.get('/neorain/google-certification?field=sta&url=evil').status_code,400)
            self.assertEqual(self.client.post('/neorain/google-certification').status_code,405)
            factory.return_value.diagnose.return_value={'status':'BLOCKED','field':'sta'}
            for _ in range(2):
                result=self.client.get('/neorain/google-certification?field=sta')
                self.assertEqual(result.status_code,200);self.assertIn('no-store',result.headers['Cache-Control'])
            factory.assert_called_once()
            factory.side_effect=RuntimeError('PRIVATE-KEY-DO-NOT-RETURN')
            result=self.client.get('/neorain/google-certification?field=elmac')
            self.assertNotIn('PRIVATE-KEY',result.get_data(as_text=True))
            self.assertEqual(result.json['status'],'BLOCKED')
            # The fixture deliberately holds one app context. End with anonymous
            # access so Flask-Login's g cache cannot contaminate later assertions.
            from flask import g
            g.pop('_login_user',None)
            self.assertEqual(self.contract.app.test_client().get('/neorain/google-certification?field=sta').status_code,302)

    def test_uncertain_result_persists_neo_and_never_retries(self):
        c=self.contract
        db.session.add(SortDateGoogleMissionLink(sort_date_operation_id=c.operation.id,sort_date_mission_id=c.arrival.id,mission_type='arrival',source_sheet='Inbound',source_row=4));db.session.commit()
        with patch('app.services.neorain_google_overrides.certified_write',side_effect=GoogleWriteUncertain('Timed out after send')) as writer:
            result=c.edit(c.arrival,'sta','0235')
            self.assertEqual(result.status_code,200)
            row=NeoRainMissionOverride.query.one()
            self.assertEqual(row.sync_status,'UNCERTAIN');self.assertTrue(row.active)
            attempt_once(row.id,row.generation);writer.assert_called_once()
            self.assertEqual(NeoRainOverrideAudit.query.filter_by(action='SYNC').count(),1)

    def test_timeout_after_send_is_uncertain_exactly_one_attempt(self):
        c=self.contract
        db.session.add(SortDateGoogleMissionLink(sort_date_operation_id=c.operation.id,sort_date_mission_id=c.arrival.id,mission_type='arrival',source_sheet='Inbound',source_row=4));db.session.commit()
        adapter=Mock(certified_fields={'sta'})
        adapter.permission_verified.return_value=True
        adapter.read_identities.return_value=[{'flight':c.arrival.flight_number,'direction':'arrival','gateway':'RFD',
            'sort_date':c.operation.sort_date.isoformat(),'sort_name':c.operation.sort_name,
            'tail':c.arrival.assigned_tail_number,'place':c.arrival.origin,'row':4}]
        adapter.inspect_input.return_value={'manual_contract_verified':True,'spill_owned':False,'cell':{}}
        adapter.write_input_once.side_effect=TimeoutError('Transport timed out after send')
        c.app.config['NEORAIN_CERTIFIED_GOOGLE_ADAPTER']=adapter
        response=c.edit(c.arrival,'sta','0235')
        self.assertEqual(response.status_code,200);self.assertEqual(response.json['sync_status'],'UNCERTAIN')
        row=NeoRainMissionOverride.query.one();attempt_once(row.id,row.generation)
        adapter.write_input_once.assert_called_once();adapter.read_input.assert_not_called()
