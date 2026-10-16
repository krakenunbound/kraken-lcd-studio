import unittest
from types import SimpleNamespace
from unittest.mock import patch

import thermaltake_lcd
from thermaltake_lcd import (
    STATE_BUSY,
    STATE_MISSING,
    STATE_READY,
    LcdBusyError,
    LcdError,
    ThermaltakeLcd,
)


def fake_interface(index: int, opened: bool = True):
    return SimpleNamespace(
        device_path=rf"\\?\hid#vid_264a&pid_233d&mi_0{index}#8&1&0&0000#{{guid}}",
        vendor_id=thermaltake_lcd.VENDOR_ID if opened else 0,
        serial_number="A247393OG000988" if opened else "",
    )


class TransportStatusTests(unittest.TestCase):
    def test_status_ready_when_both_interfaces_open(self):
        devices = [fake_interface(0), fake_interface(1)]
        with patch.object(ThermaltakeLcd, "discover", return_value=devices):
            self.assertEqual(ThermaltakeLcd.status(), (STATE_READY, "A247393OG000988"))

    def test_status_busy_when_present_but_held_by_another_program(self):
        devices = [fake_interface(0, opened=False), fake_interface(1, opened=False)]
        with patch.object(ThermaltakeLcd, "discover", return_value=devices):
            self.assertEqual(ThermaltakeLcd.status(), (STATE_BUSY, ""))

    def test_status_missing_when_interfaces_absent(self):
        with patch.object(ThermaltakeLcd, "discover", return_value=[]):
            self.assertEqual(ThermaltakeLcd.status(), (STATE_MISSING, ""))

    def test_connect_reports_busy_instead_of_not_found(self):
        devices = [fake_interface(0, opened=False), fake_interface(1)]
        with patch.object(ThermaltakeLcd, "discover", return_value=devices):
            with self.assertRaises(LcdBusyError):
                ThermaltakeLcd().connect()

    def test_connect_reports_missing_interfaces(self):
        with patch.object(ThermaltakeLcd, "discover", return_value=[fake_interface(0)]):
            with self.assertRaises(LcdError) as raised:
                ThermaltakeLcd().connect()
        self.assertNotIsInstance(raised.exception, LcdBusyError)
        self.assertIn("not found", str(raised.exception))

    def test_failed_handshake_releases_hid_handles(self):
        lcd = ThermaltakeLcd()
        with patch.object(lcd, "connect"), patch.object(
            lcd, "initialize", side_effect=LcdError("LCD did not respond")
        ), patch.object(lcd, "close") as close:
            with self.assertRaises(LcdError):
                with lcd:
                    pass
        close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
