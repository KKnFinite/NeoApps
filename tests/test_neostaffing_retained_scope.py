"""Current authority must not rewrite or hide retained historical attribution."""
from datetime import date
import json
import unittest
from unittest.mock import patch
from sqlalchemy import event
from app.extensions import db
from app.models import PermissionRule,StaffingWorkAssignment,StaffingUnit,GatewayNodeRole,NeoNode
from app.models.staffing_timecard import StaffingTimecardSlice as Slice,StaffingTimecardSegment as Segment,StaffingTimecardEdit as Edit,StaffingTimecardWeek as Week
from app.services import neostaffing as staffing
from app.services import neostaffing_timecards as tc
from tests import test_neostaffing_timecards as fixtures
from tests import test_staffing_live_ux as live


def snapshot():
    return {model.__tablename__:[tuple(getattr(row,c.name) for c in model.__table__.columns)
        for row in model.query.populate_existing().order_by(*model.__table__.primary_key.columns)]
        for model in (Slice,Segment,Edit,Week)}


class RetainedScopeTest(unittest.TestCase):
    setUp=fixtures.TimecardsTest.setUp
    setUpBase=fixtures.TimecardsTest.setUpBase
    tearDown=fixtures.TimecardsTest.tearDown
    _login_approved_user=fixtures.TimecardsTest._login_approved_user
    _add_sort_operation=fixtures.TimecardsTest._add_sort_operation
    person=fixtures.TimecardsTest.person
    active_sort=fixtures.TimecardsTest.active_sort
    page=fixtures.TimecardsTest.page
    form=fixtures.TimecardsTest.form
    attendance=fixtures.TimecardsTest.attendance
    command=fixtures.TimecardsTest.command

    def transferred(self,*,day=date(2026,9,10),status='call_in'):
        self.current=self.attendance()
        operation=self._add_sort_operation(day,'night')
        assignment=StaffingWorkAssignment.query.filter_by(person_id=self.peer.id,active=True).one()
        tc.sync_attendance(operation,{self.peer.id:status},{self.peer.id:assignment},self.user.id,as_of=date(2026,9,12))
        db.session.commit();row=Slice.query.filter_by(person_id=self.peer.id).one()
        staffing.assign_work_area(self.peer,self.areas['wbm']);db.session.commit()
        return row

    def view(self,area='wbm',*,day='2026-09-12',mode='times'):
        return self.client.get('/neosektor/manage-employees',query_string=dict(area=area,mode=mode,period='week',date=day))

    def test_transfer_times_reports_and_historical_central_filter_are_read_only(self):
        row=self.transferred();before=snapshot();writes=[]
        def capture(c,u,s,*a):
            if s.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')):writes.append(s)
        event.listen(db.engine,'before_cursor_execute',capture)
        try:
            with patch.object(db.session,'commit',wraps=db.session.commit) as committed:
                east=self.view('ebm');west=self.view();report=self.view(mode='reports')
                committed.assert_not_called()
        finally:event.remove(db.engine,'before_cursor_execute',capture)
        self.assertEqual([r.status_code for r in (east,west,report)],[200]*3)
        self.assertNotIn(f'data-timecard-id="{row.id}"'.encode(),east.data)
        self.assertIn(f'data-timecard-id="{row.id}"'.encode(),west.data)
        self.assertIn(b'Call In',west.data);self.assertIn(self.peer.full_name.encode(),report.data)
        self.assertEqual(writes,[]);self.assertEqual(snapshot(),before)
        values=dict(node_workspace='sektor',node_area='wbm',work_area_ids=[self.areas['wbm'].id])
        found=tc.read_rows(self.user,date(2026,9,6),date(2026,9,12),**values)
        self.assertEqual([item['slice'].id for item in found],[row.id])
        self.assertEqual(row.work_area_unit_id,self.areas['ebm'].id)
        self.assertEqual(json.loads(row.context_json)['work_area'],self.areas['ebm'].name)
        # Central filters continue to mean historical report attribution.
        central=tc.read_rows(self.user,date(2026,9,6),date(2026,9,12),work_area_ids=[self.areas['ebm'].id])
        self.assertIn(row.id,[item['slice'].id for item in central])
        self.assertEqual(snapshot(),before)

    def test_prior_week_boundary_and_invalid_date_preserve_records(self):
        row=self.transferred(day=date(2026,9,3));before=snapshot()
        self.assertNotIn(f'data-timecard-id="{row.id}"'.encode(),self.view().data)
        self.assertIn(f'data-timecard-id="{row.id}"'.encode(),self.view(day='2026-09-03').data)
        self.assertNotIn(f'data-timecard-id="{row.id}"'.encode(),self.view(day='2026-09-13').data)
        self.assertEqual(self.view(day='2026-02-30').status_code,400)
        self.assertEqual(snapshot(),before)

    def test_requested_area_cannot_expand_current_authority_or_use_inactive_home(self):
        row=self.transferred();before=snapshot()
        for ids in ([self.outside.id],[],[self.areas['ebm'].id]):
            found=tc.read_rows(self.user,date(2026,9,6),date(2026,9,12),node_workspace='sektor',node_area='wbm',work_area_ids=ids)
            self.assertEqual(found,[])
        home=StaffingWorkAssignment.query.filter_by(person_id=self.peer.id,active=True).one();home.active=False;db.session.commit()
        self.assertNotIn(f'data-timecard-id="{row.id}"'.encode(),self.view().data)
        self.assertEqual(snapshot(),before)

    def test_revoked_identity_node_and_action_grants_deny_history(self):
        row=self.transferred();before=snapshot()
        rule=PermissionRule.query.filter_by(permission_key='neosektor.wbm.edit').one()
        previous=rule.minimum_role;rule.minimum_role='grandmaster';db.session.commit()
        self.assertEqual(self.view().status_code,403)
        rule.minimum_role=previous;self.manager.active=False;db.session.commit()
        self.assertEqual(self.view().status_code,403)
        self.manager.active=True;db.session.commit()
        node=NeoNode.query.filter_by(code='sektor').one()
        grant=GatewayNodeRole.query.filter_by(node_id=node.id).join(GatewayNodeRole.gateway_membership).filter_by(user_id=self.user.id).one()
        grant.is_active=False;db.session.commit()
        self.assertIn(self.view().status_code,(302,403))
        self.assertFalse(db.session.get(GatewayNodeRole,grant.id).is_active)
        self.assertEqual(snapshot(),before)

    def test_current_area_edit_keeps_original_attribution_and_rejects_stale_or_old_area(self):
        row=self.transferred(status='here');identity=(row.work_area_unit_id,row.context_json,row.sort_unit_id,row.sort_date_operation_id,row.attendance_status)
        command=self.command(row,[{'start':'22:00','end':'02:00'}])
        before=snapshot()
        denied=self.client.post('/neostaffing/timecards/save',json=dict(node_workspace='sektor',node_area='ebm',commands=[command]))
        self.assertEqual(denied.status_code,409);self.assertEqual(snapshot(),before)
        payload=dict(node_workspace='sektor',node_area='wbm',commands=[command])
        saved=self.client.post('/neostaffing/timecards/save',json=payload)
        self.assertEqual(saved.status_code,200,saved.json)
        after=snapshot();self.assertGreater(row.version,command['version'])
        stale=self.client.post('/neostaffing/timecards/save',json=payload)
        self.assertEqual(stale.status_code,409);self.assertEqual(snapshot(),after)
        self.assertEqual((row.work_area_unit_id,row.context_json,row.sort_unit_id,row.sort_date_operation_id,row.attendance_status),identity)

    def test_invalid_bulk_rolls_back_all_rows_and_receipts(self):
        first=self.attendance();second=self.attendance(self.peer);before=snapshot()
        commands=[self.command(first),{**self.command(second),'version':second.version+1}]
        result=self.client.post('/neostaffing/timecards/save',json=dict(node_workspace='sektor',node_area='ebm',commands=commands))
        self.assertEqual(result.status_code,409);self.assertEqual(snapshot(),before)

    def test_combo_week_keeps_both_authorized_sorts(self):
        live.LiveUxTest.test_week_shows_both_combo_halves_using_shared_authority(self)

    def test_unrelated_sort_does_not_become_authorized_by_an_area_transfer(self):
        row=self.transferred();other=StaffingUnit(name='Unrelated Sort',unit_type='sort');db.session.add(other);db.session.flush()
        row.sort_unit_id=other.id;db.session.commit()  # Explicit foreign historical fixture.
        before=snapshot()
        self.assertNotIn(f'data-timecard-id="{row.id}"'.encode(),self.view().data)
        self.assertEqual(snapshot(),before)
