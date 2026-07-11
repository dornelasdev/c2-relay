from unittest.mock import patch

from c2_relay.agent.metadata import collect_metadata


def test_collect_metadata_uses_cross_platform_python_apis() -> None:
    with (
        patch("c2_relay.agent.metadata.platform.node", return_value="host"),
        patch("c2_relay.agent.metadata.platform.system", return_value="Linux"),
        patch("c2_relay.agent.metadata.platform.machine", return_value=""),
        patch("c2_relay.agent.metadata.getpass.getuser", return_value="user"),
    ):
        metadata = collect_metadata()

    assert metadata.hostname == "host"
    assert metadata.username == "user"
    assert metadata.architecture is None
