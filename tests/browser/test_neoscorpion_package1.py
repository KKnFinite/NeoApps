"""Actual authenticated conflict feedback, density validation and capacity recovery."""
from datetime import date, datetime, timedelta
from pathlib import Path
import unittest
from unittest.mock import patch

from playwright.sync_api import expect
from app.extensions import db
from app.models import (User, SortDateOperation, SortDateMission, NeoScorpionFuelTruck,
                        NeoScorpionSortTruck, NeoScorpionSortFueler, NeoScorpionFuelAssignment)
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as fixtures


class Package1BrowserTest(unittest.TestCase):
    def test_conflict_feedback_and_settings_guards(self):
        F = fixtures.MobileDrawerBrowserTest
        F.setUpClass()
        F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS'] = 5000
        try:
            with F.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                admin = User.query.filter_by(username='drawer-admin').one()
                op = SortDateOperation(gateway_id=gateway.id, gateway_code=gateway.code,
                    generated_by_user_id=admin.id, sort_date=date.today(), sort_name='night', window_minutes=60)
                db.session.add(op); db.session.flush(); oid = op.id; uid = admin.id
                db.session.add(NeoScorpionSortFueler(sort_date_operation_id=oid, user_id=uid))
                trucks = []
                for number in ('SAFE-A', 'SAFE-B', 'SAFE-C'):
                    truck = NeoScorpionFuelTruck(gateway_id=gateway.id, truck_number=number, capacity_gallons=10000)
                    db.session.add(truck); db.session.flush(); trucks.append(truck.id)
                    db.session.add(NeoScorpionSortTruck(sort_date_operation_id=oid, fuel_truck_id=truck.id,
                        status='available', starting_gallons=8000, current_gallons=8000))
                mission = SortDateMission(sort_date_operation_id=oid, sort_date=op.sort_date,
                    gateway_code=gateway.code, sort_name='night', mission_type='departure', mission_source='manual',
                    flight_number='UPS8123', origin='RFD', destination='SDF', assigned_tail_number='N491UP',
                    planned_datetime_utc=datetime.utcnow()+timedelta(hours=1), timezone='America/Chicago')
                db.session.add(mission); db.session.flush(); mid = mission.id
                db.session.commit()
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                    side_effect=lambda gateway,now=None:[db.session.get(SortDateOperation, oid)]):
                for engine in ('chromium', 'webkit'):
                    browser = getattr(F.pw, engine).launch()
                    try:
                        pages = [browser.new_page(viewport={'width':1440,'height':900}, service_workers='block') for _ in range(2)]
                        errors = []
                        for page in pages:
                            page.on('pageerror', lambda error:errors.append(str(error)))
                            F().login(page)
                            page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                        a, b = pages
                        a.goto(F.origin+'/neoscorpion/fuel-dispatch')
                        row = a.locator(f'[data-dispatch-mission-id="{mid}"]')
                        row.locator('select[name=assigned_fueler_user_id]').select_option(str(uid))
                        row.locator('select[name=assigned_truck_id]').select_option(str(trucks[0]))
                        with a.expect_response(lambda r:r.request.method=='POST' and '/fuel-dispatch/assignment' in r.url) as first:
                            row.locator('[data-dispatch-assignment-submit]').click()
                        self.assertEqual(first.value.status, 200, first.value.text())
                        expect(row.locator('input[name=expected_assigned_truck_id]')).to_have_value(str(trucks[0]), timeout=15000)
                        expect(row.locator('[data-dispatch-assignment-submit]')).to_be_enabled()
                        b.goto(F.origin+'/neoscorpion/fuel-dispatch')
                        stale = b.locator(f'[data-dispatch-mission-id="{mid}"]')
                        # Hold the second Dispatcher draft before the first commits.
                        stale.locator('select[name=assigned_truck_id]').select_option(str(trucks[2]))
                        row.locator('select[name=assigned_truck_id]').select_option(str(trucks[1]))
                        with a.expect_response(lambda r:r.request.method=='POST' and '/fuel-dispatch/assignment' in r.url) as changed:
                            row.locator('[data-dispatch-assignment-submit]').click()
                        self.assertEqual(changed.value.status, 200, changed.value.text())
                        expect(row.locator('input[name=expected_assigned_truck_id]')).to_have_value(str(trucks[1]), timeout=15000)
                        with b.expect_response(lambda r:r.request.method=='POST' and '/fuel-dispatch/assignment' in r.url) as response:
                            stale.locator('[data-dispatch-assignment-submit]').click()
                        self.assertEqual(response.value.status, 400)
                        feedback = stale.locator('[data-assignment-save-status]')
                        expect(feedback).to_contain_text('NOT SAVED')
                        expect(b.locator('[data-assignment-errors]')).to_contain_text('Fuel assignment changed')
                        expect(b.locator('[data-assignment-errors]')).to_be_in_viewport()
                        expect(feedback).to_be_visible()
                        expect(stale.locator('select[name=assigned_truck_id]')).to_have_value(str(trucks[2]))
                        expect(stale.locator('[data-dispatch-assignment-submit]')).to_be_enabled()
                        Path('instance/package1-evidence').mkdir(exist_ok=True)
                        b.screenshot(path=f'instance/package1-evidence/{engine}-assignment-conflict.png')
                        b.locator('[data-operational-board-toggle]:visible').click()
                        expect(b.locator('[data-assignment-errors]')).to_be_in_viewport()
                        expect(stale.locator('select[name=assigned_truck_id]')).to_have_value(str(trucks[2]))
                        b.screenshot(path=f'instance/package1-evidence/{engine}-board-conflict.png')
                        with F.app.app_context():
                            self.assertEqual(NeoScorpionFuelAssignment.query.filter_by(sort_date_mission_id=mid).one().assigned_truck_id, trucks[1])
                        b.reload()
                        expect(stale.locator('select[name=assigned_truck_id]')).to_have_value(str(trucks[1]))
                        stale.locator('select[name=assigned_truck_id]').select_option(str(trucks[2]))
                        b.route('**/neoscorpion/fuel-dispatch/assignment', lambda route:route.abort('failed'), times=1)
                        stale.locator('[data-dispatch-assignment-submit]').click()
                        expect(b.locator('[data-assignment-errors]')).to_contain_text('RESULT UNKNOWN')
                        expect(stale.locator('select[name=assigned_truck_id]')).to_have_value(str(trucks[2]))
                        # Browser guard keeps the rejected value visible, no POST.
                        b.goto(F.origin+'/neoscorpion/settings')
                        density = b.locator('[name=fuel_density_lbs_per_gallon]')
                        for value in ('NaN', 'Infinity', '-Infinity', '0', 'abc'):
                            density.fill(value)
                            self.assertFalse(density.evaluate('(el)=>el.checkValidity()'), value)
                        density.fill('6.7'); self.assertTrue(density.evaluate('(el)=>el.checkValidity()'))
                        # Permanent capacity editor cannot trap a live trip.
                        b.goto(F.origin+'/neoscorpion/truck-manager')
                        truck_form = b.locator(f'form:has(input[name=truck_id][value="{trucks[2]}"])')
                        truck_form.locator('[name=capacity_gallons]').fill('1000')
                        with b.expect_response(lambda r:r.request.method=='POST' and '/truck-manager' in r.url) as bad_capacity:
                            truck_form.get_by_role('button', name='Save', exact=True).click()
                        self.assertEqual(bad_capacity.value.status, 400)
                        expect(b.locator('.flash-error').first).to_contain_text('capacity')
                        b.goto(F.origin+'/neoscorpion/fuel-dispatch')
                        card = b.locator(f'[data-dispatch-truck-id="{trucks[2]}"]')
                        card.get_by_role('button', name='TOP OFF', exact=True).click()
                        expect(card.get_by_role('button', name='RETURN', exact=True)).to_be_visible(timeout=15000)
                        b.goto(F.origin+'/neoscorpion/truck-manager')
                        truck_form.locator('[name=capacity_gallons]').fill('9000')
                        truck_form.get_by_role('button', name='Save', exact=True).click()
                        expect(b.locator('.flash-error').first).to_contain_text('open TOP OFF trip')
                        b.goto(F.origin+'/neoscorpion/fuel-dispatch')
                        card.locator('[name=current_gallons]').fill('9500')
                        card.get_by_role('button', name='RETURN', exact=True).click()
                        expect(card.get_by_role('button', name='TOP OFF', exact=True)).to_be_visible(timeout=15000)
                        # Mobile Dispatch remains view-only; settings guard works
                        # at phone width for an authorized administrator.
                        b.set_viewport_size({'width':375,'height':812})
                        b.goto(F.origin+'/neoscorpion/settings')
                        density.fill('NaN')
                        self.assertFalse(density.evaluate('(el)=>el.checkValidity()'))
                        b.screenshot(path=f'instance/package1-evidence/{engine}-mobile-density.png', full_page=True)
                        self.assertEqual(errors, [])
                        print('Package 1 browser passed:', engine, flush=True)
                    finally:
                        browser.close()
        finally:
            F.tearDownClass()
