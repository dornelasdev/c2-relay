from importlib.metadata import version

import c2_relay


def test_package_exposes_installed_version() -> None:
    assert c2_relay.__version__ == version("c2-relay")
