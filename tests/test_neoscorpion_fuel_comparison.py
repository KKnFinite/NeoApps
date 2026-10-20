"""Display-only raw-pound shortfalls, retaining the existing excess policy."""
from datetime import datetime
from html.parser import HTMLParser
import unittest
from app.extensions import db
from app.models import NeoScorpionFuelingEvent
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_dispatch_checks_excess as fixtures


class Numbers(HTMLParser):
    def __init__(self,html):
        super().__init__();self.values={};self.feed(html)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        for name in ('neo','required'):
            if 'data-dispatch-'+name+'-fuel' in attrs:self.values[name]=(tag,attrs)


class FuelComparisonMathTest(unittest.TestCase):
    def test_any_raw_shortfall_including_one_pound_and_zero(self):
        for neo,required,expected in ((25300,25400,True),(25399,25400,True),
            (25400,25400,False),(25401,25400,False),(0,100,True),(0,0,False),
            ('25400.01','25400.02',True),(None,25400,False),(25400,None,False),
            (-1,25400,False),(25400,-1,False),('NaN',25400,False),
            (25400,'Infinity',False),('bad',25400,False),('',25400,False)):
            with self.subTest(neo=neo,required=required):
                self.assertEqual(fuel.neo_fuel_shortfall_alert(neo,required),expected)


class FuelComparisonIntegrationTest(unittest.TestCase):
    setUp=fixtures.DispatchChecksExcessIntegrationTest.setUp

    def test_context_payload_and_editable_completed_markup_use_the_same_comparison(self):
        for completed in (False,True):
            self.assignment.completed_at_utc=datetime.utcnow() if completed else None
            for target,threshold,shortfall,excess in ((28850,500,True,False),(28751,500,True,False),
                (28750,500,False,False),(25401,500,False,False),(25400,500,False,True),
                (25399,500,False,True),(25400,700,False,False),(26740,300,False,True),
                (None,500,False,False)):
                with self.subTest(completed=completed,target=target,threshold=threshold):
                    self.mission.planned_fuel_load=target
                    self._set_threshold(threshold);db.session.commit()
                    row=fuel.fuel_dispatch_context(self.gateway)['rows'][0]
                    payload=fuel.dispatch_neo_fuel_presentation(self.gateway,self.mission,self.assignment)
                    self.assertEqual(row['neo_fuel_shortfall_alert'],shortfall)
                    self.assertEqual(row['neo_fuel_excess_alert'],excess)
                    self.assertEqual(payload['shortfall'],shortfall)
                    self.assertEqual(payload['excess'],excess)
                    html=self.client.get('/neoscorpion/fuel-dispatch/live-panel').json['html']
                    numbers=Numbers(html).values
                    for name in ('neo','required'):
                        self.assertEqual('is-neo-fuel-shortfall' in numbers[name][1].get('class',''),shortfall)
                    self.assertEqual('is-neo-fuel-excess' in numbers['neo'][1].get('class',''),excess)
                    self.assertNotIn('is-neo-fuel-excess',numbers['required'][1].get('class',''))
                    self.assertEqual(numbers['required'][0],'span' if completed else 'input')
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        self.assertEqual({t.tank_code:t.actual_lbs for t in self.work.tank_states},
                         {'left':15000,'ctr':0,'right':13750})

    def _set_threshold(self,value):
        from app.models import NeoScorpionSettings
        settings=NeoScorpionSettings.query.filter_by(gateway_id=self.gateway.id).first()
        if settings is None:settings=NeoScorpionSettings(gateway_id=self.gateway.id);db.session.add(settings)
        settings.neo_fuel_excess_alert_gallons=value

    def test_incomplete_actual_and_unconfirmed_apu_do_not_create_warnings(self):
        self.mission.planned_fuel_load=40000
        tank=self.work.tank_states[0];original=tank.actual_lbs
        for missing in ('actual','apu'):
            tank.actual_lbs=None if missing=='actual' else original
            self.work.apu_running=None if missing=='apu' else False;db.session.commit()
            row=fuel.fuel_dispatch_context(self.gateway)['rows'][0]
            payload=fuel.dispatch_neo_fuel_presentation(self.gateway,self.mission,self.assignment)
            self.assertFalse(row['neo_fuel_available']);self.assertFalse(row['neo_fuel_shortfall_alert'])
            self.assertFalse(row['neo_fuel_excess_alert']);self.assertFalse(payload['shortfall'])
            self.assertFalse(payload['excess'])

    def test_autosave_immediately_returns_shortfall_and_clears_it_when_resolved(self):
        for target,old,shortfall in (('28.9','25.4',True),('28.7','28.9',False),('25.4','28.7',False)):
            response=self.client.post('/neoscorpion/fuel-dispatch/autosave',data={
                'mission_id':self.mission.id,'field_name':'required_fuel','value':target,'expected_value':old})
            self.assertEqual(response.status_code,200,response.get_data(as_text=True))
            self.assertEqual(response.json['neo_fuel']['shortfall'],shortfall)
            row=fuel.fuel_dispatch_context(self.gateway)['rows'][0]
            self.assertEqual(row['neo_fuel_shortfall_alert'],shortfall)
            self.assertEqual(row['neo_fuel_excess_alert'],response.json['neo_fuel']['excess'])
        self.assertTrue(response.json['neo_fuel']['excess'])

    def test_frozen_visit_target_is_not_the_displayed_departure_load_comparison(self):
        self.work.visit_number=1;self.work.physical_sequence_number=1
        self.work.physical_cycle_number=1;self.work.physical_cycle_type='fuel'
        self.work.physical_required_fuel_lbs=25400;self.work.target_frozen=True
        self.assignment.active_work_state_id=self.work.id
        for target,shortfall,excess in ((28900,True,False),(28750,False,False),
                                      (25401,False,False),(25400,False,True)):
            self.mission.planned_fuel_load=target;db.session.commit()
            row=fuel.fuel_dispatch_context(self.gateway)['rows'][0]
            payload=fuel.dispatch_neo_fuel_presentation(self.gateway,self.mission,self.assignment)
            self.assertEqual(row['required_fuel_lbs'],25400)
            self.assertEqual(row['departure_required_fuel_lbs'],target)
            self.assertEqual(row['neo_fuel_shortfall_alert'],shortfall)
            self.assertEqual(row['neo_fuel_excess_alert'],excess)
            self.assertEqual(payload['shortfall'],shortfall);self.assertEqual(payload['excess'],excess)
        self.assertTrue(self.work.target_frozen)
        self.assertEqual(self.work.physical_required_fuel_lbs,25400)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
