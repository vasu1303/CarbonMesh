from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class WorkspaceOptionKind(StrEnum):
    actors = "actors"
    metrics = "metrics"
    methods = "methods"
    policies = "policies"
    imports = "imports"
    documents = "documents"
    activity = "activity"
    evidence = "evidence"
    measurements = "measurements"
    suppliers = "suppliers"
    products = "products"
    standards = "standards"
    loads = "loads"
    forecasts = "forecasts"
    assurance = "assurance"
    procurement = "procurement"
    dispatch = "dispatch"
    runs = "runs"
    ledger = "ledger"


class WorkspaceActorRole(StrEnum):
    sustainability_analyst = "sustainability_analyst"
    procurement_manager = "procurement_manager"
    approver = "approver"
    auditor = "auditor"
    system = "system"


class WorkspaceOptionsQuery(BaseModel):
    company_id: UUID
    kind: WorkspaceOptionKind
    id: UUID | None = None
    role: WorkspaceActorRole | None = None
    site_id: UUID | None = None
    reporting_period_id: UUID | None = None
    search: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10_000)

    @model_validator(mode="after")
    def validate_actor_role(self):
        if self.role is not None and self.kind != WorkspaceOptionKind.actors:
            raise ValueError("role is supported only for actors")
        return self


class WorkspaceOption(BaseModel):
    id: UUID
    label: str
    description: str | None
    status: str | None
    role: str | None


class WorkspaceOptionsResult(BaseModel):
    items: list[WorkspaceOption]
    total: int
    limit: int
    offset: int
