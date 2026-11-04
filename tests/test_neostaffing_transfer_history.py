"""Retained attendance/timecards and manager scope follow a confirmed real transfer."""
from datetime import date
from app.extensions import db
from app.models import User, PortalAppAccess, StaffingWorkAssignment
from app.models.staffing_timecard import StaffingTimecardSlice as Slice
from app.services import neostaffing as staffing
from app.services import neostaffing_timecards as tc
from app.services.neostaffing_transfers import transfer_employee
from tests import test_neostaffing_retained_scope as contracts


class TransferRetainedHistoryTest(contracts.RetainedScopeTest):
    def transferred(self,*,day=date(2026,9,10),status='call_in'):
        self.current=self.attendance()
        operation=self._add_sort_operation(day,'night')
        assignment=StaffingWorkAssignment.query.filter_by(person_id=self.peer.id,active=True).one()
        tc.sync_attendance(operation,{self.peer.id:status},{self.peer.id:assignment},self.user.id,as_of=date(2026,9,12))
        actor=User(username='transfer-administrator',password_hash='synthetic-no-login',role='watcher',is_active=True)
        db.session.add(actor);db.session.flush()
        db.session.add(PortalAppAccess(user_id=actor.id,app_code='neostaffing',role='grandmaster',status='approved',is_active=True))
        db.session.commit()
        row=Slice.query.filter_by(person_id=self.peer.id).one()
        before=contracts.snapshot()
        transfer_employee(self.peer.id,actor,dict(source_assignment_id=assignment.id,
            destination_work_area_id=self.areas['wbm'].id,confirm_transfer='yes',
            expected_person_version=staffing.entity_version(self.peer),expected_assignment_version=str(self.peer.shift_flow_version)))
        db.session.commit()
        self.assertEqual(contracts.snapshot(),before)
        self.assertFalse(assignment.active)
        self.assertEqual(assignment.work_area_unit_id,self.areas['ebm'].id)
        return row
