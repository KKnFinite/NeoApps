"""Attribute the mandatory vendor boundary separately from operational budgets."""
import re
from collections import Counter
from contextlib import contextmanager
from unittest.mock import patch


@contextmanager
def measure_authority_reads():
    """Count real queries inside the shared scoped-navigation authority lookup."""
    from sqlalchemy import event
    from app.extensions import db
    from app.services import account_authority

    reads = []
    original = account_authority.read_authority

    def record(_conn, _cursor, statement, *_):
        reads.append(statement)

    def measured(*args, **kwargs):
        engine = db.engine
        event.listen(engine, 'before_cursor_execute', record)
        try:
            return original(*args, **kwargs)
        finally:
            event.remove(engine, 'before_cursor_execute', record)

    with patch.object(account_authority, 'read_authority', measured):
        yield reads


def is_vendor_identity_read(statement):
    # A gateway or personnel projection can include a correlated vendor
    # exclusion. That entire SELECT belongs to the operational budget.
    return bool(re.match(
        r'^\s*select\s+neoscorpion_vendor_accounts\.user_id\b[^()]*'
        r'\bfrom\s+neoscorpion_vendor_accounts\s+where\s+neoscorpion_vendor_accounts\.user_id\s*=',
        statement, re.I | re.S,
    ))


def assert_service_read_budget(test, statements, maximum):
    """Service-level budgets include one independently required vendor identity check."""
    boundary = [statement for statement in statements if is_vendor_identity_read(statement)]
    test.assertEqual(len(boundary), 1)
    test.assertLessEqual(len(statements) - len(boundary), maximum)


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
        if is_vendor_identity_read(statement):
            self.vendor_reads.append(statement)
        else:
            super().append(entry)

    def assert_boundary(self, test):
        test.assertEqual(len(self.vendor_reads), 1,
                         'Private request must check vendor identity exactly once')
        test.assertTrue(self.vendor_reads[0].lstrip().upper().startswith('SELECT '))
        test.assertRegex(self.vendor_reads[0].lower(),
                         r'where\s+neoscorpion_vendor_accounts\.user_id\s*=')
