"""One isolated rendered NeoRain board fixture for W5 presentation contracts."""
from datetime import datetime
from unittest.mock import patch
from app.extensions import db
from app.services.google_rain_integration_mode import NEO_ONLY,set_rain_integration_mode
from tests import test_neorain_outbound as fixtures


def boards(test):
    host=fixtures.NeoRainOutboundTest();host.setUp();test.addCleanup(host.tearDown)
    operation=host._operation()
    host._mission(operation,'UPS0910','SDF',planned=datetime(2026,8,30,6),tail='N919UP')
    host._mission(operation,'UPS0911','RFD',planned=datetime(2026,8,30,5),mission_type='arrival',tail='N920UP')
    editor=host._user('w5-board-editor','simulator');host._login(editor)
    set_rain_integration_mode(host.gateway,operation.sort_name,NEO_ONLY);db.session.commit()
    with patch('app.neonodes.neorain.routes.current_neorain_outbound_operation',return_value=operation):
        responses={name:host.client.get('/neorain/'+name) for name in ('inbound','outbound')}
    for response in responses.values():test.assertEqual(response.status_code,200)
    return host,responses
