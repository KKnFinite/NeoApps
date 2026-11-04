"""Attribute the mandatory vendor boundary separately from operational budgets."""
import re
from collections import Counter


class RequestSql(list):
    """Domain statements plus separately asserted mandatory boundary reads.

    No authentication is mocked. Only the exact vendor lookup is separated;
    all other reads/writes remain in the original operational budget. Commit
    indexes therefore keep their original domain-statement meaning too.
    """
    def __init__(self):
        super().__init__()
        self.vendor_reads = []
        self.permission_reads = Counter()

    def append(self, entry, parameters=None):
        statement = entry[0] if isinstance(entry, tuple) else entry
        parameters = entry[1] if isinstance(entry, tuple) else parameters
        if re.search(r'\bfrom\s+permission_rules\b', statement, re.I) and parameters:
            values=parameters.values() if isinstance(parameters,dict) else parameters
            self.permission_reads.update(value for value in values if isinstance(value,str))
        if re.search(r'\bfrom\s+neoscorpion_vendor_accounts\b', statement, re.I):
            self.vendor_reads.append(statement)
        else:
            super().append(entry)

    def assert_boundary(self, test):
        test.assertEqual(len(self.vendor_reads), 1,
                         'Private request must check vendor identity exactly once')
        test.assertTrue(self.vendor_reads[0].lstrip().upper().startswith('SELECT '))
        test.assertRegex(self.vendor_reads[0].lower(),
                         r'where\s+neoscorpion_vendor_accounts\.user_id\s*=')
