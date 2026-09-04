import json
from pathlib import Path
import re
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (
    PROJECT_ROOT / "integrations" / "google_motherbrain" / "apps_script"
)
CODE_PATH = PACKAGE_ROOT / "Code.gs"
MANIFEST_PATH = PACKAGE_ROOT / "appsscript.json"
README_PATH = PACKAGE_ROOT / "README.md"


class GoogleMotherBrainAppsScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = CODE_PATH.read_text(encoding="utf-8")
        cls.manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
        cls.manifest = json.loads(cls.manifest_text)
        cls.readme = README_PATH.read_text(encoding="utf-8")

    def test_source_package_exists(self):
        self.assertTrue(CODE_PATH.is_file())
        self.assertTrue(MANIFEST_PATH.is_file())
        self.assertTrue(README_PATH.is_file())

    def test_source_is_locked_to_exact_workbook_and_endpoint(self):
        self.assertIn(
            "10Il5VRW-O3-T9RhrVPvvDphUh03vD-heMbqJwxxmyDg",
            self.code,
        )
        self.assertIn("RFD-N-sim: Mother Brain", self.code)
        self.assertIn("America/Chicago", self.code)
        self.assertIn(
            "https://neoapps.onrender.com/integrations/"
            "google-motherbrain/current-sort/preview",
            self.code,
        )
        self.assertIn("spreadsheet.getId()", self.code)
        self.assertIn("spreadsheet.getName()", self.code)
        self.assertIn("spreadsheet.getSpreadsheetTimeZone()", self.code)

    def test_exact_workbook_ranges_are_committed(self):
        expected_ranges = (
            "Inbound!A4:G13",
            "Inbound!A15:G100",
            "Inbound!P4:P100",
            "Outbound!A4:G13",
            "Outbound!A15:G100",
            "Outbound!P4:P100",
            "Outbound!W4:Z100",
            "Parking Plan!BG3:BH100",
        )
        for a1_range in expected_ranges:
            with self.subTest(a1_range=a1_range):
                self.assertIn(a1_range, self.code)

    def test_inbound_h2_is_the_only_operation_sort_date_source(self):
        self.assertIn("sortDate: 'Inbound!H2'", self.code)
        self.assertIn("sort_date: sortDate", self.code)
        self.assertNotIn("Outbound!H2", self.code)
        self.assertIn("formatRequiredSheetDate_", self.code)

    def test_date_and_text_reads_follow_the_sheet_contract(self):
        self.assertIn("getValues()", self.code)
        self.assertIn("getDisplayValues()", self.code)
        self.assertIn("Utilities.formatDate(rawValue, timezone, 'yyyy-MM-dd')", self.code)
        self.assertIn("submitted_at: new Date().toISOString()", self.code)

    def test_manual_and_alp_row_filters_are_present(self):
        self.assertIn("Boolean(tailNumber || (cancelled && flightNumber))", self.code)
        self.assertIn("values.slice(1, 7).some(Boolean)", self.code)
        self.assertIn("normalized === 'CNL' || normalized === 'CANCELLED'", self.code)
        self.assertIn("isAlpHeaderRow_(values)", self.code)

    def test_tail_swaps_and_parking_keep_required_source_values(self):
        self.assertIn("if (!values[2] && !values[3])", self.code)
        self.assertIn("scorpion_unlock: values[3]", self.code)
        self.assertIn("SpreadsheetApp.flush()", self.code)
        self.assertIn("position: trimDisplayedValue_(displayed[1])", self.code)
        self.assertNotIn("deduplicate", self.code.lower())

    def test_bound_menu_contains_preview_only(self):
        self.assertRegex(self.code, r"function\s+onOpen\s*\(")
        self.assertIn("createMenu('NeoApps')", self.code)
        self.assertIn("PREVIEW CURRENT SORT IN NEO", self.code)
        self.assertIn("previewCurrentSortInNeo", self.code)
        self.assertNotRegex(self.code, r"\.addItem\(\s*['\"]\s*APPLY")

    def test_token_comes_only_from_script_properties(self):
        self.assertIn("PropertiesService.getScriptProperties().getProperty", self.code)
        self.assertIn("NEO_GOOGLE_MOTHERBRAIN_IMPORT_TOKEN", self.code)
        self.assertRegex(
            self.code,
            r"['\"]X-Neo-Integration-Token['\"]\s*:\s*token",
        )
        self.assertNotRegex(
            self.code,
            r"['\"]X-Neo-Integration-Token['\"]\s*:\s*['\"]",
        )
        self.assertNotIn("NEO_GOOGLE_MOTHERBRAIN_IMPORT_TOKEN=", self.code)
        self.assertNotRegex(self.code, r"return\s+token\s*;")
        self.assertNotRegex(
            self.code,
            r"(?i)\b(?:token|secret)\s*=\s*['\"][^'\"]{12,}['\"]",
        )

    def test_request_uses_document_lock_and_one_explicit_fetch(self):
        self.assertIn("LockService.getDocumentLock()", self.code)
        self.assertIn("lock.tryLock", self.code)
        self.assertIn("finally", self.code)
        self.assertIn("lock.releaseLock()", self.code)
        self.assertEqual(self.code.count("UrlFetchApp.fetch("), 1)
        self.assertIn("method: 'post'", self.code)
        self.assertIn("contentType: 'application/json'", self.code)
        self.assertIn("payload: JSON.stringify(envelope)", self.code)
        self.assertIn("muteHttpExceptions: true", self.code)
        self.assertIn("followRedirects: false", self.code)

    def test_source_has_no_logs_or_automatic_triggers(self):
        self.assertNotIn("Logger.log", self.code)
        self.assertNotIn("console.log", self.code)
        self.assertNotRegex(self.code, r"function\s+onEdit\s*\(")
        self.assertNotIn("ScriptApp", self.code)

    def test_source_has_no_prohibited_spreadsheet_writes(self):
        prohibited_methods = (
            "setValue",
            "setValues",
            "clear",
            "clearContent",
            "appendRow",
            "insertRow",
            "insertSheet",
            "deleteRow",
            "deleteSheet",
            "moveTo",
            "copyTo",
            "sort",
            "protect",
        )
        for method in prohibited_methods:
            with self.subTest(method=method):
                self.assertNotRegex(self.code, rf"\.{method}\s*\(")

    def test_manifest_is_valid_and_minimal(self):
        self.assertEqual(self.manifest["runtimeVersion"], "V8")
        self.assertEqual(self.manifest["timeZone"], "America/Chicago")
        self.assertEqual(
            self.manifest["oauthScopes"],
            [
                "https://www.googleapis.com/auth/spreadsheets.currentonly",
                "https://www.googleapis.com/auth/script.external_request",
            ],
        )
        self.assertEqual(
            self.manifest["urlFetchWhitelist"],
            ["https://neoapps.onrender.com/"],
        )
        self.assertEqual(
            set(self.manifest),
            {"timeZone", "runtimeVersion", "oauthScopes", "urlFetchWhitelist"},
        )

    def test_manifest_has_no_trigger_or_deployment_configuration(self):
        prohibited_keys = {"addOns", "executionApi", "triggers", "webapp"}
        self.assertTrue(prohibited_keys.isdisjoint(self.manifest))
        self.assertNotIn("drive", " ".join(self.manifest["oauthScopes"]).lower())

    def test_readme_documents_manual_install_enablement_and_rollback(self):
        required_text = (
            "Extensions -> Apps Script",
            "Project Settings -> Script Properties",
            "NEO_GOOGLE_MOTHERBRAIN_IMPORT_TOKEN",
            "GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=true",
            "GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=false",
            "PREVIEW CURRENT SORT IN NEO",
            "There is no Apply action",
            "There is no automatic trigger",
            "No data rollback is required",
        )
        for text in required_text:
            with self.subTest(text=text):
                self.assertIn(text, self.readme)


if __name__ == "__main__":
    unittest.main()
