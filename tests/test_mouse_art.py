"""The mouse drawing: one shape per supported model, original artwork only, wired into the Mouse page."""
import re
import unittest
from pathlib import Path

from wheelguard.gui import app
from wheelguard.hw import models

QML = Path(app.QML_DIR) / "Wg"
ART = (QML / "MouseArt.qml").read_text()
PAGE = (QML / "MousePage.qml").read_text()


class MouseArtTests(unittest.TestCase):
    def test_every_supported_model_has_a_drawing(self):
        drawn = set(re.findall(r'^\s+"([a-z0-9-]+)": \{$', ART, re.M))
        self.assertEqual(drawn, set(models.MODELS))

    def test_each_drawing_has_all_parts(self):
        for key in models.MODELS:
            block = ART[ART.index(f'"{key}"'):]
            block = block[:block.index("}")]
            for part in ("left:", "right:", "palm:", "wheel:", "side:"):
                self.assertIn(part, block, f"{key} lacks {part}")

    def test_registered_and_used_on_the_mouse_page(self):
        self.assertIn("MouseArt 1.0 MouseArt.qml", (QML / "qmldir").read_text())
        self.assertGreaterEqual(PAGE.count("MouseArt {"), 2)       # the header and the Button actions card

    def test_button_rows_light_the_matching_button(self):
        self.assertIn("root.setHot(br.index, hovered)", PAGE)
        self.assertIn("root.setHot(br.index, activeFocus)", PAGE)
        self.assertIn("root.setHot(fr.index, hovered)", PAGE)
        for i, name in enumerate(("left button", "right button", "middle click", "back button", "forward button")):
            self.assertIn(name, ART)
            self.assertEqual(["Left button", "Right button", "Middle click", "Back (side)", "Forward (side)"][i].split()[0].lower(),
                             name.split()[0])

    def test_no_brand_text_in_the_artwork(self):
        text = re.sub(r'Accessible\.name:.*?\n\s+\+ ', "", ART, flags=re.S)       # the accessible names are plain model names
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))
        self.assertNotIn("Text {", code)
        self.assertNotIn("Image {", code)

    def test_picture_is_exposed_to_assistive_tech(self):
        self.assertIn("Accessible.role: Accessible.Graphic", ART)


if __name__ == "__main__":
    unittest.main()
