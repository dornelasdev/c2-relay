"""Local host metadata collection."""

import getpass
import platform

from c2_relay import __version__
from c2_relay.models import AgentMetadata


def collect_metadata() -> AgentMetadata:
    return AgentMetadata(
        hostname=platform.node(),
        operating_system=platform.system(),
        username=getpass.getuser(),
        agent_version=__version__,
        architecture=platform.machine() or None,
    )
