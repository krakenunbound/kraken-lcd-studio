import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

import lcd_gui


class CompanionTests(unittest.TestCase):
    def test_startup_command_is_hidden_background_launch(self):
        command = lcd_gui.startup_command()
        self.assertIn(f'"{Path(lcd_gui.__file__).resolve()}"', command)
        self.assertTrue(command.endswith(" --background"))

    def test_tray_icon_has_expected_size_and_content(self):
        icon = lcd_gui.tray_image()
        self.assertEqual(icon.size, (64, 64))
        self.assertEqual(icon.mode, "RGBA")
        self.assertGreater(len(icon.getcolors(maxcolors=1024)), 2)

    def test_old_project_media_path_migrates_to_new_location(self):
        with TemporaryDirectory() as temporary:
            app_dir = Path(temporary)
            expected = app_dir / "Sample Looping Videos" / "beachgirl.webm"
            expected.parent.mkdir()
            expected.touch()
            old_path = Path(r"C:\Old App Folder\Sample Looping Videos\beachgirl.webm")
            with patch.object(lcd_gui, "APP_DIR", app_dir):
                self.assertEqual(lcd_gui.resolve_media_path(old_path), expected)

    def test_overlay_defaults_are_transparent_and_color_buttons_keep_contrast(self):
        self.assertFalse(lcd_gui.DEFAULT_OVERLAY_VISUALS["panel_background"])
        self.assertEqual(lcd_gui.DEFAULT_OVERLAY_VISUALS["panel_opacity"], 0)
        self.assertEqual(lcd_gui.KrakenLcdApp.contrasting_text("#ffffff"), "#000000")
        self.assertEqual(lcd_gui.KrakenLcdApp.contrasting_text("#001020"), "#ffffff")


if __name__ == "__main__":
    unittest.main()
