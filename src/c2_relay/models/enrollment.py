"""Non-secret records for single-use agent enrollment grants."""

from typing import NewType
from uuid import UUID

from pydantic import model_validator

from c2_relay.models.common import AgentId, DomainModel, UtcDateTime
from c2_relay.models.operators import OperatorId

EnrollmentGrantId = NewType("EnrollmentGrantId", UUID)


class EnrollmentGrant(DomainModel):
    id: EnrollmentGrantId
    issued_by_operator_id: OperatorId
    created_at: UtcDateTime
    expires_at: UtcDateTime
    redeemed_at: UtcDateTime | None = None
    redeemed_by_agent_id: AgentId | None = None
    revoked_at: UtcDateTime | None = None
    revoked_by_operator_id: OperatorId | None = None

    @model_validator(mode="after")
    def lifecycle_is_consistent(self) -> "EnrollmentGrant":
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must follow created_at")
        if (self.redeemed_at is None) != (self.redeemed_by_agent_id is None):
            raise ValueError("redemption requires both a timestamp and an agent")
        if (self.revoked_at is None) != (self.revoked_by_operator_id is None):
            raise ValueError("revocation requires both a timestamp and an operator")
        if self.redeemed_at is not None and self.revoked_at is not None:
            raise ValueError("a grant cannot be both redeemed and revoked")
        if self.redeemed_at is not None and not (
            self.created_at <= self.redeemed_at < self.expires_at
        ):
            raise ValueError("redemption must occur within the grant lifetime")
        if self.revoked_at is not None and self.revoked_at < self.created_at:
            raise ValueError("revoked_at cannot precede created_at")
        return self
