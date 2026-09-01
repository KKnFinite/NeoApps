import unittest
from datetime import date, datetime
from io import StringIO
from zoneinfo import ZoneInfo

from app.services.neosubzero_frost_history import (
    CsvHistoricalWeatherProvider,
    build_frost_history_dataset,
    build_frost_training_dataset,
    default_negative_exposure_window,
    group_cryotech_treatment_events,
    historical_no_sort_dates,
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
        dataset = build_frost_history_dataset(
            cryotech.rows,
            None,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 8),
            departure_exposure_nights=(date(2026, 1, 8),),
        )
        records = dataset.training_records

        outside_window = next(row for row in records if row.tail_number == "N101")
        self.assertEqual(outside_window.frost_label, "positive")
        self.assertEqual(outside_window.exposure_timestamp_local.hour, 23)

        pretreat = next(row for row in records if row.tail_number == "N202")
        self.assertEqual(pretreat.frost_label, "unlabeled")
        self.assertEqual(pretreat.outcome, "pretreat")

        window_rows = {
            row.operational_night: row for row in records if row.tail_number is None
        }
        self.assertNotIn(date(2026, 1, 5), window_rows)
        self.assertNotIn(date(2026, 1, 6), window_rows)
        self.assertEqual(window_rows[date(2026, 1, 7)].frost_label, "negative")
        self.assertEqual(window_rows[date(2026, 1, 7)].outcome, "no_frost_exposure")
        self.assertEqual(window_rows[date(2026, 1, 8)].frost_label, "negative")
        self.assertEqual(window_rows[date(2026, 1, 8)].outcome, "no_frost_exposure")
        evidence = {row.operational_night: row for row in dataset.night_evidence}
        self.assertEqual(
            evidence[date(2026, 1, 6)].evidence_class,
            "uncertain_pretreat",
        )
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

    def test_precipitation_before_0200_excludes_entire_frost_window(self):
        weather = CsvHistoricalWeatherProvider(
            "station,valid,wxcodes\n"
            "KRFD,2026-01-06T07:30:00Z,-SN\n"
            "KRFD,2026-01-06T09:00:00Z,\n"
        )

        dataset = build_frost_history_dataset(
            (),
            weather,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
        )

        evidence = dataset.night_evidence[0]
        self.assertEqual(evidence.evidence_class, "unlabeled")
        self.assertTrue(evidence.excluded_by_pre_0200_precipitation)
        self.assertEqual(evidence.precipitation_onset_local.hour, 1)
        self.assertIsNone(evidence.usable_exposure_start_local)
        self.assertIsNone(evidence.usable_exposure_end_local)
        self.assertFalse(any(row.frost_label == "negative" for row in dataset.training_records))

    def test_precipitation_during_window_truncates_exposure_without_reopening(self):
        weather = CsvHistoricalWeatherProvider(
            "station,valid,wxcodes\n"
            "KRFD,2026-01-06T08:00:00Z,\n"
            "KRFD,2026-01-06T08:30:00Z,FZDZ\n"
            "KRFD,2026-01-06T09:00:00Z,\n"
        )

        dataset = build_frost_history_dataset(
            (),
            weather,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
        )

        evidence = dataset.night_evidence[0]
        record = dataset.training_records[0]
        self.assertEqual(evidence.evidence_class, "clean_negative")
        self.assertEqual(evidence.usable_exposure_start_local.strftime("%H:%M"), "02:00")
        self.assertEqual(evidence.usable_exposure_end_local.strftime("%H:%M"), "02:30")
        self.assertEqual(evidence.precipitation_onset_local.strftime("%H:%M"), "02:30")
        self.assertEqual(record.exposure_window_end_local.strftime("%H:%M"), "02:30")
        self.assertEqual(record.usable_exposure_end_local.strftime("%H:%M"), "02:30")

    def test_dry_night_retains_full_original_exposure_window(self):
        weather = CsvHistoricalWeatherProvider(
            "station,valid,wxcodes\n"
            "KRFD,2026-01-06T07:30:00Z,FG\n"
            "KRFD,2026-01-06T09:00:00Z,BR\n"
        )

        dataset = build_frost_history_dataset(
            (),
            weather,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
        )

        evidence = dataset.night_evidence[0]
        self.assertEqual(evidence.evidence_class, "clean_negative")
        self.assertEqual(evidence.original_exposure_start_local.strftime("%H:%M"), "02:00")
        self.assertEqual(evidence.original_exposure_end_local.strftime("%H:%M"), "04:00")
        self.assertEqual(evidence.usable_exposure_start_local, evidence.original_exposure_start_local)
        self.assertEqual(evidence.usable_exposure_end_local, evidence.original_exposure_end_local)
        self.assertIsNone(evidence.precipitation_onset_local)

    def test_frost_before_precipitation_onset_remains_confirmed_positive(self):
        cryotech = parse_cryotech_csv(
            "Application Date,Start Time,Tail,Reason\n"
            "01/06/2026,02:15,N901,F\n"
        )
        weather = CsvHistoricalWeatherProvider(
            "station,valid,wxcodes\n"
            "KRFD,2026-01-06T08:30:00Z,SN\n"
        )

        dataset = build_frost_history_dataset(
            cryotech.rows,
            weather,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
        )

        self.assertEqual(dataset.night_evidence[0].evidence_class, "confirmed_positive")
        self.assertEqual(dataset.training_records[0].frost_label, "positive")

    def test_post_precipitation_frost_does_not_create_false_negative(self):
        cryotech = parse_cryotech_csv(
            "Application Date,Start Time,Tail,Reason\n"
            "01/06/2026,03:00,N902,F\n"
        )
        weather = CsvHistoricalWeatherProvider(
            "station,valid,wxcodes\n"
            "KRFD,2026-01-06T08:30:00Z,RA\n"
            "KRFD,2026-01-06T09:30:00Z,\n"
        )

        dataset = build_frost_history_dataset(
            cryotech.rows,
            weather,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
        )

        self.assertEqual(dataset.night_evidence[0].evidence_class, "unlabeled")
        self.assertEqual(dataset.training_records[0].evidence_class, "unlabeled")
        self.assertFalse(any(row.frost_label == "negative" for row in dataset.training_records))

    def test_multi_truck_rows_collapse_to_one_aircraft_event(self):
        cryotech = parse_cryotech_csv(
            "Application ID,Application Date,Start Time,End Time,Tail,Truck,"
            "Reason for Application,Gallons\n"
            "EVT-10,01/06/2026,02:10,02:25,N100,1,Frost,20\n"
            "EVT-10,01/06/2026,02:11,02:26,N100,2,Frost,22\n"
            "EVT-10,01/06/2026,02:12,02:27,N100,3,Frost,24\n"
            "EVT-10,01/06/2026,02:13,02:28,N100,4,Frost,26\n"
        )
        dataset = build_frost_history_dataset(
            cryotech.rows,
            None,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
            departure_opportunities_by_night={date(2026, 1, 5): 10},
        )

        self.assertEqual(len(dataset.raw_application_rows), 4)
        self.assertEqual(len(dataset.treatment_events), 1)
        event = dataset.treatment_events[0]
        self.assertEqual(event.raw_application_count, 4)
        self.assertEqual(event.truck_numbers, ("1", "2", "3", "4"))
        self.assertEqual(event.total_gallons, 92.0)
        self.assertEqual(len(dataset.training_records), 1)
        evidence = dataset.night_evidence[0]
        self.assertEqual(evidence.number_frost_treated_events, 1)
        self.assertEqual(evidence.number_departure_opportunities, 10)
        self.assertEqual(evidence.frost_treated_percentage, 10.0)
        self.assertTrue(evidence.weak_frost_evidence)

    def test_same_tail_twice_remains_two_inferred_events(self):
        cryotech = parse_cryotech_csv(
            "Application Date,Start Time,End Time,Tail,Truck,Reason\n"
            "01/06/2026,01:05,01:20,N200,1,Frost\n"
            "01/06/2026,01:08,01:22,N200,2,Frost\n"
            "01/06/2026,04:10,04:25,N200,3,Frost\n"
            "01/06/2026,04:12,04:26,N200,4,Frost\n"
        )

        events = group_cryotech_treatment_events(cryotech.rows)

        self.assertEqual(len(events), 2)
        self.assertEqual(
            [event.raw_application_count for event in events],
            [2, 2],
        )
        self.assertTrue(all(event.grouping_method == "bounded_time" for event in events))

    def test_one_or_two_frost_events_without_pretreat_are_weak_evidence(self):
        cryotech = parse_cryotech_csv(
            "Application Date,Start Time,Tail,Reason\n"
            "01/06/2026,01:00,N301,Frost\n"
            "01/06/2026,03:00,N302,Frost\n"
        )
        dataset = build_frost_history_dataset(
            cryotech.rows,
            None,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
        )

        evidence = dataset.night_evidence[0]
        self.assertEqual(evidence.evidence_class, "confirmed_positive")
        self.assertEqual(evidence.number_frost_treated_events, 2)
        self.assertTrue(evidence.weak_frost_evidence)
        self.assertFalse(evidence.broader_frost_treatment)

    def test_three_frost_events_expose_broader_treatment_metadata(self):
        cryotech = parse_cryotech_csv(
            "Application Date,Start Time,Tail,Reason\n"
            "01/06/2026,01:00,N311,Frost\n"
            "01/06/2026,02:00,N312,Frost\n"
            "01/06/2026,03:00,N313,Frost\n"
        )
        dataset = build_frost_history_dataset(
            cryotech.rows,
            None,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
        )

        evidence = dataset.night_evidence[0]
        self.assertEqual(evidence.number_frost_treated_events, 3)
        self.assertFalse(evidence.weak_frost_evidence)
        self.assertTrue(evidence.broader_frost_treatment)

    def test_pretreat_only_is_uncertain_even_with_confirmed_exposure(self):
        cryotech = parse_cryotech_csv(
            "Application Date,Start Time,Tail,Reason\n"
            "01/05/2026,22:00,N401,Pretreat\n"
        )
        dataset = build_frost_history_dataset(
            cryotech.rows,
            None,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
            departure_exposure_nights=(date(2026, 1, 5),),
        )

        evidence = dataset.night_evidence[0]
        self.assertEqual(evidence.evidence_class, "uncertain_pretreat")
        self.assertTrue(evidence.pretreat_occurred)
        self.assertEqual(evidence.number_pretreat_treated_events, 1)
        self.assertEqual(dataset.training_records[0].evidence_class, "uncertain_pretreat")

    def test_pretreat_then_frost_is_confirmed_positive(self):
        cryotech = parse_cryotech_csv(
            "Application Date,Start Time,Tail,Reason\n"
            "01/05/2026,22:00,N501,Pretreat\n"
            "01/06/2026,02:30,N502,Frost\n"
        )
        dataset = build_frost_history_dataset(
            cryotech.rows,
            None,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
        )

        evidence = dataset.night_evidence[0]
        self.assertEqual(evidence.evidence_class, "confirmed_positive")
        self.assertTrue(evidence.pretreat_and_frost)
        self.assertTrue(evidence.pretreat_before_frost)
        self.assertFalse(evidence.weak_frost_evidence)

    def test_normal_sort_nights_reconstruct_clean_negative_without_fake_counts(self):
        dataset = build_frost_history_dataset(
            (),
            None,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 6),
        )
        evidence = {row.operational_night: row for row in dataset.night_evidence}

        self.assertEqual(evidence[date(2026, 1, 5)].evidence_class, "clean_negative")
        self.assertEqual(evidence[date(2026, 1, 6)].evidence_class, "clean_negative")
        self.assertIsNone(evidence[date(2026, 1, 5)].number_departure_opportunities)
        self.assertIsNone(evidence[date(2026, 1, 5)].frost_treated_percentage)

    def test_authoritative_reason_codes_preserve_code_and_description(self):
        expected = (
            ("FG", "Fog", "other_spray"),
            ("FZFG", "Freezing Fog", "other_spray"),
            ("FZDZ", "Freezing Drizzle", "other_spray"),
            ("FZRA", "Freezing Rain", "other_spray"),
            ("GR", "Hail", "other_spray"),
            ("GS", "Small Hail", "other_spray"),
            ("GS", "Snow Pellets", "other_spray"),
            ("PL", "Ice Pellets", "other_spray"),
            ("IC", "Ice Crystals", "other_spray"),
            ("SG", "Snow Grains", "other_spray"),
            ("SN", "Snow", "other_spray"),
            ("DZ", "Drizzle", "other_spray"),
            ("CS", "Cold Soak", "other_spray"),
            ("F", "Frost", "departure_frost"),
            ("P", "Preventative De-Ice/Anti-Ice", "pretreat"),
        )
        lines = ["Application Date,Start Time,Tail,Reason Code,Reason Description"]
        for index, (code, description, _outcome) in enumerate(expected, start=1):
            lines.append(f"01/06/2026,02:{index:02d},N{index:03d},{code},{description}")

        result = parse_cryotech_csv("\n".join(lines) + "\n")

        self.assertEqual(len(result.rows), len(expected))
        for row, (code, description, outcome) in zip(result.rows, expected):
            with self.subTest(code=code, description=description):
                self.assertEqual(row.reason_code_raw, code)
                self.assertEqual(row.reason_description_raw, description)
                self.assertEqual(row.reason_description_normalized, description)
                self.assertEqual(row.outcome, outcome)
        gs_rows = [row for row in result.rows if row.reason_code_raw == "GS"]
        self.assertEqual(
            {row.reason_description_normalized for row in gs_rows},
            {"Small Hail", "Snow Pellets"},
        )

        ambiguous_events = group_cryotech_treatment_events(
            parse_cryotech_csv(
                "Application Date,Start Time,Tail,Reason Code,Reason Description\n"
                "01/06/2026,02:00,N777,GS,Small Hail\n"
                "01/06/2026,02:00,N777,GS,Snow Pellets\n"
            ).rows
        )
        self.assertEqual(len(ambiguous_events), 2)

    def test_reason_code_only_maps_frost_and_pretreat_without_guessing_gs(self):
        result = parse_cryotech_csv(
            "Application Date,Start Time,Tail,Reason\n"
            "01/06/2026,02:00,N701,f\n"
            "01/06/2026,02:30,N702,P\n"
            "01/06/2026,03:00,N703,GS\n"
        )
        frost, pretreat, ambiguous_gs = result.rows

        self.assertEqual(frost.reason_code_raw, "f")
        self.assertEqual(frost.reason_description_normalized, "Frost")
        self.assertEqual(frost.reason_for_application, "Frost")
        self.assertEqual(frost.outcome, "departure_frost")
        self.assertEqual(pretreat.reason_description_normalized, "Preventative De-Ice/Anti-Ice")
        self.assertEqual(pretreat.reason_for_application, "Pretreat")
        self.assertEqual(pretreat.outcome, "pretreat")
        self.assertEqual(ambiguous_gs.reason_code_raw, "GS")
        self.assertIsNone(ambiguous_gs.reason_description_normalized)
        self.assertEqual(ambiguous_gs.reason_for_application, "GS")

    def test_no_sort_calendar_excludes_only_locked_dates(self):
        no_sort_2026 = historical_no_sort_dates(2026)
        self.assertIn(date(2026, 5, 25), no_sort_2026)  # Memorial Day
        self.assertIn(date(2026, 9, 7), no_sort_2026)  # Labor Day
        self.assertIn(date(2026, 11, 25), no_sort_2026)  # Before Thanksgiving
        self.assertIn(date(2026, 11, 26), no_sort_2026)  # Thanksgiving
        self.assertIn(date(2026, 7, 4), no_sort_2026)
        self.assertIn(date(2026, 12, 25), no_sort_2026)
        self.assertIn(date(2026, 12, 31), no_sort_2026)
        self.assertIn(date(2026, 1, 1), no_sort_2026)
        self.assertIn(date(2026, 12, 24), no_sort_2026)
        self.assertIn(date(2026, 1, 19), no_sort_2026)  # MLK Day
        self.assertNotIn(date(2024, 1, 15), historical_no_sort_dates(2024))

        reconstructed = set(
            normal_operational_nights(date(2026, 2, 16), date(2026, 11, 26))
        )
        self.assertIn(date(2026, 2, 16), reconstructed)  # Presidents Day
        self.assertIn(date(2026, 11, 11), reconstructed)  # Veterans Day
        self.assertNotIn(date(2026, 5, 25), reconstructed)
        self.assertNotIn(date(2026, 11, 25), reconstructed)
        self.assertNotIn(date(2026, 11, 26), reconstructed)

    def test_excluded_no_sort_date_never_becomes_clean_negative(self):
        no_event = build_frost_history_dataset(
            (),
            None,
            start_date=date(2026, 11, 25),
            end_date=date(2026, 11, 25),
            departure_exposure_nights=(date(2026, 11, 25),),
        )
        self.assertEqual(no_event.night_evidence, ())
        self.assertEqual(no_event.training_records, ())

        frost = parse_cryotech_csv(
            "Application Date,Start Time,Tail,Reason\n"
            "11/25/2026,23:15,N801,F\n"
        )
        positive = build_frost_history_dataset(
            frost.rows,
            None,
            start_date=date(2026, 11, 25),
            end_date=date(2026, 11, 25),
        )
        self.assertEqual(positive.night_evidence[0].evidence_class, "confirmed_positive")
        self.assertEqual(positive.training_records[0].frost_label, "positive")

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
        dataset = build_frost_history_dataset(
            cryotech.rows,
            None,
            start_date="01/05/2026",
            end_date="01/05/2026",
        )
        record = dataset.training_records[0]

        payload = record.to_dict()
        self.assertEqual(payload["operational_night"], "2026-01-05")
        self.assertTrue(payload["exposure_timestamp_local"].endswith("-06:00"))
        self.assertIn(payload["frost_label"], {"positive", "negative", "unlabeled"})
        artifact = dataset.to_dict()
        self.assertEqual(artifact["schema_version"], 4)
        self.assertEqual(len(artifact["raw_application_rows"]), 2)
        self.assertEqual(len(artifact["treatment_events"]), 1)
        self.assertEqual(len(artifact["night_evidence"]), 1)


if __name__ == "__main__":
    unittest.main()
