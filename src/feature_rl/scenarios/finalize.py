"""Joined ScenarioPlan validation against an exact frozen contract."""

from __future__ import annotations

from pydantic import ValidationError

from feature_rl.contracts import ArtifactRef, RequirementContract, ScenarioPlan
from feature_rl.requirements import GroundedSource, GroundingError, validate_link

from .models import ScenarioFinalizationInputs, ScenarioPlanProposal


class ScenarioJoinError(ValueError):
    """A scenario proposal conflicts with its frozen contract or controller policy."""


class ScenarioFinalizer:
    def finalize(
        self,
        proposal: ScenarioPlanProposal,
        contract: RequirementContract,
        inputs: ScenarioFinalizationInputs,
        sources: tuple[GroundedSource, ...],
        *,
        expected_contract: ArtifactRef,
    ) -> ScenarioPlan:
        proposal = ScenarioPlanProposal.model_validate(proposal)
        contract = RequirementContract.model_validate(contract)
        try:
            inputs = ScenarioFinalizationInputs.model_validate(inputs)
        except ValidationError as error:
            raise ScenarioJoinError(str(error)) from error
        expected_contract = ArtifactRef.model_validate(expected_contract)
        sources = tuple(GroundedSource.model_validate(source) for source in sources)
        if inputs.contract != expected_contract:
            raise ScenarioJoinError("scenario input does not identify the exact frozen contract")
        if not inputs.seed_policy.same_cases_within_group:
            raise ScenarioJoinError("scenario planning must use the same cases within each group")

        known_ids = {
            requirement.requirement_id
            for requirement in contract.requirements + contract.compatibility_obligations
        }
        mandatory_ids = tuple(
            requirement.requirement_id
            for requirement in contract.requirements
            if requirement.mandatory
        ) + tuple(
            requirement.requirement_id for requirement in contract.compatibility_obligations
        )
        scenario_ids = [scenario.scenario_id for scenario in proposal.scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ScenarioJoinError("duplicate scenario IDs")
        covered: set[str] = set()
        try:
            for scenario in proposal.scenarios:
                if scenario.reset_needs:
                    raise ScenarioJoinError(
                        f"scenario {scenario.scenario_id} has unsupported reset_needs; the current "
                        "adapter grants no additional reset capability. Each case uses a fresh "
                        "runtime and process. Describe local fixture creation in preconditions "
                        "and keep reset_needs=[] for self-contained cases; do not conceal "
                        "genuine cross-case state or external reset requirements."
                    )
                if any(identifier not in known_ids for identifier in scenario.requirement_ids):
                    raise ScenarioJoinError("scenario contains an unknown requirement ID")
                if any(
                    observation not in inputs.supported_observables
                    for observation in scenario.observations
                ):
                    raise ScenarioJoinError("scenario contains an unsupported observation")
                validate_link(scenario.oracle_origin, sources)
                covered.update(scenario.requirement_ids)
        except GroundingError as error:
            raise ScenarioJoinError(str(error)) from error
        if not set(mandatory_ids) <= covered:
            raise ScenarioJoinError("mandatory feature or compatibility coverage is incomplete")

        return ScenarioPlan(
            kind="ScenarioPlan",
            schema_version=1,
            visibility=inputs.visibility,
            provenance=inputs.provenance,
            costs=inputs.costs,
            contract=inputs.contract,
            mandatory_requirement_ids=mandatory_ids,
            scenarios=proposal.scenarios,
            seed_policy=inputs.seed_policy,
        )
