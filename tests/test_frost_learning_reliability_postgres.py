"""Same audit contracts on an isolated loopback PostgreSQL schema."""
import unittest
from tests import test_frost_learning_reliability_audit as contracts
from tests import test_frost_phase13 as fixtures
from tests import test_neosubzero_ucc_postgres as pg
from tests import test_frost_memory_efficiency as memory


@unittest.skipUnless(pg.URI, 'Requires disposable loopback PostgreSQL')
class LearningReliabilityPostgresTest(contracts.LearningReliabilityTest):
    def setUp(self):
        self.case = fixtures.PostgresAutoTest()
        self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.gateway_id = self.case.f.gateway.id


@unittest.skipUnless(pg.URI, 'Requires disposable loopback PostgreSQL')
class MemoryEfficiencyPostgresTest(memory.MemoryEfficiencyTest):
    setUp=LearningReliabilityPostgresTest.setUp
