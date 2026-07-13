import unittest

from PIL import Image, ImageChops

from lcd_metrics import OverlayStyle, PerformanceOverlayRenderer, _flatten_lhm, _number


class MetricsOverlayTests(unittest.TestCase):
    def test_cycle_uses_each_enabled_card(self):
        style = OverlayStyle(cycle_seconds=3, cards=("cpu", "gpu", "memory", "clock"))
        renderer = PerformanceOverlayRenderer(None, style, started=0)
        self.assertEqual(renderer.card_at(0), "cpu")
        self.assertEqual(renderer.card_at(3), "gpu")
        self.assertEqual(renderer.card_at(6), "memory")
        self.assertEqual(renderer.card_at(9), "clock")
        self.assertEqual(renderer.card_at(12), "cpu")

    def test_render_is_exact_panel_size_and_changes_background(self):
        source = Image.new("RGB", (480, 128), "#32506d")
        rendered = PerformanceOverlayRenderer(None, OverlayStyle()).render(source, forced_card="gpu")
        self.assertEqual(rendered.size, (480, 128))
        self.assertIsNotNone(ImageChops.difference(source, rendered).getbbox())

    def test_transparent_mode_does_not_dim_unused_background(self):
        source = Image.new("RGB", (480, 128), "#32506d")
        style = OverlayStyle(panel_background=False, panel_opacity=0)
        rendered = PerformanceOverlayRenderer(None, style).render(source, forced_card="cpu")
        self.assertEqual(rendered.getpixel((470, 10)), source.getpixel((470, 10)))

    def test_lhm_values_are_flattened_with_sensor_path(self):
        data = {
            "Text": "Computer",
            "Children": [
                {
                    "Text": "CPU",
                    "Children": [
                        {"Text": "Temperatures", "Children": [{"Text": "CPU Package", "Value": "54.0 °C"}]}
                    ],
                }
            ],
        }
        values = _flatten_lhm(data)
        self.assertIn(("computer / cpu / temperatures / cpu package", 54.0), values)

    def test_number_parses_sensor_units(self):
        self.assertEqual(_number("1,860 MHz"), 1860)
        self.assertEqual(_number("65.0 °C"), 65)


if __name__ == "__main__":
    unittest.main()
