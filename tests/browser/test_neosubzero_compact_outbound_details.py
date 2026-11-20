"""Exercise the single set of OUTBOUND forms in both desktop layouts."""
from datetime import datetime, timezone
from unittest import TestCase

from playwright.sync_api import expect

from app.extensions import db
from app.models import NeoSubZeroAuditEntry, NeoSubZeroSprayRecord, SortDateOperation
from app.services.neosubzero_spray import set_neosubzero_ucc_truck
from tests.browser import test_neosubzero_operations as fixtures


class CompactOutboundDetailsBrowserTest(TestCase):
    def test_participation_notes_and_audited_removal(self):
        for engine, layout in (("chromium", "list"), ("webkit", "visual")):
            with self.subTest(engine=engine, layout=layout):
                case = fixtures.OperationsBrowserTest()
                case.setUpClass()
                try:
                    with case.app.app_context():
                        operation = db.session.get(SortDateOperation, case.operation_id)
                        set_neosubzero_ucc_truck(operation, "Delta", 1, "UCC-831")
                        db.session.commit()
                    with getattr(case.pw, engine).launch() as browser:
                        page = browser.new_page(viewport={"width": 1366, "height": 960}, service_workers="block")
                        page.clock.install(time=datetime(2026, 10, 23, 3, 10, tzinfo=timezone.utc))
                        dialogs = []
                        page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
                        case.login(page)
                        case.ready(page, "/neosubzero/operations?mode=outbound")
                        case.layout(page, layout)
                        self.exercise(case, page, layout)
                        self.assertEqual(dialogs, [])
                finally:
                    case.tearDownClass()

    def exercise(self, case, page, layout):
        row = page.locator(f'[data-ops-row][data-mission-id="{case.flow_id}"]')
        actions = row.locator("[data-ops-quick-actions]:visible")
        for action in ("initial_contact", "toggle_configured"):
            case.submit(page, actions.locator(f'button[value="{action}"]'))
        extra = row.locator(".ops-outbound-extra")
        list_chevron = row.locator('[data-ops-expand-details][aria-expanded="false"]:visible')
        if list_chevron.count():
            list_chevron.click()
        expect(extra).to_be_visible()
        observation = "FROST PRESENT" if layout == "list" else "NERVOUS CAPTAIN"
        case.submit(page, extra.get_by_role("button", name=observation, exact=True))
        expect(extra.get_by_text(f"{observation} · RECORDED", exact=True)).to_be_visible()
        plan = row.locator('.ops-outbound-primary [name="treatment_plan"]')
        self.autosave(page, plan, lambda: plan.select_option("type_i_type_iv"))
        pass_one = extra.locator("[data-ops-participation-inline] .neosubzero-spray-pass").first
        expect(pass_one).to_be_visible()
        add = pass_one.locator(".ops-participant-add")
        add.locator("summary").click()
        add.locator('[name="truck_number"]').fill("COMPACT-TEST")
        case.submit(page, add.get_by_role("button", name="ADD TRUCK", exact=True))

        implicit = pass_one.locator("article").filter(has_text="UCC-831")
        expect(implicit.locator('[data-ops-remove-inline] button[value="remove_truck"]')).to_be_visible()
        case.submit(page, implicit.locator('[data-ops-remove-inline] button[value="remove_truck"]'))
        expect(implicit).to_have_count(0)

        notes = extra.locator('[data-ops-disclosure="notes"]')
        notes.locator("summary").click()
        notes.locator("textarea").fill("Synthetic treatment note")
        case.submit(page, notes.get_by_role("button", name="SAVE NOTES", exact=True))
        expect(notes.locator("textarea")).to_have_value("Synthetic treatment note")
        notes.locator("summary").click()

        for name, value in (("pass1_start", "22:10"), ("pass1_end", "22:15")):
            clock = row.locator(f'.ops-outbound-primary [name="{name}"]')
            clock.fill(value)
            self.autosave(page, clock, lambda: clock.press("Tab"))
        participant = pass_one.locator("article").filter(has_text="COMPACT-TEST")
        participant.locator('[name="gallons"]').fill("25")
        case.submit(page, participant.get_by_role("button", name="SAVE GALLONS", exact=True))
        expect(participant.locator('[name="gallons"]')).to_have_value("25")
        edit = participant.locator(".ops-participant-edit")
        edit.locator("summary").click()
        edit.locator('[name="driver_person_id"]').select_option(str(case.people[2]))
        edit.locator('[name="crew_confirmed"]').check()
        edit.locator('[name="correction_reason"]').fill("Verified crew correction")
        case.submit(page, edit.get_by_role("button", name="SAVE PARTICIPANT CORRECTION"))
        expect(participant.locator(".ops-participant-crew")).to_contain_text("Longname-Deicer-2")

        removal = participant.locator(".ops-participant-remove")
        removal.locator("summary").click()
        removal.locator('[name="correction_reason"]').fill("Verified participation correction")
        case.submit(page, removal.get_by_role("button", name="REMOVE", exact=True))
        expect(participant.locator(".ops-participant-retained")).to_contain_text("APPLIED GALLONS RETAINED")
        with case.app.app_context():
            record = NeoSubZeroSprayRecord.query.filter_by(truck_number_snapshot="COMPACT-TEST", pass_number=1).one()
            self.assertEqual(float(record.gallons), 25)
            self.assertFalse(record.participation_selected)
            self.assertEqual(record.driver_person_id, case.people[2])
            audit = NeoSubZeroAuditEntry.query.filter_by(action="correct_participant", mission_id=case.flow_id).one()
            self.assertEqual(audit.reason, "Verified crew correction")

        page.set_viewport_size({"width": 390, "height": 844})
        mobile_evidence = extra.locator(".ops-inline-evidence")
        expect(mobile_evidence.locator("summary").first).to_have_text("PARTICIPATION / AUDITED CORRECTIONS", use_inner_text=True)
        expect(extra.locator("[data-ops-participation-inline] .ops-compact-participation")).to_have_count(0)
        page.set_viewport_size({"width": 1366, "height": 960})
        expect(pass_one).to_be_visible()
        for name, value in (("pass2_start", "22:16"), ("pass2_end", "22:20")):
            clock = row.locator(f'.ops-outbound-primary [name="{name}"]')
            clock.fill(value)
            self.autosave(page, clock, lambda: clock.press("Tab"))
        expect(row).to_have_attribute("data-status", "fin")
        case.submit(page, actions.locator('button[value="clear"]'))
        expect(row).to_have_attribute("data-status", "radio-pending")
        row.locator("[data-ops-radio]:visible").first.click()
        expect(row.locator("[data-radio-script]")).to_be_visible()
        case.submit(page, row.get_by_role("button", name="RADIO COMPLETE", exact=True))
        expect(row).to_have_attribute("data-status", "gone")
        expect(row.locator("[data-radio-script]")).to_be_hidden()

    def autosave(self, page, field, action):
        original = field.evaluate_handle("field => field.form")
        with page.expect_response(lambda r: r.url.endswith("/operations/mutate") and r.request.method == "POST") as result:
            action()
        self.assertEqual(result.value.status, 200, result.value.text())
        page.wait_for_function("form => !form.isConnected", arg=original)
