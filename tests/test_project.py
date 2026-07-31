import tempfile
import unittest
from pathlib import Path
from app.models.project import Project

class ProjectTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "project.json"
            original = Project(project_name="Test", optimization="Lowest Cost")
            original.save(path)
            loaded = Project.load(path)
            self.assertEqual(loaded.project_name, "Test")
            self.assertEqual(loaded.optimization, "Lowest Cost")
