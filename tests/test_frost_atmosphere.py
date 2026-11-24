import json
import math
from pathlib import Path
from unittest import TestCase

from app.services import frost_atmosphere as physical, frost_forecast_native as native


def weather(air_f, dew_f, wind=2, cloud=0, night=1):
    air, dew = (air_f-32)/1.8, (dew_f-32)/1.8
    return dict(obs_temperature_c=air, obs_dewpoint_c=dew, obs_cooling_3h_c_per_hour=0,
                fcst_temperature_change_c=0, fcst_trend_interval_hours=3,
                fcst_temperature_c=air, fcst_dewpoint_c=dew, fcst_wind_kt=wind,
                fcst_relative_humidity=100*math.exp(17.62*dew/(243.12+dew)-17.62*air/(243.12+air)),
                fcst_cloud_cover_percent=cloud, target_night=night)


def classify(fields):
    return physical.assess(fields, minimum=fields['fcst_temperature_c'], nws_air=fields['fcst_temperature_c'])


class AtmosphericAssessmentTest(TestCase):
    def test_weather_cases(self):
        for temp in (40,45,50):
            with self.subTest(temp=temp):
                self.assertEqual(classify(weather(temp,temp-10,14,100))['state'],'weak')
        self.assertEqual(classify(weather(35,32))['state'],'possible')
        self.assertEqual(classify(weather(29,29))['state'],'supporting')
        self.assertEqual(classify(weather(29,10,14,95))['state'],'weak')
        self.assertEqual(classify(weather(40,36,2,100))['state'],'weak')
        self.assertEqual(classify(weather(40,36,14,0))['state'],'weak')
        self.assertEqual(classify(weather(35,32,night=0))['state'],'mixed')
        cooling=dict(weather(39,34),fcst_temperature_change_c=-2)
        self.assertEqual(classify(cooling)['state'],'possible')

    def test_frost_point_is_ice_saturation_within_the_valid_ice_domain(self):
        self.assertAlmostEqual(physical.ice_threshold(-10), -8.88, places=1)
        self.assertEqual(physical.ice_threshold(5),0)
        self.assertIsNone(classify(weather(50,41))['inputs']['frost_point_c'])
        self.assertIsNone(physical.ice_threshold(-80))
        self.assertIsNone(physical.ice_threshold(None))
        self.assertEqual(classify(dict(weather(29,29),fcst_relative_humidity=15))['state'],'mixed')

    def test_missing_guidance_conflicting_guidance_and_class_disagreement(self):
        conditions=weather(35,31)
        for missing in ('obs_temperature_c','fcst_dewpoint_c','fcst_relative_humidity','fcst_cloud_cover_percent','fcst_wind_kt','target_night'):
            with self.subTest(missing=missing):
                self.assertEqual(classify(dict(conditions,**{missing:None}))['state'],'unknown')
        self.assertEqual(physical.assess(conditions,minimum=None,nws_air=2)['state'],'unknown')
        self.assertEqual(physical.assess(conditions,minimum=1,nws_air=-1)['state'],'mixed')
        self.assertEqual(classify(dict(conditions,obs_cooling_3h_c_per_hour=None))['state'],'mixed')
        self.assertIn('unreliable',physical.comparison('weak',{'logistic':.98}))
        self.assertIn('Models disagree',physical.comparison('weak',{'logistic':.98,'forest':.1}))
        self.assertEqual(physical.comparison('possible',{'forest':.1}),'')
        self.assertIn('cannot rule out frost',physical.comparison('supporting',{'forest':.1}))

    def test_assessment_from_weather_without_model_artifacts(self):
        from tests.test_frost_phase9 import NativeContractTest
        observations,snapshots,times=NativeContractTest().data(1)
        grid=dict(hours=1,target_at_utc=times['target'],issued_at_utc=times['cutoff'],
                  period_minimum=dict(temperature_c=-1,start_utc=times['cutoff'],
                                      end_utc='2026-01-02T18:00:00Z'),
                  observation=dict(temperature_c=0))
        source=dict(observations=observations,native_forecasts=snapshots,
                    nws_forecasts=[grid],cutoff_at_utc=times['cutoff'])
        value=physical.for_capture(source,[])['horizons']['1']
        self.assertEqual(value['inputs']['air_c'],0)
        self.assertEqual(value['inputs']['nws_minimum_c'],-1)
        self.assertEqual(value['state'],'mixed')
        self.assertEqual(value['comparison'],'')

    def test_november_19_all_eight_frozen_models_at_each_capture(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/frost_nov19_atmosphere.json').read_bytes())
        for capture, case in fixture.items():
            source=dict(cutoff_at_utc=case['cutoff'],nws_forecasts=[])
            predictions=[]
            for row in case['rows']:
                source['nws_forecasts'].append(dict(hours=row['hours'],target_at_utc=row['target'],
                    issued_at_utc=case['nws_issued'],period_minimum=case['minimum'],
                    observation=dict(temperature_c=row['nws_air_c'])))
                for kind,score in zip(('logistic','forest'),row['scores']):
                    predictions.append(dict(hours=row['hours'],model_version=kind,state='SCORED',score=score,
                        vector=dict(core_available=True,features=[row['fields'].get(k) for k in native.FEATURES])))
            checks=physical.for_capture(source,predictions)['horizons']
            # NeoSubZero Sorts are testing/pretreat context, never weather or
            # physical ground truth. Even contradictory attached records have
            # no influence on any horizon or the comparison with frozen scores.
            unrelated=dict(source,operational_observations=[
                dict(schema_version='frost-physical-inspection-v1',physical_frost=True),
                dict(activity='PRETREAT',reason='F',testing_phase=True)])
            self.assertEqual(physical.for_capture(unrelated,predictions)['horizons'],checks)
            self.assertEqual(set(checks),{'0','1','3','6'})
            for h,check in checks.items():
                self.assertEqual(check['state'],'weak' if capture=='3885' else 'mixed')
                self.assertAlmostEqual(check['inputs']['nws_minimum_c']*1.8+32,43)
                if h in ('1','3') or (capture=='3889' and h=='0'):
                    self.assertIn('Models disagree',check['comparison'])
                    self.assertIn('unreliable' if capture=='3885' else 'unconfirmed',check['comparison'])
                else:
                    self.assertEqual(check['comparison'],'')
            source['nws_forecasts'][0]['issued_at_utc']='2027-01-01T00:00:00Z'
            self.assertEqual(physical.for_capture(source,predictions)['horizons']['0']['state'],'unknown')
