import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def dependency_name(requirement: str) -> str:
    return re.split(r"[<>=!~\[]", requirement, maxsplit=1)[0].strip().lower()


class ReleaseMetadataTests(unittest.TestCase):
    def test_version_files_match(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        version_file = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(project["project"]["version"], version_file)

    def test_requirements_match_project_dependencies(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project_names = {dependency_name(value) for value in project["project"]["dependencies"]}
        requirements = [
            line.strip()
            for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual({dependency_name(value) for value in requirements}, project_names)

    def test_release_installation_files_exist(self):
        for name in (
            "README.md",
            "INSTALL.md",
            "LICENSE",
            "requirements.txt",
            "Install Kraken LCD Studio.cmd",
            "Launch Kraken LCD Studio.cmd",
        ):
            self.assertTrue((ROOT / name).is_file(), name)

    def test_packaging_includes_metrics_module(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("lcd_metrics", project["tool"]["setuptools"]["py-modules"])


if __name__ == "__main__":
    unittest.main()
