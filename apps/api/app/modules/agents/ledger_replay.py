"""Idempotent confirmation of ledger writes owned by deterministic domains."""

from app.modules.agents.repository import AgentRunRepository
from app.modules.agents.tools import AgentToolContext, WriteLedgerEventInput


class LedgerWriteReplayError(ValueError):
    code = "ledger_write_proof_mismatch"


class DomainLedgerWriteReplay:
    """The ledger tool cannot manufacture an event or commit a decision.

    Domain services atomically create authoritative events with their artifacts.
    This adapter confirms the exact event and its upstream links for the current
    run. Repeated calls return that immutable event and perform no extra writes.
    """

    def __init__(self, repository: AgentRunRepository):
        self._repository = repository

    async def __call__(self, context: AgentToolContext, arguments: WriteLedgerEventInput):
        if context.run_id is None or arguments.event_type.startswith("approval."):
            raise LedgerWriteReplayError
        proof = await self._repository.domain_ledger_write_proof(
            company_id=context.company_id, run_id=context.run_id,
            event_type=arguments.event_type, subject_type=arguments.subject_type,
            subject_id=arguments.subject_id, payload_hash=arguments.payload_hash,
        )
        if proof is None:
            raise LedgerWriteReplayError
        event_id, payload_hash, sources, facts, evidence, method_id = proof
        if (not set(arguments.source_event_ids) <= sources
                or not set(arguments.fact_ids) <= facts
                or not set(arguments.evidence_item_ids) <= evidence
                or (arguments.method_definition_id is not None
                    and arguments.method_definition_id != method_id)):
            raise LedgerWriteReplayError
        return event_id, payload_hash
