import unittest
import threading
from pathlib import Path
from unittest.mock import patch

from lcd_media import Crop, MediaInfo, exact_filter, maximum_crop, quality_to_qscale, stream_media


class CropTests(unittest.TestCase):
    def test_16_by_9_crop_has_lcd_aspect(self):
        crop = maximum_crop(1920, 1080)
        self.assertAlmostEqual((crop.width * 1920) / (crop.height * 1080), 480 / 128)
        self.assertAlmostEqual(crop.x, 0)
        self.assertAlmostEqual(crop.y, (1 - crop.height) / 2)

    def test_ultrawide_source_crops_sides(self):
        crop = maximum_crop(1000, 200)
        self.assertAlmostEqual(crop.height, 1)
        self.assertGreater(crop.x, 0)

    def test_clamp_keeps_crop_inside_source(self):
        crop = Crop(-1, 4, 0.5, 0.5).clamped()
        self.assertEqual(crop.x, 0)
        self.assertEqual(crop.y, 0.5)

    def test_filter_has_exact_output(self):
        value = exact_filter(Crop(0, 0.25, 1, 0.5), 90, speed=1.5, fps=8)
        self.assertIn("transpose=1", value)
        self.assertIn("scale=480:128", value)
        self.assertIn("setpts=PTS/1.500000", value)
        self.assertIn("fps=8.000000", value)

    def test_quality_mapping(self):
        self.assertLess(quality_to_qscale(95), quality_to_qscale(35))

    def test_static_media_refreshes_while_live_overlay_is_enabled(self):
        stop = threading.Event()

        class FakeLcd:
            def __init__(self):
                self.frames = []
                self.heartbeat_started = False
                self.heartbeat_stopped = False

            def start_heartbeat(self):
                self.heartbeat_started = True

            def stop_heartbeat(self):
                self.heartbeat_stopped = True

            def send_jpeg(self, frame, log_frame=False):
                self.frames.append(frame)
                stop.set()

        lcd = FakeLcd()
        info = MediaInfo(Path("still.jpg"), 480, 128, 0, 0, "mjpeg", 1, 100, False)
        with patch("lcd_media.render_lcd_jpeg", return_value=b"base"):
            count = stream_media(
                lcd,
                info,
                Crop(0, 0, 1, 1),
                0,
                8,
                85,
                1,
                True,
                stop,
                frame_transform=lambda frame: frame + b"-overlay",
            )
        self.assertEqual(count, 1)
        self.assertEqual(lcd.frames, [b"base-overlay"])
        self.assertTrue(lcd.heartbeat_started)
        self.assertTrue(lcd.heartbeat_stopped)


if __name__ == "__main__":
    unittest.main()
