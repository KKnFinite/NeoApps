import unittest
from datetime import date, datetime
from io import StringIO
from zoneinfo import ZoneInfo

from app.services.neosubzero_frost_history import (
    CsvHistoricalWeatherProvider,
    build_frost_training_dataset,
    default_negative_exposure_window,
    normal_operational_nights,
    parse_cryotech_csv,
)


CRYOTECH_CSV = '''Application ID,Application Date,Start Time,End Time,Tail Number,Truck Number,Fluid Type,Surface Area,Reason for Application,Active Precipitation,Gallons,Concentration %,Notes
F-1,01/06/2026,03:10,03:22," n123 up ",truck 7,Type 1,Wings + Tail,fRoSt,Freezing Fog,"123.5",50%,"first line
second line"
P-1,01/06/2026,23:30,23:45,N456,8,Type IV,Entire Aircraft,Pre-Treat,Snow,44,100,overnight pretreat
'''

WEATHER_CSV = '''station,valid,tmpf,dwpf,relh,sknt,gust,drct,vsby,wxcodes,skyc1
KRFD,2026-01-06T06:00:00Z,34,29,82,6,10,170,10,,BKN
KRFD,2026-01-06T09:00:00Z,30,28,90,4,8,180,5,FZFG,OVC
KRFD,2026-01-07T09:00:00Z,25,24,94,3,6,190,4,BR,OVC
'''


class NeoSubZeroFrostHistoryTest(unittest.TestCase):
    def test_cryotech_parser_normalizes_available_application_fields(self):
        result = parse_cryotech_csv(StringIO(CRYOTECH_CSV), source_name="cryotech.csv")

        self.assertEqual(result.issues, ())
        self.assertEqual(len(result.rows), 2)
        frost, pretreat = result.rows
        self.assertEqual(frost.application_id, "F-1")
        self.assertEqual(frost.operational_night, date(2026, 1, 5))
        self.assertEqual(frost.tail_number, "N123UP")
        self.assertEqual(frost.truck_number, "TRUCK 7")
        self.assertEqual(frost.fluid_type, "Type I")
        self.assertEqual(frost.reason_for_application, "Frost")
        self.assertEqual(frost.outcome, "departure_frost")
        self.assertEqual(frost.gallons, 123.5)
        self.assertEqual(frost.concentration_percent, 50.0)
        self.assertEqual(frost.notes, "first line\nsecond line")
        self.assertEqual(pretreat.reason_for_application, "Pretreat")
        self.assertEqual(pretreat.outcome, "pretreat")

    def test_normal_nights_and_default_window_follow_operational_night(self):
        nights = normal_operational_nights(date(2026, 1, 5), date(2026, 1, 11))
        self.assertEqual(
            nights,
            (
                date(2026, 1, 5),
                date(2026, 1, 6),
                date(2026, 1, 7),
                date(2026, 1, 8),
            ),
        )

        start_at, end_at = default_negative_exposure_window(date(2026, 1, 5))
        self.assertEqual(start_at.isoformat(), "2026-01-06T02:00:00-06:00")
        self.assertEqual(end_at.isoformat(), "2026-01-06T04:00:00-06:00")

    def test_dataset_preserves_outside_window_frost_and_avoids_false_negatives(self):
        cryotech = parse_cryotech_csv(
            "Application Date,Start Time,End Time,Tail,Reason for Application\n"
            "01/05/2026,23:15,23:30,N101,Frost\n"
            "01/07/2026,01:00,01:12,N202,Pretreat\n"
        )
        records = build_frost_training_dataset(
            cryotech.rows,
            None,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 8),
            departure_exposure_nights=(date(2026, 1, 6),),
        )

        outside_window = next(row for row in records if row.tail_number == "N101")
        self.assertEqual(outside_window.frost_label, "positive")
        self.assertEqual(outside_window.exposure_timestamp_local.hour, 23)

        pretreat = next(row for row in records if row.tail_number == "N202")
        self.assertEqual(pretreat.frost_label, "unlabeled")
        self.assertEqual(pretreat.outcome, "pretreat")

        window_rows = {row.operational_night: row for row in records if row.tail_number is None}
        self.assertNotIn(date(2026, 1, 5), window_rows)
        self.assertEqual(window_rows[date(2026, 1, 6)].frost_label, "negative")
        self.assertEqual(window_rows[date(2026, 1, 6)].outcome, "no_frost_exposure")
        self.assertEqual(window_rows[date(2026, 1, 7)].frost_label, "unlabeled")
        self.assertEqual(window_rows[date(2026, 1, 7)].outcome, "no_exposure")
        self.assertEqual(window_rows[date(2026, 1, 8)].frost_label, "unlabeled")
        self.assertFalse(
            any(
                row.frost_label == "negative" and row.outcome == "no_exposure"
                for row in records
            )
        )

    def test_historical_weather_provider_adds_nearest_and_lookback_features(self):
        weather = CsvHistoricalWeatherProvider(
            StringIO(WEATHER_CSV),
            source_name="iem-asos.csv",
        )
        cryotech = parse_cryotech_csv(StringIO(CRYOTECH_CSV))
        records = build_frost_training_dataset(
            cryotech.rows,
            weather,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
        )

        frost = next(row for row in records if row.outcome == "departure_frost")
        self.assertEqual(frost.weather_observed_at.hour, 3)
        self.assertEqual(frost.weather_observed_at.tzinfo, ZoneInfo("America/Chicago"))
        self.assertEqual(frost.temperature_f, 30.0)
        self.assertEqual(frost.dewpoint_f, 28.0)
        self.assertEqual(frost.dewpoint_spread_f, 2.0)
        self.assertEqual(frost.temperature_change_3h_f, -4.0)
        self.assertEqual(frost.dewpoint_spread_change_3h_f, -3.0)
        self.assertEqual(frost.sky_condition, "OVC")
        self.assertEqual(frost.reported_weather, "FZFG")
        self.assertEqual(frost.weather_source, "iem-asos.csv")

    def test_weather_provider_is_krfd_scoped_and_accepts_missing_tokens(self):
        weather = CsvHistoricalWeatherProvider(
            "station,valid,tmpf,dwpf,relh,sknt\n"
            "KORD,2026-01-06T09:00:00Z,10,8,90,12\n"
            "KRFD,2026-01-06T09:00:00Z,30,28,M,4\n"
        )
        rows = weather.observations(
            datetime.fromisoformat("2026-01-06T08:00:00+00:00"),
            datetime.fromisoformat("2026-01-06T10:00:00+00:00"),
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].station, "KRFD")
        self.assertAlmostEqual(rows[0].relative_humidity, 92.2, places=1)

    def test_training_record_serializes_as_compact_artifact_data(self):
        cryotech = parse_cryotech_csv(StringIO(CRYOTECH_CSV))
        record = build_frost_training_dataset(
            cryotech.rows,
            None,
            start_date="01/05/2026",
            end_date="01/05/2026",
        )[0]

        payload = record.to_dict()
        self.assertEqual(payload["operational_night"], "2026-01-05")
        self.assertTrue(payload["exposure_timestamp_local"].endswith("-06:00"))
        self.assertIn(payload["frost_label"], {"positive", "negative", "unlabeled"})


if __name__ == "__main__":
    unittest.main()
