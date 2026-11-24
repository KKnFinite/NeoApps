"""Authenticated tests of the shared arrival/departure save queue and conflict recovery."""
from datetime import datetime, time
from unittest import TestCase

from playwright.sync_api import expect
from app.extensions import db
from app.models import MasterFlightSchedule, User
from app.services.access_control import ensure_default_gateway_and_nodes, backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from tests.browser import test_mobile_drawer as fixture
from tests import test_motherbrain_block_out_authority_postgres as pg

PATH = '/motherbrain/master-schedule'


class MasterQueueBrowserTest(TestCase):
    def exercise(self, engine):
        env = fixture.MobileDrawerBrowserTest
        if pg.URI:
            pg.postgres_setup(self, fixture, env.setUpClass)
        else:
            env.setUpClass()
        self.addCleanup(env.tearDownClass)
        env.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE'] = datetime(2026,11,21,12)
        with env.app.app_context():
            gw = ensure_default_gateway_and_nodes()
            second = User(username='second-admin', email='second-admin@example.test', role='grandmaster',
                          first_name='Second', last_name='Operator', email_verified_at=datetime.now())
            set_user_password(second, 'BrowserFixture123!')
            db.session.add(second); db.session.flush()
            backfill_default_gateway_node_roles(second, role='grandmaster')
            for i in range(60):
                dep = i % 2 == 1
                db.session.add(MasterFlightSchedule(gateway_code=gw.code, gateway_id=gw.id,
                    sort_name='night',mission_type='departure' if dep else 'arrival',flight_number=f'UPS{7200+i}',
                    origin='RFD' if dep else 'SDF',destination='EWR' if dep else 'RFD',
                    aircraft_type='757',active=True,active_days='monday',planned_time_local=time(1)))
            db.session.commit()
        browser = getattr(env.pw, engine).launch()
        self.addCleanup(browser.close)
        page = browser.new_page(viewport={'width':1440 if engine=='chromium' else 390,'height':900})
        other = browser.new_page()
        for tab, name in ((page,'drawer-admin'),(other,'second-admin')):
            env().login(tab, name); env().ready(tab, PATH)
        first = page.locator('#master-arrivals tbody tr').first
        status = page.locator('[data-master-status]')
        save = page.locator('[data-master-save]')
        page.evaluate("""() => {
            const send = window.fetch.bind(window);
            window.saves = {sizes:[], active:0, peak:0, hold:true};
            window.fetch = async (url, init) => {
                if (init?.method !== 'POST') return send(url,init);
                const s = window.saves;
                s.sizes.push(init.body.getAll('row_indexes').length);
                s.peak=Math.max(++s.active,s.peak);
                try {
                    if(s.fail){s.fail=false;return new Response('unavailable',{status:503})}
                    const response=await send(url,init);
                    if(s.hold){s.hold=false;await new Promise(resolve=>{s.release=resolve})}
                    return response;
                } finally {s.active--}
            };
        }""")
        page.clock.install()
        page.evaluate("""() => document.querySelectorAll('tbody input[value=tuesday]').forEach(c=>c.click())""")
        expect(status).to_have_text('Unsaved changes')
        page.clock.fast_forward(4000)
        first.get_by_role('checkbox',name='Wednesday').check()
        self.assertEqual(page.evaluate('saves.sizes'),[])
        page.clock.fast_forward(4900)
        self.assertEqual(page.evaluate('saves.sizes'),[])
        page.clock.fast_forward(150)
        page.wait_for_function('Boolean(saves.release)')
        expect(status).to_have_text('Saving')
        first.get_by_role('checkbox',name='Thursday').check()
        save.click()
        page.clock.fast_forward(6000)
        self.assertEqual(page.evaluate('saves.sizes'),[60])
        page.evaluate('saves.release()')
        expect(status).to_have_text('Saved')
        self.assertEqual(page.evaluate('saves.sizes'),[60,1])
        self.assertEqual(page.evaluate('saves.peak'),1)
        with env.app.app_context():
            schedules=MasterFlightSchedule.query.all()
            self.assertEqual(sum('tuesday' in row.active_days for row in schedules),60)
            self.assertIn('thursday',next(row.active_days for row in schedules if row.flight_number=='UPS7200'))
            dom=page.evaluate("Object.fromEntries([...document.querySelectorAll('input[name^=version_]')].map(i=>[i.name.slice(8),i.value]))")
            self.assertEqual(dom,{str(r.id):r.updated_at.isoformat() for r in schedules})

        # Another signed-in operator tries a stale row. Resolve only their
        # edited checkbox and retain the first operator's committed weekdays.
        their_row=other.locator('#master-arrivals tbody tr').first
        their_row.get_by_role('checkbox',name='Friday').check()
        other.locator('[data-master-save]').click()
        expect(other.locator('[data-master-status]')).to_have_text('Save failed')
        expect(their_row.get_by_role('checkbox',name='Friday')).to_be_checked()
        other.get_by_role('button',name='Keep my edited fields').click()
        other.locator('[data-master-save]').click()
        expect(other.locator('[data-master-status]')).to_have_text('Saved')
        for day in ('Monday','Tuesday','Wednesday','Thursday','Friday'):
            expect(their_row.get_by_role('checkbox',name=day)).to_be_checked()

        # A failed response protects local drafts and triggers the native
        # navigation warning. Manual retry cancels the old debounce timer.
        dep=page.locator('#master-departures tbody tr').first
        dep.get_by_role('checkbox',name='Saturday').check()
        page.evaluate('saves.fail=true')
        save.click()
        expect(status).to_have_text('Save failed')
        expect(dep.get_by_role('checkbox',name='Saturday')).to_be_checked()
        dismissed=[]
        def stay(dialog):
            dismissed.append(dialog.type);dialog.dismiss()
        page.once('dialog',stay)
        page.evaluate('location.reload()')
        page.wait_for_timeout(100)
        self.assertEqual(dismissed,['beforeunload'])
        save.click()
        expect(status).to_have_text('Saved')
        page.clock.fast_forward(5100)
        self.assertEqual(page.evaluate('saves.sizes'),[60,1,1,1])

        # New flight creation stays explicit and never reloads away other drafts.
        page.get_by_role('button',name='ADD ARRIVAL ROW').click()
        new=page.locator('#master-arrivals .master-board-add-row')
        new.get_by_role('textbox',name='Flight number').fill('UPS9000')
        new.get_by_role('textbox',name='Origin').fill('SDF')
        new.get_by_role('textbox',name='Hour',exact=True).fill('03')
        new.get_by_role('textbox',name='Minute',exact=True).fill('10')
        page.clock.fast_forward(6000)
        self.assertEqual(page.evaluate('saves.sizes'),[60,1,1,1])
        page.evaluate('saves.hold=true; delete saves.release')
        new.get_by_role('button',name='Save new arrival row').click()
        page.wait_for_function('Boolean(saves.release)')
        new.get_by_role('textbox',name='Origin').fill('EWR')
        page.evaluate('saves.release()')
        expect(new).to_have_count(0)
        save.click()
        page.wait_for_function("['Saved','Save failed'].includes(document.querySelector('[data-master-status]').textContent)")
        expect(page.locator('[data-master-error]')).to_be_empty()
        expect(status).to_have_text('Saved')
        created=page.locator('#master-arrivals tbody tr').last
        expect(created.get_by_role('textbox',name='Origin')).to_have_value('EWR')
        created.get_by_role('checkbox',name='Friday').uncheck()
        created.locator('.master-active-control').click()
        expect(created.locator('.master-active-control')).to_have_text('Inactive')
        expect(status).to_have_text('Saved')
        created.get_by_role('button',name='Delete UPS9000').click()
        expect(page.get_by_role('button',name='Delete UPS9000')).to_have_count(0)
        with env.app.app_context():
            record=MasterFlightSchedule.query.filter_by(flight_number='UPS9000').one()
            self.assertIsNotNone(record.deleted_at)
            self.assertFalse(record.active)
            self.assertNotIn('friday',record.active_days)
            self.assertEqual(record.origin,'EWR')
        expect(status).to_have_text('Saved')

        # Sustained edits never postpone the oldest eligible draft indefinitely.
        start=page.evaluate('saves.sizes.length')
        page.evaluate('saves.hold=true; delete saves.release')
        for i in range(8):
            page.locator('#master-departures tbody tr').nth(i).get_by_role('checkbox',name='Sunday').check()
            page.clock.fast_forward(4000)
        page.wait_for_function('Boolean(saves.release)')
        dep.get_by_role('checkbox',name='Thursday').check()
        page.evaluate('saves.release()')
        expect(status).to_have_text('Unsaved changes')
        page.clock.fast_forward(3500)
        self.assertEqual(page.evaluate('saves.sizes.slice('+str(start)+')'),[8])
        page.clock.fast_forward(1800)
        expect(status).to_have_text('Saved')
        self.assertEqual(page.evaluate('saves.sizes.slice('+str(start)+')'),[8,1])
        self.assertEqual(page.evaluate('saves.peak'),1)

    def test_chromium(self):
        self.exercise('chromium')

    def test_webkit(self):
        self.exercise('webkit')
