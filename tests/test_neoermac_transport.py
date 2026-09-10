"""Transport measurements use isolated SQLite fixtures and fresh requests."""
import json
import unittest
from datetime import time
from urllib.parse import urlencode
import re
from unittest.mock import patch

from app.extensions import db
from app.models import Gateway, GatewayMembership, SortDateMission
from tests import test_neoermac_read_budgets as fixtures


class NeoErmacTransportTest(unittest.TestCase):
    setUp = fixtures.NeoErmacReadBudgetTest.setUp
    tearDown = fixtures.NeoErmacReadBudgetTest.tearDown
    measure = fixtures.NeoErmacReadBudgetTest.measure
    _add_master_departure = fixtures.NeoErmacReadBudgetTest._add_master_departure
    _add_operation_departure = fixtures.NeoErmacReadBudgetTest._add_operation_departure
    _assign_lineup_destination = fixtures.NeoErmacReadBudgetTest._assign_lineup_destination
    _login_approved_user = fixtures.NeoErmacReadBudgetTest._login_approved_user

    def profile(self):
        result = {}
        response, metrics = self.measure('/neoermac/building-lineup/state?revision=old')
        result['lineup_changed'] = dict(metrics, bytes=len(response.data))
        revision = response.json['revision']
        if 'common_version' in response.json:
            common = response.json['common_version']
            incremental, _ = self.measure('/neoermac/building-lineup/state?'+urlencode({'revision':'old', 'common':common}))
            result['lineup_incremental_bytes'] = len(incremental.data)
        response, metrics = self.measure('/neoermac/building-lineup/state?revision='+revision)
        result['lineup_unchanged'] = dict(metrics, bytes=len(response.data))
        response, _ = self.measure('/neoermac/door-view?door=D1')
        result['door_html_bytes'] = len(response.data)
        self._add_operation_departure('UPS502', 'ONT', tail='N502UP')
        for count in (2, 50):
            if count == 50:
                for i in range(3, 51):
                    self._add_operation_departure(f'UPS{500+i}', f'D{i:02}', tail=f'N{500+i}UP')
            db.session.commit()
            response, metrics = self.measure('/neoermac/view-outbound/state?revision=old')
            result[f'outbound_{count}'] = dict(metrics, bytes=len(response.data))
            if 'row_manifest' in response.json:
                manifest = response.json['row_manifest']
                mission = SortDateMission.query.filter_by(sort_date_operation_id=self.operation_id, flight_number='UPS501').one()
                mission.actual_pure_pull_time_local = time(1, count)
                db.session.commit()
                query = urlencode({'revision':response.json['revision'], 'rows':json.dumps(manifest, separators=(',',':'))})
                delta, delta_metrics = self.measure('/neoermac/view-outbound/state?'+query)
                result[f'outbound_delta_{count}'] = dict(delta_metrics, bytes=len(delta.data),
                    request_query_bytes=len(query), replaced_rows=len(delta.json['row_delta']['rows'])*2)
        return result

    def test_transport_budgets(self):
        result = self.profile()
        self.assertEqual(result['lineup_unchanged']['SELECT'], 8)
        self.assertEqual(result['lineup_unchanged']['bytes'], 367)
        self.assertEqual(result['lineup_unchanged']['select_tables']['gateway_sort_matrix'], 2)
        self.assertEqual(result['lineup_changed']['SELECT'], 11)
        self.assertLess(result['lineup_incremental_bytes'], 16404)
        self.assertLess(result['door_html_bytes'], 53000)  # was 90,429
        for count, byte_budget in ((2, 3300), (50, 5200)):
            metric = result[f'outbound_delta_{count}']
            self.assertEqual(metric['SELECT'], 14)  # No extra DB reads for transport.
            self.assertLess(metric['bytes'], byte_budget)
            self.assertLess(metric['request_query_bytes'], 3500)
            self.assertEqual(metric['replaced_rows'], 2)  # One desktop + one mobile.
        for value in result.values():
            if isinstance(value, dict):
                self.assertEqual([value[k] for k in ('INSERT','UPDATE','DELETE','commits')], [0]*4)

    def test_outbound_deltas_reconcile_to_full_snapshot_and_preserve_identity_order(self):
        second = self._add_operation_departure('UPS502', 'SDF', tail='N502UP')
        second_id = second.id
        db.session.commit()
        initial, _ = self.measure('/neoermac/view-outbound/state?revision=old')
        manifest = initial.json['row_manifest']
        self.assertIn(f'm{second_id}', manifest['rows'])  # Repeated destinations stay distinct.
        self.assertIn('missing:PHX', manifest['rows'])
        second = db.session.get(SortDateMission, second_id)
        second.actual_mix_pull_time_local = time(2, 45)
        second.pure_pull_time_local = time(0, 30)  # Change canonical order as well.
        db.session.commit()
        delta, _ = self.measure('/neoermac/view-outbound/state?'+urlencode({'revision':initial.json['revision'], 'rows':json.dumps(manifest)}))
        self.assertEqual(list(delta.json['row_delta']['rows']), [f'm{second_id}'])
        changed = delta.json['row_delta']['rows'][f'm{second_id}']
        self.assertIn('02:45', changed['desktop'])
        self.assertIn('02:45', changed['mobile'])
        self.assertIn('N502UP', changed['desktop'])
        full, _ = self.measure('/neoermac/view-outbound/state?revision=old')
        self.assertEqual(delta.json['row_manifest'], full.json['row_manifest'])
        for html in changed.values():
            self.assertIn(html, full.json['content_html'])
        expected_order = re.findall(r'data-neoermac-outbound-row data-outbound-key="([^"]+)"', full.json['content_html'])
        self.assertEqual(delta.json['row_delta']['order'], expected_order)
        # Removal and reappearance of a missing-mission destination use distinct keys.
        self.gateway = db.session.get(Gateway, self.gateway_id)
        third = self._add_operation_departure('UPS503', 'PHX', tail='N503UP')
        third_id = third.id
        db.session.delete(db.session.get(SortDateMission, second_id))
        db.session.commit()
        next_response, _ = self.measure('/neoermac/view-outbound/state?'+urlencode({'revision':'old', 'rows':json.dumps(delta.json['row_manifest'])}))
        self.assertNotIn(f'm{second_id}', next_response.json['row_delta']['order'])
        self.assertNotIn('missing:PHX', next_response.json['row_delta']['order'])
        self.assertIn(f'm{third_id}', next_response.json['row_delta']['order'])

    def test_outbound_fallbacks_and_unchanged_remain_read_only(self):
        response, _ = self.measure('/neoermac/view-outbound/state?revision=old')
        manifest = response.json['row_manifest']
        for token in ('not-json', '[]', '{}', json.dumps(dict(manifest, scope='other-sort')), 'x'*16001):
            with self.subTest(token=token[:30]):
                snapshot, _ = self.measure('/neoermac/view-outbound/state?'+urlencode({'revision':'old', 'rows':token}))
                self.assertIn('content_html', snapshot.json)
                self.assertNotIn('row_delta', snapshot.json)
        unchanged, metrics = self.measure('/neoermac/view-outbound/state?'+urlencode({'revision':response.json['revision'], 'rows':json.dumps(manifest)}))
        self.assertFalse(unchanged.json['changed'])
        self.assertNotIn('row_manifest', unchanged.json)
        self.assertNotIn('content_html', unchanged.json)
        self.assertEqual([metrics[k] for k in ('INSERT','UPDATE','DELETE','commits')], [0]*4)
        with patch('app.neonodes.neoermac.routes.current_view_outbound_operation', return_value=None):
            empty, _ = self.measure('/neoermac/view-outbound/state?'+urlencode({'revision':'old', 'rows':json.dumps(manifest)}))
            self.assertIn('content_html', empty.json)
            self.assertIn('NO CURRENT SORT MISSION', empty.json['content_html'])

    def test_lineup_common_snapshot_omission_and_master_change(self):
        from app.models import MasterFlightSchedule
        first, _ = self.measure('/neoermac/building-lineup/state?revision=old')
        common = first.json['common_version']
        next_response, _ = self.measure('/neoermac/building-lineup/state?'+urlencode({'revision':'old', 'common':common}))
        self.assertEqual(next_response.json['state']['slots'], first.json['state']['slots'])
        self.assertEqual(len(next_response.json['state']['slots']), 96)
        self.assertNotIn('destination_choices', next_response.json['state'])
        self.assertNotIn('pull_times', next_response.json['state'])
        MasterFlightSchedule.query.filter_by(destination='PHX').one().destination = 'BOS'
        db.session.commit()
        updated, _ = self.measure('/neoermac/building-lineup/state?'+urlencode({'revision':first.json['revision'], 'common':common}))
        self.assertTrue(updated.json['changed'])
        self.assertIn('BOS', updated.json['state']['destination_choices'])
        self.assertNotEqual(updated.json['common_version'], common)

    def test_door_controller_is_cacheable_with_explicit_escaped_config(self):
        from pathlib import Path
        response, _ = self.measure('/neoermac/door-view?door=D1')
        html = response.get_data(as_text=True)
        config = json.loads(re.search(r'id="neoermac-door-config">(.*?)</script>', html, re.S).group(1))
        self.assertEqual(set(config), {'refresh','canEdit','doorViewUrl','doorTabAlerts'})
        self.assertTrue(config['canEdit'])
        self.assertIn('neoermac_door_live.js?v=ermac-transport-1', html)
        self.assertNotIn('const refreshState =', html)
        source = Path('app/static/js/neoermac_door_live.js').read_text()
        self.assertNotIn('{{', source)
        self.assertIn('if (!JSON.parse(document.getElementById("neoermac-door-config").textContent).canEdit) return;', source)

    def test_large_lineup_catalog_is_not_resent_when_common_data_is_unchanged(self):
        self.gateway = db.session.get(Gateway, self.gateway_id)
        for i in range(3, 50):
            self._add_master_departure(f'UPS7{i:02}', f'X{i:02}')
        db.session.commit()
        full, _ = self.measure('/neoermac/building-lineup/state?revision=old')
        incremental, _ = self.measure('/neoermac/building-lineup/state?'+urlencode({'revision':'old', 'common':full.json['common_version']}))
        self.assertEqual(len(full.json['state']['destination_choices']), 50)
        self.assertEqual(incremental.json['state']['slots'], full.json['state']['slots'])
        self.assertGreater(len(full.data)-len(incremental.data), 1800)

    def test_lightweight_lineup_still_rejects_revoked_gateway_access(self):
        membership = GatewayMembership.query.filter_by(gateway_id=self.gateway_id).one()
        membership.is_active = False
        db.session.commit()
        response, metric = self.measure('/neoermac/building-lineup/state?revision=old')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/access-pending'))
        self.assertEqual([metric[k] for k in ('INSERT','UPDATE','DELETE','commits')], [0]*4)
