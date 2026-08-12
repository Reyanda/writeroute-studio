"""Regression tests for the studio's single-source trace control mode."""

import unittest

from app import resolve_control_mode, update_control_mode


class TestStudioControls(unittest.TestCase):
    def test_control_modes_are_mutually_exclusive(self):
        self.assertEqual(resolve_control_mode("Automatic"), (True, False))
        self.assertEqual(resolve_control_mode("Preset"), (False, False))
        self.assertEqual(resolve_control_mode("Custom"), (False, True))

    def test_custom_mode_updates_every_control(self):
        updates = update_control_mode("Custom")
        self.assertEqual(len(updates), 14)
        self.assertTrue(updates[0]["interactive"])
        for update in updates[1:12]:
            self.assertTrue(update["interactive"])
        self.assertFalse(updates[12]["value"])
        self.assertFalse(updates[12]["interactive"])

    def test_unknown_control_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_control_mode("Automatic and Custom")


if __name__ == "__main__":
    unittest.main()
