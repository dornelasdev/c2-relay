import pytest
from pydantic import TypeAdapter, ValidationError

from c2_relay.models import (
    ActionOutput,
    ActionRequest,
    CurrentUserAction,
    CurrentUserOutput,
    HostnameAction,
    HostnameOutput,
    OperatingSystemAction,
    OperatingSystemOutput,
)


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        ({"kind": "host.hostname"}, HostnameAction),
        ({"kind": "host.current_user"}, CurrentUserAction),
        ({"kind": "host.operating_system"}, OperatingSystemAction),
    ],
)
def test_action_request_uses_a_discriminated_allowlist(
    payload: dict[str, str], expected_type: type[object]
) -> None:
    action: ActionRequest = TypeAdapter(ActionRequest).validate_python(payload)

    assert isinstance(action, expected_type)


def test_action_request_rejects_an_unknown_action() -> None:
    with pytest.raises(ValidationError, match="Input tag 'shell.execute'"):
        TypeAdapter(ActionRequest).validate_python({"kind": "shell.execute"})


@pytest.mark.parametrize(
    ("output", "expected_type"),
    [
        ({"kind": "host.hostname", "hostname": "relay-host"}, HostnameOutput),
        ({"kind": "host.current_user", "username": "operator"}, CurrentUserOutput),
        (
            {
                "kind": "host.operating_system",
                "system": "Linux",
                "release": "6.1",
                "version": "build-1",
                "machine": "x86_64",
            },
            OperatingSystemOutput,
        ),
    ],
)
def test_action_output_is_structured(output: dict[str, str], expected_type: type[object]) -> None:
    value: ActionOutput = TypeAdapter(ActionOutput).validate_python(output)

    assert isinstance(value, expected_type)


def test_action_output_is_bounded() -> None:
    with pytest.raises(ValidationError, match="at most 4096 characters"):
        HostnameOutput(hostname="x" * 4097)
