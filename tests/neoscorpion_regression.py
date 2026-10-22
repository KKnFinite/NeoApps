"""Curated, isolated regression groups without imported-fixture rediscovery.

Run: python -m tests.neoscorpion_regression core --json instance/scorpion-core.json
     python -m tests.neoscorpion_regression postgres
     python -m tests.neoscorpion_regression browser --repeat 2

PostgreSQL requires NEOSTAFFING_TEST_POSTGRES_URL pointing to the existing
disposable loopback database. Repetition records every failure; it is NOT retry.
JavaScript: node --test tests/js/neoscorpion*.test.{js,cjs}
Expand both extensions in PowerShell (including the vendor .cjs regression).
No production connection, credentials or data is required.
"""
import argparse
import importlib
import inspect
import json
import os
from pathlib import Path
import unittest
from urllib.parse import urlsplit


GROUPS = {
    'core': tuple('tests.test_neoscorpion_'+name for name in (
        'dispatch_planning','apu_fuel','dispatch_details','status_audit',
        'lifecycle_audit','fueler_submission','resume','truck_queue','continuations',
        'operational_timeline','resource_prof','top_off_settlement','future_inventory','package1','package2',
        'row_height','ready_pulse','assets_workspace','vendor_workflow','uplift_defuel','direct_entry')),
    'postgres': tuple('tests.test_neoscorpion_'+name+'_postgres' for name in (
        'segments','lifecycle','submission','truck_queue','vendor','package1','package2','direct_entry')),
    'browser': tuple('tests.browser.test_neoscorpion_'+name for name in (
        'package1','package2','package3','fueler_redesign','submission','resume',
        'lifecycle_visits','truck_queue','top_off_sent','details_layout',
        'rolldown','assets_workspace','dispatch_profile','dispatch_collapse',
        'spear_readiness','spear_occupancy','shared_fuel_screen')),
    'navigation': ('tests.browser.test_mobile_drawer',),
}


def defined_cases(module):
    """Do not run imported TestCase classes as accidental duplicate tests."""
    return [value for _,value in inspect.getmembers(module,inspect.isclass)
            if issubclass(value,unittest.TestCase) and value.__module__==module.__name__]


def suite_for(group):
    suite=unittest.TestSuite()
    for name in GROUPS[group]:
        module=importlib.import_module(name)
        for case in defined_cases(module):
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    return suite


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('group',choices=GROUPS)
    parser.add_argument('--repeat',type=int,default=1)
    parser.add_argument('--json',type=Path)
    args=parser.parse_args()
    if args.repeat<1:parser.error('--repeat must be positive')
    if args.group=='postgres':
        url=urlsplit(os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL',''))
        if url.hostname not in ('localhost','127.0.0.1') or not url.path.startswith('/neostaffing_test_'):
            parser.error('postgres requires an explicitly configured disposable loopback test database')
    outcomes=[]
    for iteration in range(1,args.repeat+1):
        result=unittest.TextTestRunner(verbosity=2).run(suite_for(args.group))
        outcomes.append(dict(iteration=iteration,tests=result.testsRun,
            failures=[dict(test=str(test),trace=trace) for test,trace in result.failures],
            errors=[dict(test=str(test),trace=trace) for test,trace in result.errors],
            skipped=[dict(test=str(test),reason=reason) for test,reason in result.skipped],
            passed=result.wasSuccessful()))
        if args.json:
            args.json.write_text(json.dumps(dict(group=args.group,runs=outcomes),indent=2),encoding='utf8')
    return 0 if all(row['passed'] for row in outcomes) else 1


if __name__=='__main__':
    raise SystemExit(main())
