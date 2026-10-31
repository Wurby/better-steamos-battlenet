"""Unit tests. They must never touch the network, so urlopen is disabled for the whole run;
a test that needs a download mocks `_fetch` / `download_installer` instead."""
import urllib.request


def _no_network(*args, **kwargs):
    raise OSError("network access is disabled in the unit tests")


urllib.request.urlopen = _no_network
