"""Rendered Portal navigation preserves approval and preference boundaries."""
from unittest import TestCase
from html.parser import HTMLParser
from xml.etree.ElementTree import Element,SubElement
from app.extensions import db
from app.models import PortalAppAccess,AccountAuthorizationState,AccountScopedRole,AccountSecurityEvent
from app.models.account_preference import AccountPreference
from tests import test_accounts_workspace as workspace


class RenderedTree(HTMLParser):
    """Use the standard library; no new runtime/test dependency."""
    VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

    def __init__(self,html):
        super().__init__();self.root=Element('document');self.stack=[self.root];self.feed(html)

    def handle_starttag(self,tag,attrs):
        node=SubElement(self.stack[-1],tag,{k:v or '' for k,v in attrs})
        if tag not in self.VOID:self.stack.append(node)

    def handle_endtag(self,tag):
        for index in range(len(self.stack)-1,0,-1):
            if self.stack[index].tag==tag:
                del self.stack[index:];break

    def handle_data(self,data):
        self.stack[-1].text=(self.stack[-1].text or '')+data


def by_class(root,name):
    return [node for node in root.iter() if name in node.get('class','').split()]


class PortalNavigationTest(TestCase):
    setUp=workspace.AccountsWorkspaceTest.setUp
    tearDown=workspace.AccountsWorkspaceTest.tearDown
    login=workspace.AccountsWorkspaceTest.login

    def portal(self,user):
        self.login(user)
        response=self.client.get('/portal')
        self.assertEqual(response.status_code,200)
        return RenderedTree(response.get_data(as_text=True)).root

    def test_artwork_and_launchers_have_no_floating_utilities_or_duplicate_header(self):
        html=self.portal(self.admin)
        launcher=by_class(html,'portal-launcher')[0]
        self.assertEqual(by_class(html,'aa-apps-extra'),[])
        self.assertEqual([a.get('href') for a in launcher.iter('a')],['/rfd'])
        self.assertIsNotNone(launcher.find('.//picture/img[@alt="NeoApps Portal"]'))
        header=by_class(html,'portal-topbar')[0]
        self.assertEqual(len(by_class(html,'topbar')),1)
        self.assertEqual(len(header.findall('.//a[@href="/my-account"]')),1)
        self.assertEqual(len(header.findall('.//a[@href="/accounts"]')),1)
        self.assertNotIn('App switcher',' '.join(header.itertext()))
        drawer=html.find('.//*[@data-drawer-view="menu"]')
        self.assertEqual(len(drawer.findall('.//a[@href="/my-account"]')),1)
        self.assertEqual(len(drawer.findall('.//a[@href="/accounts"]')),1)
        self.assertTrue(any('portal_navigation.css' in link.get('href','') for link in html.iter('link')))

    def test_regular_account_does_not_gain_administration_or_unauthorized_staffing(self):
        access=PortalAppAccess(user_id=self.target.id,app_code='neostaffing',role='operator',status='approved',is_active=True)
        db.session.add(access);db.session.commit()
        for status,active,visible in (('approved',True,True),('pending',True,False),('denied',True,False),('approved',False,False)):
            with self.subTest(status=status,active=active):
                access.status=status;access.is_active=active;db.session.commit()
                html=self.portal(self.target)
                self.assertEqual(bool(html.findall('.//*[@data-portal-app="neostaffing"]')),visible)
                self.assertEqual(html.findall('.//a[@href="/accounts"]'),[])
                self.assertEqual(by_class(html,'aa-apps-extra'),[])

    def test_cryocheck_is_opt_in_external_navigation_outside_artwork(self):
        html=self.portal(self.admin)
        self.assertEqual(html.findall('.//a[@href="https://cryocheck.onrender.com"]'),[])
        db.session.add(AccountPreference(user_id=self.admin.id,show_cryocheck=True));db.session.commit()
        html=self.portal(self.admin)
        links=html.findall('.//a[@href="https://cryocheck.onrender.com"]')
        self.assertEqual(len(links),2) # one desktop header and one mobile MENU
        self.assertEqual(by_class(html,'portal-launcher')[0].findall('.//a[@href="https://cryocheck.onrender.com"]'),[])
        for link in links:
            self.assertEqual(link.get('target'),'_blank')
            self.assertIn('noopener',link.get('rel','').split());self.assertIn('noreferrer',link.get('rel','').split())

    def test_app_access_stays_in_my_account_and_cutover_is_not_activated_by_gets(self):
        self.portal(self.admin)
        response=self.client.get('/my-account')
        html=RenderedTree(response.get_data(as_text=True)).root
        self.assertIsNotNone(html.find('.//*[@id="app-access"]'))
        self.assertIn('App switcher',' '.join(html.itertext()))
        self.assertEqual(AccountAuthorizationState.query.count(),0)
        self.assertEqual(AccountScopedRole.query.count(),0)
        self.assertEqual(AccountSecurityEvent.query.count(),0)
