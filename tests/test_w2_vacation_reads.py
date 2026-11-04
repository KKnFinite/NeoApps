"""Same-call snapshot reuse retains November authority and seasonal behavior."""
from datetime import date
import unittest
from unittest.mock import patch
from sqlalchemy import event
from app.extensions import db
from app.models import User,StaffingLeadershipAssignment
from app.services import neostaffing_vacation as vacation
from tests import test_neostaffing_vacation_selection as fixture


class VacationReadReuseTest(unittest.TestCase):
    setUp=fixture.NeoStaffingVacationSelectionTest.setUp
    tearDown=fixture.NeoStaffingVacationSelectionTest.tearDown
    YEAR=fixture.NeoStaffingVacationSelectionTest.YEAR
    _hierarchy=fixture.NeoStaffingVacationSelectionTest._hierarchy
    _person=fixture.NeoStaffingVacationSelectionTest._person
    _user=fixture.NeoStaffingVacationSelectionTest._user
    _union_actor=fixture.NeoStaffingVacationSelectionTest._union_actor
    _calendar=fixture.NeoStaffingVacationSelectionTest._calendar

    def projection(self,context):
        return dict(carry=[row['calendar'].id for row in context['carry_forward_candidates']],
            calendars=[row['calendar'].id for row in context['calendars']],
            views=[row['calendar'].id for row in context['my_view_calendars']],
            scope=sorted(context['actor'].normal_scope_ids),create=context['can_create_official'])

    def profile(self,uid,day,*,reuse):
        db.session.remove();actor=db.session.get(User,uid);sql=[];commits=[]
        original=vacation.official_calendar_carry_forward_candidates
        def legacy(user,today=None,**snapshots):return original(user,today=today)
        def capture(_c,_u,s,*unused):sql.append(s)
        def committed(_c):commits.append(True)
        event.listen(db.engine,'before_cursor_execute',capture);event.listen(db.engine,'commit',committed)
        try:
            with patch.object(vacation,'official_calendar_carry_forward_candidates',wraps=original if reuse else legacy):
                result=self.projection(vacation.union_calendars_context(self.YEAR,actor,today=day))
        finally:
            event.remove(db.engine,'before_cursor_execute',capture);event.remove(db.engine,'commit',committed)
        self.assertEqual(commits,[])
        self.assertFalse(any(s.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for s in sql))
        return result,len(sql)

    def test_november_reuses_current_actor_hierarchy_without_stale_authority(self):
        owner=self._union_actor('w2_owner','simulator','full_time_supervisor');uid=owner.id
        source=self._calendar(owner,[self.units['blue_area'].id]);source_id=source.id
        ramp_id,area_id=self.units['ramp'].id,self.units['blue_area'].id
        measurements=[]
        for views in (1,5):
            start=0 if views==1 else 1
            for index in range(start,views):
                vacation.create_union_calendar(dict(vacation_year=self.YEAR,calendar_type='view_only',
                    name=f'W2 View {index}',operation_unit_id=ramp_id,include_part_time='1',
                    staffing_unit_ids=[area_id],active='1'),db.session.get(User,uid))
            db.session.commit()
            for day in (date(self.YEAR,10,31),date(self.YEAR,11,1)):
                before,old=self.profile(uid,day,reuse=False);after,new=self.profile(uid,day,reuse=True)
                self.assertEqual(before,after);self.assertEqual(after['carry'],[source_id] if day.month==11 else [])
                self.assertEqual(len(after['views']),views)
                self.assertEqual(old-new,4 if day.month==11 else 0)
                measurements.append((views,day.month,old,new))
        self.assertEqual(measurements[0][2:],measurements[2][2:])
        self.assertEqual(measurements[1][2:],measurements[3][2:])
        print('W2_VACATION_READS',measurements)
        StaffingLeadershipAssignment.query.update({'active':False});db.session.commit()
        denied,_=self.profile(uid,date(self.YEAR,11,1),reuse=True)
        self.assertEqual(denied['carry'],[]);self.assertFalse(denied['create'])
