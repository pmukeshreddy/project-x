"""Deterministic evidence grounding and M0 RequirementContract finalization."""

from __future__ import annotations

from pydantic import ValidationError

from feature_rl.contracts import EvidenceLink, RequirementContract

from .models import ContractFinalizationInputs, GroundedSource, RequirementContractProposal


class GroundingError(ValueError):
    """A proposal cannot be joined to its declared admissible evidence."""


def _catalog(sources: tuple[GroundedSource, ...]) -> dict[tuple[object, str], GroundedSource]:
    by_key: dict[tuple[object, str], GroundedSource] = {}
    context_ids: set[str] = set()
    for source in sources:
        if source.context_id in context_ids:
            raise GroundingError("duplicate grounded context ID")
        context_ids.add(source.context_id)
        key = (source.source, source.locator)
        if key in by_key:
            raise GroundingError("duplicate source and locator in grounding allowlist")
        by_key[key] = source
    return by_key


def validate_link(link: EvidenceLink, sources: tuple[GroundedSource, ...]) -> None:
    catalog = _catalog(sources)
    grounded = catalog.get((link.source, link.locator))
    if grounded is None:
        raise GroundingError("evidence source or locator is outside the authoring allowlist")
    if link.provenance_label != grounded.provenance_label:
        raise GroundingError("evidence provenance label does not match its grounded source")
    if link.quote not in grounded.text:
        raise GroundingError("evidence quote is not present at the declared locator")


class ContractFinalizer:
    def finalize(
        self,
        proposal: RequirementContractProposal,
        inputs: ContractFinalizationInputs,
        sources: tuple[GroundedSource, ...],
    ) -> RequirementContract:
        proposal = RequirementContractProposal.model_validate(proposal)
        inputs = ContractFinalizationInputs.model_validate(inputs)
        sources = tuple(GroundedSource.model_validate(source) for source in sources)
        _catalog(sources)
        request_sources = [source for source in sources if source.role == "request"]
        if len(request_sources) != 1:
            raise GroundingError("exactly one resolved authoring request is required")
        request_source = request_sources[0]
        if (
            request_source.text != inputs.visible_request
            or request_source.provenance_label != inputs.provenance_label
            or request_source.source not in inputs.provenance.inputs
        ):
            raise GroundingError(
                "visible request or provenance differs from resolved authoring evidence"
            )
        runtime_sources = [
            source for source in sources if source.source == inputs.runtime_discovery
        ]
        if len(runtime_sources) != 1:
            raise GroundingError("exact validated runtime discovery context is required")
        try:
            from .discovery import ClickDiscoveryObservation

            discovery = ClickDiscoveryObservation.model_validate_json(runtime_sources[0].text)
        except (ValidationError, ValueError) as error:
            raise GroundingError("runtime discovery context is invalid") from error
        if any(entry not in discovery.entry_points for entry in inputs.entry_points):
            raise GroundingError("controller entry point is absent from runtime discovery")
        if any(
            observable not in discovery.supported_observables
            for observable in inputs.supported_observables
        ):
            raise GroundingError("controller observable is absent from runtime discovery")

        requirements = proposal.requirements + proposal.compatibility_obligations
        identifiers = tuple(requirement.requirement_id for requirement in requirements)
        if len(identifiers) != len(set(identifiers)):
            raise GroundingError("duplicate requirement IDs")
        if not set(identifiers) <= set(inputs.allowed_requirement_ids):
            raise GroundingError("proposal requirement ID is outside the bounded namespace")
        if any(entry not in inputs.entry_points for entry in proposal.entry_points):
            raise GroundingError("proposal entry point is unsupported by discovery")
        if proposal.allowed_changes != inputs.allowed_changes:
            raise GroundingError("proposal allowed changes differ from controller policy")
        for requirement in requirements:
            if requirement.observable not in inputs.supported_observables:
                raise GroundingError("requirement observable is unsupported by discovery")
            for link in requirement.evidence:
                validate_link(link, sources)
        for ambiguity in proposal.ambiguities:
            if ambiguity.disposition == "unresolved":
                raise GroundingError("unresolved ambiguity blocks automatic finalization")
            for link in ambiguity.evidence:
                validate_link(link, sources)

        return RequirementContract(
            kind="RequirementContract",
            schema_version=1,
            visibility=inputs.visibility,
            provenance=inputs.provenance,
            costs=inputs.costs,
            visible_request=inputs.visible_request,
            capability=proposal.capability,
            entry_points=proposal.entry_points,
            requirements=proposal.requirements,
            compatibility_obligations=proposal.compatibility_obligations,
            ambiguities=proposal.ambiguities,
            allowed_changes=proposal.allowed_changes,
            public_checks=inputs.public_checks,
            episode_limits=inputs.episode_limits,
            provenance_label=inputs.provenance_label,
        )
