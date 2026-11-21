"""Round-trip authority changes using only a disposable loopback PostgreSQL schema."""
import unittest
from unittest.mock import patch

from sqlalchemy import select

from app.extensions import db
from app.models import (
    NeoSektorBallmatCount, NeoSektorBallmatWaveCount, NeoSektorOpenBayState,
    NeoSektorSortState, NeoSektorWaveState,
)
from app.services import neosektor_sheets_compat as sheets
from app.services.neosektor_live_counts import native_uld_summary
from tests import test_gateway_shell as fixtures
from tests.neoraiden_fixtures import record_counts
from tests.test_neoraiden import seed_sort
from tests.test_sektor_ermac_performance_postgres import URI, postgres


@unittest.skipUnless(URI, "Requires disposable loopback PostgreSQL")
class SektorRaidenMigrationTest(unittest.TestCase):
    def setUp(self):
        postgres(self, fixtures, lambda: fixtures.GatewayShellTest.setUp(self))

    tearDown = fixtures.GatewayShellTest.tearDown

    def test_cutover_and_rollback_preserve_native_evidence(self):
        op = seed_sort(self.gateway)
        record_counts(self.gateway, op)
        models = (NeoSektorSortState, NeoSektorWaveState, NeoSektorBallmatWaveCount,
                  NeoSektorBallmatCount, NeoSektorOpenBayState)

        def rows():
            return [db.session.execute(select(m.__table__).order_by(m.id)).all() for m in models]

        evidence = rows()
        path = f"/neoraiden/state?gateway=RFD&operation_id={op.id}"
        with patch.object(sheets, "_get_worksheet", side_effect=AssertionError("Google worksheet access")), \
             patch("requests.sessions.Session.request", side_effect=AssertionError("external HTTP")):
            for mode in (sheets.NEO_PRIMARY_GOOGLE_MIRROR, sheets.NEO_ONLY, sheets.NEO_PRIMARY_GOOGLE_MIRROR):
                sheets.change_neosektor_integration_mode(self.gateway, mode)
                db.session.expire_all()
                self.assertEqual(sheets.neosektor_integration_mode(self.gateway), mode)
                self.assertFalse(sheets.neosektor_google_mirror_writes_enabled(self.gateway))
                native = native_uld_summary(self.gateway, op.sort_date, op.sort_name)
                self.assertEqual([w["west"] for w in native["waves"]], [0, 7])
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("E 2 · W 3", response.json["html"])
                self.assertEqual(rows(), evidence)
