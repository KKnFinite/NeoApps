"""Real canonical Org Chart: desktop/mobile geometry, scrolling and selection."""
from datetime import date
import unittest
from playwright.sync_api import expect
from app.extensions import db
from app.models import StaffingPerson, StaffingUnit, StaffingLeadershipAssignment, StaffingReportingRelationship
from tests.browser import test_mobile_drawer as fixture_module


class OrgLayoutBrowserTest(unittest.TestCase):
    def test_canonical_large_forest_desktop_mobile_and_existing_inspector(self):
        Fixture=fixture_module.MobileDrawerBrowserTest
        Fixture.setUpClass()
        browser=None
        try:
            with Fixture.app.app_context():
                sort=StaffingUnit(name='Org Night',unit_type='sort')
                operation=StaffingUnit(name='Org Ramp',unit_type='operation',parent=sort)
                department=StaffingUnit(name='Org Shift',unit_type='department',parent=operation)
                area=StaffingUnit(name='Org Door',unit_type='work_area',parent=department)
                db.session.add_all([sort,operation,department,area]);db.session.flush()
                expected={}; people=[]; selected=None
                def employee(classification, unit, supervisor=None):
                    number=len(people)+1
                    person=StaffingPerson(employee_id=f'ORG{number:04}',first_name=f'Employee {number}',
                        last_name='Long Supervisor Name' if number%3==0 else 'Chart',
                        classification=classification,seniority_date=date(2000,1,1))
                    db.session.add(person);db.session.flush();people.append(person.id)
                    db.session.add(StaffingLeadershipAssignment(person=person,unit=unit,leadership_level=unit.unit_type))
                    if supervisor:
                        db.session.add(StaffingReportingRelationship(person=person,reports_to_person=supervisor))
                        expected[str(person.id)]=str(supervisor.id)
                    return person
                for _ in range(2):
                    root=employee('division_manager',sort)
                    for _ in range(2):
                        manager=employee('manager',operation,root)
                        for _ in range(4):
                            supervisor=employee('full_time_supervisor',department,manager)
                            for _ in range(6):
                                report=employee('part_time_supervisor',area,supervisor)
                                if selected is None: selected=report.id
                db.session.commit();count=len(people);selected_name=db.session.get(StaffingPerson,selected).full_name
                relation_count=StaffingReportingRelationship.query.count()

            browser=Fixture.pw.chromium.launch()
            errors=[]
            for width in (1440,390):
                context=browser.new_context(viewport={'width':width,'height':700 if width==1440 else 844},is_mobile=width==390,has_touch=width==390)
                page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                Fixture().login(page);Fixture().ready(page,'/neostaffing/org-chart')
                canvas=page.locator('[data-org-forest]');expect(canvas).to_have_attribute('data-org-layout','ready')
                page.evaluate('async()=>{await document.fonts.ready;await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);}')
                expect(page.locator('[data-reporting-person]')).to_have_count(count)
                geometry=canvas.evaluate("""canvas=>[...canvas.querySelectorAll('[data-reporting-person]')].map(li=>{
                    const card=li.querySelector(':scope > .staffing-reporting-card'),r=card.getBoundingClientRect();
                    return {id:li.dataset.reportingPerson,parent:li.parentElement.closest('[data-reporting-person]')?.dataset.reportingPerson||null,
                        x:r.left,y:r.top,width:r.width,height:r.height,text:card.textContent,assignment:card.querySelector('.org-person-assignment')?.textContent};})""")
                by_id={node['id']:node for node in geometry}
                for node in geometry:
                    self.assertEqual(node['parent'],expected.get(node['id']))
                    self.assertAlmostEqual(node['width'],220,delta=1)
                    self.assertIn('Employee ID',node['text']);self.assertIn('Org',node['assignment'])
                    if node['parent']:
                        parent=by_id[node['parent']]
                        self.assertGreaterEqual(node['y'],parent['y']+parent['height']+47)
                rows={}
                for node in geometry:rows.setdefault(round(node['y']),[]).append(node)
                for row in rows.values():
                    row.sort(key=lambda node:node['x'])
                    for a,b in zip(row,row[1:]):self.assertGreaterEqual(b['x'],a['x']+a['width']+23)
                drawn=canvas.locator('svg path').evaluate_all("paths=>Object.fromEntries(paths.flatMap(path=>path.dataset.reportingChildren.split(' ').map(child=>[child,path.dataset.reportingParent])))")
                self.assertEqual(drawn,expected)
                viewport=page.locator('[data-org-chart-viewport]')
                self.assertTrue(viewport.evaluate('el=>el.scrollWidth>el.clientWidth && el.scrollHeight>el.clientHeight'),
                    viewport.evaluate('el=>({width:el.clientWidth,height:el.clientHeight,scrollWidth:el.scrollWidth,scrollHeight:el.scrollHeight})'))
                viewport.evaluate('el=>{el.scrollLeft=el.scrollWidth;el.scrollTop=el.scrollHeight;}')
                self.assertGreater(viewport.evaluate('el=>el.scrollLeft'),0)
                self.assertGreater(viewport.evaluate('el=>el.scrollTop'),0)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1)
                self.assertEqual(page.locator('[data-people-scope]').count(),1)
                page.screenshot(path=str(Fixture.evidence/f'org-top-down-{width}.png'),full_page=True)
                card=page.locator(f'[data-reporting-person="{selected}"] > .staffing-reporting-card')
                card.scroll_into_view_if_needed();card.click()
                expect(page.locator(f'[data-reporting-person="{selected}"] > a')).to_have_attribute('aria-current','page')
                expect(page.locator('#org-person-inspector')).to_contain_text(selected_name)
                expect(page.locator('#org-person-inspector button').filter(has_text='SAVE REPORTS TO')).to_have_count(1)
                context.close()

            context=browser.new_context();page=context.new_page();Fixture().login(page,'drawer-watcher')
            Fixture().ready(page,f'/neostaffing/org-chart?person_id={selected}')
            expect(page.locator('#org-person-inspector')).to_contain_text(selected_name)
            expect(page.locator('#org-person-inspector button').filter(has_text='SAVE REPORTS TO')).to_have_count(0)
            context.close()
            self.assertFalse(errors)
            with Fixture.app.app_context():
                self.assertEqual(StaffingReportingRelationship.query.count(),relation_count)
                self.assertEqual(StaffingPerson.query.count(),count)
        finally:
            if browser:browser.close()
            Fixture.tearDownClass()
