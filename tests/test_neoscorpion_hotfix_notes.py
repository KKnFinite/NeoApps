"""Mission/visit notes and immediate independent load-planning projections."""
import unittest
from datetime import datetime
from app.extensions import db
from app.models import NeoScorpionFuelAssignment, NeoScorpionFuelWorkState, NeoScorpionTailFuelState, SortDateMission
from app.services import neoscorpion as service
from app.services.neoscorpion_segments import new_work
from tests import test_neoscorpion_fuel_data as fixtures

class NotesPlanningTest(unittest.TestCase):
    setUp=fixtures.FuelerDataTest.setUp
    tearDown=fixtures.FuelerDataTest.tearDown
    _assignment=fixtures.FuelerDataTest._assignment
    _add_user=fixtures.FuelerDataTest._add_user
    client_for=fixtures.FuelerDataTest.client_for
    def prepare(self):
        self.operation,self.mission,self.assignment=self._assignment('N412UP')
        self.dispatcher=self._add_user('notes_dispatch','master');db.session.commit()
    def save(self,**fields):
        result=service.save_fueler_entry(self.gateway,self.user,dict(assignment_id=self.assignment.id,**fields))
        db.session.commit();return result
    def row(self):
        return next(r for r in service.fuel_dispatch_context(self.gateway)['rows'] if r['mission'].id==self.mission.id and not r.get('is_prior_segment'))
    def test_notes_only_save_is_scoped_escaped_live_and_does_not_create_on(self):
        self.prepare();saved=self.save(notes='Waiting <script>alert(1)</script>\nCall airline')
        work=saved.fuel_work_state
        self.assertIsNone(work.on_at_utc);self.assertIsNone(work.off_at_utc)
        self.assertEqual(work.notes,'Waiting <script>alert(1)</script>\nCall airline')
        client=self.client_for(self.dispatcher)
        for url in ('/neoscorpion/hanzo','/neoscorpion/fuel-dispatch','/neoscorpion/fuel-dispatch/live-panel'):
            response=client.get(url);self.assertEqual(response.status_code,200)
            html=response.json['html'] if response.is_json else response.get_data(as_text=True)
            self.assertIn('Waiting &lt;script&gt;',html);self.assertNotIn('Waiting <script>',html)
        revision=saved.revision;self.save(notes='Updated underway')
        self.assertGreater(service.hanzo_context(self.gateway)['hanzo_revision'],revision)
        self.assertEqual(service.hanzo_context(self.gateway)['rows'][0]['fueler_notes'],'Updated underway')
    def test_notes_isolate_visits_cycles_and_same_tail_missions_without_legacy_backfill(self):
        self.prepare();first=self.save(notes='First visit').fuel_work_state
        tail=NeoScorpionTailFuelState.query.one();tail.notes='Legacy unknown owner'
        second=new_work(self.assignment,self.mission.assigned_tail_number,self.user,datetime.utcnow());db.session.commit()
        self.assertEqual(self.row()['fueler_notes'],'')
        self.save(notes='Second visit');self.assertEqual(first.notes,'First visit')
        self.assignment.current_cycle_number=2;third=new_work(self.assignment,'N412UP',self.user,datetime.utcnow());db.session.commit()
        self.save(notes='Uplift note');self.assertEqual(second.notes,'Second visit');self.assertEqual(third.notes,'Uplift note')
        other=SortDateMission(sort_date_operation_id=self.operation.id,sort_date=self.operation.sort_date,gateway_code=self.gateway.code,sort_name='night',mission_type='departure',mission_source='manual',flight_number='UPS802',origin=self.gateway.code,destination='SDF',timezone='America/Chicago',assigned_tail_number='N412UP')
        db.session.add(other);db.session.flush();db.session.add(NeoScorpionFuelAssignment(sort_date_operation_id=self.operation.id,sort_date_mission_id=other.id));db.session.commit()
        rows=service.fuel_dispatch_context(self.gateway)['rows'];self.assertEqual(next(r for r in rows if r['mission'].id==other.id)['fueler_notes'],'')
        self.assertEqual(tail.notes,'Uplift note')
        self.mission.assigned_tail_number='N491UP';db.session.commit()
        self.assertEqual(self.row()['fueler_notes'],'')
    def test_neo_cf_independent_before_off_complete_and_copy_matches(self):
        self.prepare();self.mission.assigned_tail_number='N123UP';self.assignment.confirmed_tail_number='N123UP';db.session.commit()
        values={f'remaining_{code}':'10' for code in ('l_out','l_in','ctr','r_in','r_out','tt')}
        self.save(apu_running='no',actual_ctr='3.5',**values)
        row=self.row();self.assertEqual(row['load_planning_output'],'UPS801 SDF N123UP CF > 3500')
        self.save(**{f'actual_{code}':'9' for code in ('l_out','l_in','r_in','r_out','tt')})
        row=self.row();self.assertEqual(row['load_planning_output'],'UPS801 SDF N123UP NEO > 48.5 CF > 3500')
        self.assertIsNone(self.assignment.completed_at_utc);self.assertIsNone(row['fuel_work_state'].off_at_utc)
        self.save(actual_ctr='')
        self.assertEqual(self.row()['load_planning_output'],'UPS801 SDF N123UP')
        client=self.client_for(self.dispatcher);html=client.get('/neoscorpion/fuel-dispatch').get_data(as_text=True)
        self.assertIn('data-copy-value="UPS801 SDF N123UP"',html)
