"""The curated runner must not duplicate imported fixtures or hide failures."""
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
from tests.neoscorpion_regression import defined_cases,main,suite_for


class RegressionRunnerTest(unittest.TestCase):
    def test_only_cases_defined_in_the_module_are_collected(self):
        module=types.ModuleType('synthetic_regression_module')
        imported=type('ImportedFixture',(unittest.TestCase,),{'__module__':'elsewhere'})
        local=type('LocalTests',(unittest.TestCase,),{'__module__':module.__name__,
            'test_one':lambda self:None})
        module.ImportedFixture=imported;module.LocalTests=local
        self.assertEqual(defined_cases(module),[local])
        with patch('tests.neoscorpion_regression.GROUPS',{'fixture':('synthetic_regression_module',)}), \
             patch('tests.neoscorpion_regression.importlib.import_module',return_value=module):
            suite=suite_for('fixture')
        self.assertEqual(suite.countTestCases(),1)

    def test_existing_regression_groups_have_unique_test_ids(self):
        def flatten(suite):
            for item in suite:
                if isinstance(item,unittest.TestSuite):yield from flatten(item)
                else:yield item.id()
        for group in ('core','browser','navigation','postgres'):
            ids=list(flatten(suite_for(group)))
            self.assertGreater(len(ids),0)
            self.assertEqual(len(ids),len(set(ids)),group)

    def test_repeat_retains_failed_subtest_even_if_later_run_passes(self):
        class Failing(unittest.TestCase):
            def test_one(self):
                with self.subTest(visit=2):
                    self.assertEqual(1,2)

        with tempfile.TemporaryDirectory() as folder:
            report=Path(folder)/'results.json'
            suites=[unittest.defaultTestLoader.loadTestsFromTestCase(Failing),
                    unittest.TestSuite([unittest.FunctionTestCase(lambda:None)])]
            with patch('sys.argv',['regression','core','--repeat','2','--json',str(report)]), \
                 patch('tests.neoscorpion_regression.suite_for',side_effect=suites), \
                 redirect_stderr(io.StringIO()):
                self.assertEqual(main(),1)
            runs=json.loads(report.read_text(encoding='utf8'))['runs']
        self.assertEqual(len(runs),2)
        self.assertFalse(runs[0]['passed'])
        self.assertIn('visit=2',runs[0]['failures'][0]['test'])
        self.assertTrue(runs[1]['passed'])

    def test_postgres_rejects_non_disposable_or_remote_database(self):
        for url in ('','postgresql://postgres@localhost/neoapps',
                    'postgresql://postgres@production.example/neostaffing_test_bad'):
            with self.subTest(url=url), \
                 patch('sys.argv',['regression','postgres']), \
                 patch.dict('os.environ',{'NEOSTAFFING_TEST_POSTGRES_URL':url}), \
                 patch('tests.neoscorpion_regression.suite_for') as collect, \
                 redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    main()
                self.assertEqual(stopped.exception.code,2)
                collect.assert_not_called()
