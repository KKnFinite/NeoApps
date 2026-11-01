"""Authenticated local browser projection; no real provider requests."""
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from playwright.sync_api import expect

from app.services import neosubzero_weather as weather
from tests.test_external_response_limits import Response
from tests.browser import test_neosubzero_ucc as fixtures
from tests.browser.test_security_headers import enforced_csp


class WeatherProviderReliabilityBrowserTest(unittest.TestCase):
    def test_fresh_stale_unavailable_and_flight_display_both_engines(self):
        with enforced_csp():
            fixture = fixtures.UccBrowserTest
            fixture.setUpClass()
            origin = patch.object(fixtures.MobileDrawerBrowserTest, 'origin', fixture.origin, create=True)
            origin.start()
            fixture.app.config['NEOSUBZERO_WEATHER_ENABLED'] = True
            clock = [datetime(2026, 10, 23, 3, tzinfo=timezone.utc)]
            failed = [False]
            metar_calls = []
            def provider(request, timeout):
                url = request.full_url
                if 'aviationweather.gov' in url:
                    metar_calls.append(url)
                if failed[0]:
                    raise TimeoutError('Isolated provider outage')
                if 'aviationweather.gov' in url:
                    payload = [{'icaoId': 'KRFD', 'reportTime': '2026-10-23T03:00:00Z', 'temp': 0,
                                'dewp': -1, 'wspd': 3, 'wdir': 270, 'visib': 10, 'clouds': [],
                                'rawOb': 'KRFD 230300Z 27003KT 10SM CLR 00/M01 A3000'}]
                elif '/points/' in url:
                    payload = {'properties': {'forecastHourly': 'https://api.weather.gov/gridpoints/LOT/1,1/forecast/hourly',
                                              'forecastGridData': 'https://api.weather.gov/gridpoints/LOT/1,1'}}
                elif url.endswith('/hourly'):
                    payload = {'properties': {'generatedAt': '2026-10-23T02:00:00Z', 'periods': [
                        {'startTime': '2026-10-22T22:00:00-05:00', 'temperature': 32, 'temperatureUnit': 'F',
                         'windSpeed': '3 mph', 'windDirection': 'W', 'shortForecast': 'Clear'}]}}
                else:
                    payload = {'properties': {}}
                return Response(json.dumps(payload).encode())
            try:
                with patch.object(weather, 'urlopen', side_effect=provider), patch(
                        'app.neonodes.neosubzero.routes.neosubzero_weather_context',
                        side_effect=lambda gateway, operation=None, **_: weather.neosubzero_weather_context(gateway, operation, now=clock[0])), patch(
                        'app.neonodes.neosubzero.routes.neosubzero_weather_revision',
                        side_effect=lambda **_: weather.neosubzero_weather_revision(now=clock[0])):
                    for engine, width in (('chromium', 1920), ('webkit', 390)):
                        weather.clear_neosubzero_weather_cache()
                        failed[0] = False
                        clock[0] = datetime(2026, 10, 23, 3, tzinfo=timezone.utc)
                        browser = getattr(fixture.pw, engine).launch(headless=True)
                        try:
                            page = browser.new_page(viewport={'width': width, 'height': 900}, service_workers='block')
                            fixture().login(page)
                            fixture().ready(page, '/neosubzero/ucc')
                            panel = page.locator('.neosubzero-weather-current')
                            expect(panel).to_contain_text('32')
                            failed[0] = True
                            clock[0] += timedelta(minutes=6)
                            fixture().ready(page, '/neosubzero/ucc')
                            expect(page.locator('.neosubzero-weather-source')).to_contain_text('STALE')
                            expect(page.locator('.neosubzero-weather-note').filter(has_text='showing last good data')).to_be_visible()
                            calls = len(metar_calls)
                            fixture().ready(page, '/neosubzero/ucc')
                            self.assertEqual(len(metar_calls), calls)
                            clock[0] += timedelta(hours=6)
                            fixture().ready(page, '/neosubzero/ucc')
                            expect(panel).to_contain_text('Unavailable')
                            expect(page.locator('.neosubzero-weather-note').filter(has_text='temporarily unavailable')).to_be_visible()
                            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'), 1)
                            fixture().ready(page, '/neosubzero/operations?mode=outbound')
                            expect(page.locator('[data-ops-row]').first).to_be_visible()
                            fixture().ready(page, '/motherbrain/flight-api-test')
                            expect(page.locator('#flight-api-test-title')).to_contain_text('Manage API')
                        finally:
                            browser.close()
            finally:
                weather.clear_neosubzero_weather_cache()
                origin.stop()
                fixture.tearDownClass()
