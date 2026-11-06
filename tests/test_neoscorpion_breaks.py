"""Sort-scoped availability never interrupts existing physical fueling."""
import unittest
from datetime import timedelta
from types import SimpleNamespace
from html.parser import HTMLParser
from sqlalchemy import text
from app.extensions import db
from app.models import NeoScorpionSortFueler, NeoScorpionFuelingEvent, NeoScorpionFuelWorkState
from app.services import neoscorpion as fuel
from app.services.neoscorpion_assets import set_nightly_fueler_break, select_nightly_fueler
from tests import test_neoscorpion_fueler_submission as base


class Tags(HTMLParser):
    def __init__(self,html):
        super().__init__();self.tags=[];self.feed(html)
    def handle_starttag(self,tag,attrs):self.tags.append((tag,dict(attrs)))


class FuelerBreakTest(unittest.TestCase):
    setUp=base.SubmissionTest.setUp
    prepare=base.SubmissionTest.prepare
    save=base.SubmissionTest.save
    off=base.SubmissionTest.off
    final_data=base.SubmissionTest.final_data
    login=base.SubmissionTest.login
    baseline=base.SubmissionTest.baseline
    submit=base.SubmissionTest.submit

    def toggle(self,value,expected,**extra):
        return self.client.post('/neoscorpion/fuel-dispatch/assets',data=dict(
            dispatch_assets_workspace='1',operation_id=self.operation.id,action='set_fueler_break',
            user_id=self.fueler.id,on_break=value,expected_on_break=expected,**extra))

    def test_toggle_permissions_sort_scope_idempotence_and_live_payload(self):
        select_nightly_fueler(self.operation,self.fueler);db.session.commit()
        self.login(self.dispatcher)
        response=self.toggle('1','0')
        self.assertEqual(response.status_code,200,response.data)
        self.assertTrue(response.json['changed']);revision=response.json['revision']
        self.assertTrue(NeoScorpionSortFueler.query.one().on_break)
        html=self.client.get('/neoscorpion/fuel-dispatch?assets=open').get_data(as_text=True)
        options=[a for tag,a in Tags(html).tags if tag=='option' and a.get('value')==str(self.fueler.id)
                 and 'data-nightly-fueler-option' in a]
        self.assertTrue(options)
        self.assertTrue(all('disabled' in a and a.get('class')=='is-fueler-on-break' for a in options))
        self.assertFalse(self.toggle('1','0').json['changed'])
        self.assertEqual(fuel.neoscorpion_live_revision(self.gateway)['revision'],revision)
        bad=self.client.post('/neoscorpion/fuel-dispatch/assets',data=dict(action='set_fueler_break',
            user_id=self.fueler.id,operation_id=self.operation.id+1,on_break='0',expected_on_break='1',dispatch_assets_workspace='1'))
        self.assertEqual(bad.status_code,400)
        self.login(self.fueler)
        self.assertEqual(self.toggle('0','1').status_code,403)
        self.assertTrue(self.client.get('/neoscorpion/fuel-assignments/revision').json['fueler_on_break'])
        self.assertIn(b'YOU ARE ON BREAK',self.client.get('/neoscorpion/fueler').data)
        self.login(self.dispatcher)
        self.assertEqual(self.toggle('0','1').status_code,200)
        self.login(self.fueler)
        self.assertFalse(self.client.get('/neoscorpion/fuel-assignments/revision').json['fueler_on_break'])
        self.assertIn(b'data-fueler-break-notice role="status" hidden',self.client.get('/neoscorpion/fueler/live-panel').json['html'].encode())

    def test_break_blocks_new_assignment_and_swap_but_existing_work_finishes(self):
        self.prepare()
        self.save(self.start+timedelta(minutes=2),**self.final_data())
        work_id=self.work.id;on=self.work.manual_on_at_utc;inventory=self.selection.current_gallons
        self.login(self.dispatcher)
        self.assertEqual(self.toggle('1','0').status_code,200)
        other=self.fixture._mission('UPS902','N492UP',25000,2);db.session.commit()
        response=self.client.post('/neoscorpion/fuel-dispatch/assignment',data=dict(
            mission_id=other.id,expected_work_ref='',expected_assigned_fueler_user_id='',
            expected_assigned_truck_id='',assigned_fueler_user_id=self.fueler.id,assigned_truck_id=''),
            headers={'Accept':'application/json','X-Requested-With':'XMLHttpRequest'})
        self.assertEqual(response.status_code,400,response.data)
        self.assertIn('ON BREAK',response.get_data(as_text=True))
        replacement=self.fixture._login_user('break-replacement','operator')
        select_nightly_fueler(self.operation,replacement);db.session.commit()
        set_nightly_fueler_break(self.operation,replacement,'1','0');db.session.commit()
        with self.assertRaisesRegex(ValueError,'ON BREAK'):
            fuel.swap_assignment_fueler(self.gateway,self.dispatcher,self.assignment.id,replacement.id)
        db.session.rollback()
        self.assertEqual((self.assignment.assigned_fueler_user_id,self.work.id,self.work.manual_on_at_utc),
                         (self.fueler.id,work_id,on))
        self.assertEqual(self.selection.current_gallons,inventory)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        self.save(self.start+timedelta(minutes=3),notes='Still working while on break')
        self.off();self.submit()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertEqual(self.work.notes,'Still working while on break')
        self.assertEqual(self.selection.current_gallons,inventory-3000)

    def test_on_break_selected_option_does_not_unassign_on_unrelated_save(self):
        select_nightly_fueler(self.operation,self.fueler);db.session.commit()
        fuel.save_dispatch_assignment(self.gateway,dict(mission_id=self.mission.id,expected_work_ref='',
            expected_assigned_fueler_user_id='',expected_assigned_truck_id='',
            assigned_fueler_user_id=self.fueler.id,assigned_truck_id=''));db.session.commit()
        set_nightly_fueler_break(self.operation,self.fueler,'1','0');db.session.commit()
        fuel.save_dispatch_assignment(self.gateway,dict(mission_id=self.mission.id,expected_work_ref='',
            expected_assigned_fueler_user_id=self.fueler.id,expected_assigned_truck_id='',assigned_truck_id=''))
        db.session.commit()
        self.assertEqual(self.assignment.assigned_fueler_user_id,self.fueler.id)

    def test_additive_schema_default_repeat_and_rollback_preserve_roster(self):
        from app.services.schema_sync import sync_local_sqlite_schema, LOCAL_SQLITE_OPTIONAL_COLUMNS, POSTGRES_OPTIONAL_COLUMNS
        from app.services.neoscorpion_schema import NEOSCORPION_ADDITIVE_COLUMNS
        select_nightly_fueler(self.operation,self.fueler);db.session.commit()
        identity=NeoScorpionSortFueler.query.one().id
        db.session.execute(text('ALTER TABLE neoscorpion_sort_fuelers DROP COLUMN on_break'));db.session.commit()
        for _ in range(2):sync_local_sqlite_schema(self.fixture.app)
        db.session.commit();db.session.expire_all()
        self.assertFalse(NeoScorpionSortFueler.query.one().on_break)
        self.assertEqual(NeoScorpionSortFueler.query.one().id,identity)
        set_nightly_fueler_break(self.operation,self.fueler,'1','0');db.session.rollback()
        self.assertFalse(NeoScorpionSortFueler.query.one().on_break)
        for registry in (LOCAL_SQLITE_OPTIONAL_COLUMNS,POSTGRES_OPTIONAL_COLUMNS,NEOSCORPION_ADDITIVE_COLUMNS):
            self.assertEqual(registry['neoscorpion_sort_fuelers']['on_break'],'BOOLEAN NOT NULL DEFAULT FALSE')

    def test_disabled_pending_fueler_is_not_silently_cleared_from_saved_plan(self):
        from app.services import neoscorpion_pending_uplift as pending
        from app.models import NeoScorpionPendingUplift
        self.prepare()
        p=pending.save_pending_uplift(self.gateway,self.dispatcher,dict(assignment_id=self.assignment.id,
            pending_action='create',expected_cycle=1,expected_tail=self.mission.assigned_tail_number)).pending
        db.session.commit();pid=p.id
        set_nightly_fueler_break(self.operation,self.fueler,'1','0');db.session.commit()
        with self.assertRaisesRegex(ValueError,'ON BREAK'):
            pending.save_pending_uplift(self.gateway,self.dispatcher,dict(assignment_id=self.assignment.id,
                pending_action='save',pending_id=pid,expected_version=p.version,required_fuel='40',
                proposed_truck_id=self.truck.id))
        db.session.rollback()
        self.assertEqual(db.session.get(NeoScorpionPendingUplift,pid).proposed_fueler_user_id,self.fueler.id)


class BreakPlanningTest(unittest.TestCase):
    def test_break_excludes_new_jobs_and_whole_sort_but_retains_current_assignment(self):
        from tests import test_neoscorpion_spear as fixture
        from app.services.neoscorpion_spear import build_spear_plan, SpearSettings
        from app.services.neoscorpion_spear_optimizer import optimize
        kwargs=dict(operation=SimpleNamespace(id=1),planning_settings=fixture._PlanningSettings(),
            spear_settings=SpearSettings(),nightly_fuelers=(fixture._fueler(),),
            nightly_trucks=(fixture._truck(),),now_utc=fixture.NOW)
        row=fixture._row()
        self.assertTrue(build_spear_plan([row],**kwargs).steps)
        self.assertFalse(build_spear_plan([row],on_break_fueler_ids={1},**kwargs).steps)
        self.assertFalse(optimize([row],on_break_fueler_ids={1},**kwargs).steps)
        row['assignment']=SimpleNamespace(assigned_fueler_user_id=1,assigned_truck_id=10,operational_status='active')
        plan=build_spear_plan([row],on_break_fueler_ids={1},**kwargs)
        self.assertIn(row['mission'].id,plan.risks_by_mission_id)
