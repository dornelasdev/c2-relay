"""Allowlisted action requests and their structured outputs."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from c2_relay.models.common import DomainModel

OutputValue = Annotated[str, StringConstraints(min_length=1, max_length=4096)]


class ActionKind(StrEnum):
    HOSTNAME = "host.hostname"
    CURRENT_USER = "host.current_user"
    OPERATING_SYSTEM = "host.operating_system"


class HostnameAction(DomainModel):
    kind: Literal[ActionKind.HOSTNAME] = ActionKind.HOSTNAME


class CurrentUserAction(DomainModel):
    kind: Literal[ActionKind.CURRENT_USER] = ActionKind.CURRENT_USER


class OperatingSystemAction(DomainModel):
    kind: Literal[ActionKind.OPERATING_SYSTEM] = ActionKind.OPERATING_SYSTEM


ActionRequest = Annotated[
    HostnameAction | CurrentUserAction | OperatingSystemAction,
    Field(discriminator="kind"),
]


class HostnameOutput(DomainModel):
    kind: Literal[ActionKind.HOSTNAME] = ActionKind.HOSTNAME
    hostname: OutputValue


class CurrentUserOutput(DomainModel):
    kind: Literal[ActionKind.CURRENT_USER] = ActionKind.CURRENT_USER
    username: OutputValue


class OperatingSystemOutput(DomainModel):
    kind: Literal[ActionKind.OPERATING_SYSTEM] = ActionKind.OPERATING_SYSTEM
    system: OutputValue
    release: OutputValue
    version: OutputValue
    machine: OutputValue


ActionOutput = Annotated[
    HostnameOutput | CurrentUserOutput | OperatingSystemOutput,
    Field(discriminator="kind"),
]
