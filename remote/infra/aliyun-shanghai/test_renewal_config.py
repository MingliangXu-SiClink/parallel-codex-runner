import configparser
from pathlib import Path
import unittest


class RenewalConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = configparser.ConfigParser(interpolation=None)
        self.config.read(Path(__file__).with_name("validation-window.conf"))

    def test_renewal_starts_and_waits_for_original_web_server(self):
        unit = self.config["Unit"]
        self.assertIn("nginx.service", unit["Requires"].split())
        self.assertIn("nginx.service", unit["After"].split())

    def test_validation_exception_is_bounded_and_always_removed(self):
        service = self.config["Service"]
        self.assertEqual(service["ExecStartPre"],
                         "/usr/local/sbin/pcr-acme-validation-window open")
        self.assertEqual(service["ExecStopPost"],
                         "/usr/local/sbin/pcr-acme-validation-window close")
        self.assertEqual(service["TimeoutStartSec"], "5min")
        self.assertEqual(service["TimeoutStopSec"], "30s")
        self.assertNotIn("ExecStart", service)


if __name__ == "__main__":
    unittest.main()
