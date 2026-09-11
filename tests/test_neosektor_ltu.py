"""Numeric LTU includes configured upstream work; lifecycle gates stay separate."""
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.services.neosektor_live_counts import (
    _google_mirror_left_to_unload_values,
    _numeric_wave_left_to_unload,
    _wave_views,
)


class NeoSektorLtuTest(unittest.TestCase):
    def views(self, *, first_lta=22, second_lta=22, east=(2, 2), west=(0, 0),
              openings=(4, 4), modifiers=(45, 37), first_down=False, second_down=False):
        now = datetime(2026, 9, 11, 5)
        rows = [SimpleNamespace(wave_name=name, planned_count=lta, unloaded_count=0,
                    status='Empty', all_up_started_at=now - timedelta(minutes=16) if down else None)
                for name, lta, down in [('1ST WAVE', first_lta, first_down),
                                        ('2ND WAVE', second_lta, second_down)]]
        sides = {side: {'open_bays': opened, 'waves': [
                    {'key': key, 'count': count} for key, count in zip(('first', 'second'), counts)]}
                 for side, counts, opened in [('east', east, openings[0]), ('west', west, openings[1])]}
        settings = SimpleNamespace(first_wave_unload_modifier=modifiers[0],
            second_wave_unload_modifier=modifiers[1], all_up_to_down_minutes=15)
        before = [vars(row).copy() for row in rows]
        result = _wave_views(rows, sides, settings, now=now, persist_timer=False)
        self.assertEqual([vars(row) for row in rows], before)
        return result

    def test_required_numeric_examples_and_spare_capacity(self):
        # Arguments are LTA, East back row, West back row, East/West openings, modifier.
        for values, expected in [
            ((22, 2, 0, 4, 4, 45), 61),
            ((22, 4, 2, 4, 2, 45), 67),
            ((11, 6, 3, 4, 5, 45), 56),
            ((11, 6, 8, 4, 5, 45), 61),  # Both waiting: full modifier.
            ((22, 0, 0, 0, 0, 45), 67),  # No waiting is not zero upstream work.
            ((22, 0, 0, 30, 30, 45), 22),  # Modifier floors at zero.
            ((11, 6, 3, 4, 5, 1), 13),  # Spare never erases residual waiting.
        ]:
            with self.subTest(values=values):
                self.assertEqual(_numeric_wave_left_to_unload(*values), expected)

    def test_all_first_wave_numeric_examples_use_configured_modifier(self):
        for lta, east, west, openings, modifier, expected in [
            (22, 2, 0, (4, 4), 45, 61), (22, 4, 2, (4, 2), 45, 67),
            (11, 6, 3, (4, 5), 45, 56), (22, 2, 0, (4, 4), 20, 36),
        ]:
            with self.subTest(modifier=modifier, lta=lta, openings=openings):
                state = self.views(first_lta=lta, east=(east, 0), west=(west, 0),
                                   openings=openings, modifiers=(modifier, 37))
                self.assertEqual(state[0]['left'], expected)
                self.assertEqual(state[0]['left_to_unload'], expected)

    def test_every_numeric_second_wave_phase_uses_its_configured_modifier(self):
        for active in (False, True):
            for modifier, expected in [(37, 53), (19, 35), (0, 22)]:
                with self.subTest(active=active, modifier=modifier):
                    state = self.views(first_lta=0 if active else 10, first_down=active,
                        east=(0, 2), west=(0, 0), modifiers=(45, modifier))
                    self.assertEqual(state[1]['left'], expected)

    def test_inactive_second_wave_zero_counts_still_include_upstream_work(self):
        state = self.views(first_lta=10, second_lta=0, east=(0, 0), west=(0, 0), openings=(0, 0))
        self.assertEqual(state[1]['left'], 37)

    def test_modifiers_do_not_prevent_all_up_or_change_pending_and_down(self):
        kwargs = dict(first_lta=0, second_lta=0, east=(3, 2), west=(2, 3),
                      openings=(4, 4), modifiers=(999, 999))
        self.assertEqual([w['left'] for w in self.views(**kwargs)], ['ALL UP', 'PENDING'])
        self.assertEqual([w['left'] for w in self.views(**kwargs, first_down=True)], ['DOWN', 'ALL UP'])
        self.assertEqual([w['left'] for w in self.views(**kwargs, first_down=True, second_down=True)], ['DOWN', 'DOWN'])

    def test_google_mirror_uses_corrected_canonical_numbers_and_keeps_labels(self):
        self.assertEqual(_google_mirror_left_to_unload_values(self.views()), {'E2': 61, 'E3': 53})
        state = self.views(first_lta=0, second_lta=0, east=(0, 0), west=(0, 0), modifiers=(999, 999))
        self.assertEqual(_google_mirror_left_to_unload_values(state), {'E2': 'ALL UP', 'E3': 'PENDING'})


if __name__ == '__main__':
    unittest.main()
