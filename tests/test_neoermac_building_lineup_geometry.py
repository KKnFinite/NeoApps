import unittest
from datetime import time

from app import create_app
from app.extensions import db
from app.models import MasterFlightSchedule
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoermac_building_lineup import (
    DESTINATION_FIELDS,
    building_lineup_slot_descriptors,
    get_building_lineup_destinations_for_door,
    get_building_lineup_doors_by_destination,
    get_building_lineup_rows,
    save_building_lineup_destination,
)


class NeoErmacBuildingLineupGeometryTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "NeoErmacBuildingLineupGeometryConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = ensure_default_gateway_and_nodes()
        for index, destination in enumerate(("ONT", "ONT1", "ONT2", "SDF"), start=1):
            db.session.add(
                MasterFlightSchedule(
                    gateway_id=self.gateway.id,
                    gateway_code=self.gateway.code,
                    sort_name="night",
                    mission_type="departure",
                    flight_number=f"UPS90{index}",
                    origin=self.gateway.code,
                    destination=destination,
                    active=True,
                    active_days=(
                        "monday,tuesday,wednesday,thursday,friday,saturday,sunday"
                    ),
                    planned_time_local=time(23, 0),
                    timezone="America/Chicago",
                )
            )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_each_runout_has_eight_canonical_slots(self):
        rows = get_building_lineup_rows(self.gateway)

        self.assertEqual(len(DESTINATION_FIELDS), 8)
        self.assertEqual(len(rows), 12)
        for row in rows:
            with self.subTest(runout=row.runout_key):
                descriptors = building_lineup_slot_descriptors(
                    row,
                    include_blank=True,
                )
                self.assertEqual(len(descriptors), 8)
                self.assertEqual(
                    {
                        (
                            descriptor["belt_number"],
                            descriptor["side"],
                            descriptor["slot_number"],
                        )
                        for descriptor in descriptors
                    },
                    {
                        (belt, side, slot)
                        for belt in (1, 2)
                        for side in ("east", "west")
                        for slot in (1, 2)
                    },
                )

    def test_east_and_west_face_only_the_physical_endpoint_doors(self):
        self._save("green_runout", "east_destination_1", "ONT")
        self._save("green_runout", "west_destination_1", "SDF")
        self._save("runout_11", "east_destination_1", "ONT1")
        self._save("runout_11", "west_destination_1", "ONT2")

        doors = get_building_lineup_doors_by_destination(self.gateway)

        self.assertEqual(doors["ONT"], ("D1",))
        self.assertEqual(doors["SDF"], ("D4",))
        self.assertEqual(doors["ONT1"], ("D34",))
        self.assertEqual(doors["ONT2"], ("D37",))
        self.assertNotIn("ONT", get_building_lineup_destinations_for_door(self.gateway, "D4"))
        self.assertNotIn("ONT2", get_building_lineup_destinations_for_door(self.gateway, "D34"))

    def test_duplicate_is_rejected_only_within_one_physical_belt_side(self):
        self._save("green_runout", "east_destination_1", "ONT")

        with self.assertRaisesRegex(ValueError, "both destination slots"):
            self._save("green_runout", "east_destination_1_slot_2", "ONT")

        self._save("green_runout", "east_destination_2", "ONT")
        self._save("green_runout", "west_destination_1", "ONT")
        db.session.commit()

        doors = get_building_lineup_doors_by_destination(self.gateway)
        self.assertEqual(doors["ONT"], ("D1", "D4"))

    def test_distinct_operational_destinations_share_one_belt_side(self):
        self._save("green_runout", "east_destination_1", "ONT1")
        self._save("green_runout", "east_destination_1_slot_2", "ONT2")
        db.session.commit()

        destinations = get_building_lineup_destinations_for_door(self.gateway, "D1")
        self.assertEqual(set(destinations), {"ONT1", "ONT2"})

    def test_repeated_destination_on_one_door_is_one_requirement(self):
        self._save("green_runout", "east_destination_1", "ONT")
        self._save("green_runout", "east_destination_2", "ONT")
        db.session.commit()

        destinations = get_building_lineup_destinations_for_door(self.gateway, "D1")
        doors = get_building_lineup_doors_by_destination(self.gateway)

        self.assertEqual(tuple(destinations), ("ONT",))
        self.assertEqual(len(destinations["ONT"]), 2)
        self.assertEqual(doors["ONT"], ("D1",))

    def test_destination_on_opposite_sides_requires_both_doors(self):
        self._save("green_runout", "east_destination_1", "ONT")
        self._save("green_runout", "west_destination_2", "ONT")
        db.session.commit()

        self.assertEqual(
            get_building_lineup_doors_by_destination(self.gateway)["ONT"],
            ("D1", "D4"),
        )

    def _save(self, runout_key, field_name, destination):
        return save_building_lineup_destination(
            self.gateway,
            f"lineup_{runout_key}_{field_name}",
            destination,
        )


if __name__ == "__main__":
    unittest.main()
