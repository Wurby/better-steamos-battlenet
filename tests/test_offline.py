import unittest
import urllib.request


class Offline(unittest.TestCase):
    def test_the_suite_cannot_reach_the_network(self):
        """Guards tests/__init__.py: unit tests must stay offline, fast and deterministic."""
        with self.assertRaises(OSError):
            urllib.request.urlopen("http://example.com")


if __name__ == "__main__":
    unittest.main()
