import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services import archive_transfer_diagnostics as diagnostics


class ArchiveTransferDiagnosticsTest(unittest.TestCase):
    def test_sdk_transport_counts_without_reading_body_or_exposing_credentials(self):
        import boto3
        from botocore.awsrequest import AWSResponse
        from botocore.config import Config
        from urllib3.response import HTTPResponse
        client=boto3.session.Session().client('s3',endpoint_url='https://archive.example.test',
            region_name='auto',aws_access_key_id='PRIVATE_ACCESS',aws_secret_access_key='PRIVATE_SECRET',
            config=Config(signature_version='s3v4',retries={'total_max_attempts':1}))
        diagnostics.attach(client)
        diagnostics.attach(client)  # No doubled handlers.
        sent=[]
        def send(request):
            sent.append(request)
            return AWSResponse(request.url,200,{'content-length':'0'},
                HTTPResponse(body=io.BytesIO(b''),preload_content=False))
        try:
            with patch.object(client._endpoint.http_session,'send',side_effect=send):
                client.put_object(Bucket='PRIVATE_BUCKET',Key='PRIVATE_KEY',Body=b'12345')
                client.head_object(Bucket='PRIVATE_BUCKET',Key='PRIVATE_KEY')
            with patch('sys.stderr',new_callable=io.StringIO) as output:
                diagnostics.emit(client,'frost_archive')
                diagnostics.emit(client,'frost_archive')
            line=output.getvalue()
            self.assertEqual(len(line.splitlines()),1)
            for value in ('requests=2','puts=1','heads=1','declared_out_body_bytes=5','responses=2'):
                self.assertIn(value,line)
            self.assertNotIn('PRIVATE',line)
            self.assertNotIn('example.test',line)
            self.assertEqual(len(sent),2)
        finally:client.close()

    def test_head_is_not_counted_as_download_and_streams_are_never_read(self):
        meter=diagnostics._Meter()
        body=Mock()
        meter.before_send(SimpleNamespace(headers={'Content-Length':'12'},body=body),event_name='before-send.s3.PutObject')
        meter.after_call(SimpleNamespace(status_code=200,headers={'content-length':'64000000'}),model=SimpleNamespace(name='HeadObject'))
        self.assertEqual(meter.out_bytes,12)
        self.assertEqual(meter.in_bytes,0)
        body.read.assert_not_called();body.seek.assert_not_called()
        meter.after_call(SimpleNamespace(status_code=200,headers={'content-length':'2048'}),model=SimpleNamespace(name='GetObject'))
        self.assertEqual(meter.in_bytes,2048)

    def test_retries_unknown_lengths_and_aws_checksum_framing_are_explicit(self):
        meter=diagnostics._Meter()
        request=SimpleNamespace(headers={'X-Amz-Decoded-Content-Length':b'17'},body=Mock())
        for _ in range(2):meter.before_send(request,event_name='before-send.s3.PutObject')
        meter.before_send(SimpleNamespace(headers={},body=Mock()),event_name='before-send.s3.PutObject')
        self.assertEqual((meter.requests,meter.puts,meter.out_bytes,meter.decoded_lengths,meter.unknown_out),(3,3,34,2,1))
        meter.after_call(SimpleNamespace(status_code=503,headers={}),model=SimpleNamespace(name='PutObject'))
        self.assertEqual((meter.failures,meter.unknown_in),(1,1))

    def test_diagnostic_failures_never_break_cleanup_or_primary_error(self):
        from app.services import frost_vault,neoscorpion_learning_vault as vault
        client=Mock()
        client._neo_archive_transfer_meter=diagnostics._Meter()
        client._neo_archive_transfer_meter.requests=1
        with patch('sys.stderr.write',side_effect=OSError('diagnostic failure')):
            frost_vault._close_client(client)
        client.close.assert_called_once()
        client.close.reset_mock()
        with patch.object(diagnostics,'emit',side_effect=RuntimeError('unexpected diagnostic failure')):
            frost_vault._close_client(client)
        client.close.assert_called_once()
        client.close.reset_mock()
        @vault._vault_operation
        def fail():
            vault._vault_scope.get()['client']=client
            raise ValueError('primary isolated error')
        with self.assertRaisesRegex(ValueError,'primary isolated error'):
            fail()
        client.close.assert_called_once()

    def test_attachment_failure_does_not_change_client_or_transport_settings(self):
        from app.services import neoscorpion_learning_vault as vault
        with patch('boto3.session.Session') as session,patch.object(diagnostics,'attach',side_effect=RuntimeError('diagnostic failure')):
            expected=session.return_value.client.return_value
            result=vault._r2_client(dict(endpoint='https://archive.example.test',region='auto',access_key='PRIVATE',secret_key='PRIVATE'))
        self.assertIs(result,expected)
        self.assertEqual(session.return_value.client.call_args.kwargs['config'].retries,{'mode':'standard','total_max_attempts':1})

    def test_empty_borrowed_and_uninstrumented_clients_are_untouched(self):
        client=SimpleNamespace()
        self.assertIs(diagnostics.attach(client),client)
        with patch('sys.stderr',new_callable=io.StringIO) as output:
            diagnostics.emit(client,'PRIVATE_OTHER_COMPONENT')
            diagnostics.emit(None,'frost_archive')
        self.assertEqual(output.getvalue(),'')
