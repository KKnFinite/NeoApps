"""Small isolated settings workflow; never touches production controls."""
from pathlib import Path
from unittest import TestCase

from playwright.sync_api import expect
from app.extensions import db
from app.models import GatewayMembership, GatewayNodeRole, NeoNode, User
from tests.browser import test_mobile_drawer as fixtures


class IntelligenceBrowserTest(TestCase):
    def test_settings_defaults_save_rejection_and_master_readonly(self):
        evidence=Path('instance/browser-evidence/frost-intelligence');evidence.mkdir(parents=True,exist_ok=True)
        for engine in ('chromium','webkit'):
            with self.subTest(engine=engine):
                case=fixtures.MobileDrawerBrowserTest();case.setUpClass();browser=None
                try:
                    with case.app.app_context():
                        watcher=User.query.filter_by(username='drawer-watcher').one()
                        membership=GatewayMembership.query.filter_by(user_id=watcher.id).one()
                        node=NeoNode.query.filter_by(code='subzero').one()
                        GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id,node_id=node.id).one().role='master'
                        db.session.commit()
                    browser=getattr(case.pw,engine).launch()
                    page=browser.new_page(viewport={'width':1366,'height':900},service_workers='block')
                    case.login(page);case.ready(page,'/neosubzero/settings')
                    form=page.locator('form[action$="/frost-vault/intelligence-settings"]')
                    expect(form.get_by_role('switch')).to_have_count(3)
                    for toggle in form.get_by_role('switch').all():
                        expect(toggle).not_to_be_checked();expect(toggle).to_be_enabled()
                    for width in (1366,390):
                        page.set_viewport_size({'width':width,'height':900})
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                        page.screenshot(path=str(evidence/f'{engine}-{width}.png'))
                    form.locator('[name="learn_from_live_operations"]').check()
                    form.locator('[name="automatic_retraining"]').check()
                    with page.expect_navigation():form.get_by_role('button',name='SAVE FROST INTELLIGENCE').click()
                    expect(form.locator('[name="automatic_retraining"]')).to_be_checked()
                    form.locator('[name="operational_predictions"]').check()
                    form.locator('[name="confirm_operational"]').check()
                    with page.expect_navigation():form.get_by_role('button',name='SAVE FROST INTELLIGENCE').click()
                    expect(page.get_by_text('No independently validated, explicitly approved compatible operational FROST model is available. Historical-model research is not operational approval.',exact=True).first).to_be_visible()
                    expect(form.locator('[name="operational_predictions"]')).not_to_be_checked()
                    expect(form.locator('[name="automatic_retraining"]')).to_be_checked()
                    other=browser.new_page(viewport={'width':390,'height':844},service_workers='block')
                    case.login(other,'drawer-watcher');case.ready(other,'/neosubzero/settings')
                    controls=other.locator('form[action$="/frost-vault/intelligence-settings"]')
                    for toggle in controls.get_by_role('switch').all():expect(toggle).to_be_disabled()
                    expect(controls.get_by_role('button',name='SAVE FROST INTELLIGENCE')).to_have_count(0)
                    expect(controls.locator('[name="automatic_retraining"]')).to_be_checked()
                finally:
                    if browser:browser.close()
                    case.tearDownClass()
