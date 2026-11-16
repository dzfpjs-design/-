"""Service selection regression, without starting processes or opening the database."""
import unittest
from local_runtime import services


class ServiceSelectionTest(unittest.TestCase):
    def test_setup_does_not_start_legacy_policy_worker(self):
        self.assertEqual(services("setup"), [("api", "serve"), ("setup-worker", "setup-worker")])
        self.assertNotIn(("worker", "worker"), services("setup"))

    def test_legacy_requires_explicit_selection(self):
        self.assertEqual(services("legacy"), [("api", "serve"), ("worker", "worker")])
        self.assertEqual(len(set(services("all"))), 3)


if __name__ == "__main__":
    unittest.main()
