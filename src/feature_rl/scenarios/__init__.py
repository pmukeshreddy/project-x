"""Controller-owned requirement slots for compact behavioral checker authoring."""
from feature_rl import contracts as c


def build_scenario_plan(contract, contract_ref, seed_policy, *, provenance, costs):
    """Use one slot per requirement; the checker author supplies behavioral cases."""
    requirements = contract.requirements + contract.compatibility_obligations
    if seed_policy.algorithm != 'm4-sha256-v1' or not seed_policy.same_cases_within_group:
        raise ValueError('checker requires deterministic same-case seeds')
    return c.ScenarioPlan(kind='ScenarioPlan', schema_version=1, visibility=c.Visibility.PRIVATE,
        provenance=provenance, costs=costs, contract=contract_ref,
        mandatory_requirement_ids=tuple(r.requirement_id for r in requirements if r.mandatory),
        scenarios=tuple(c.Scenario(scenario_id=f'scenario_{index}', requirement_ids=(r.requirement_id,),
            preconditions=('Fresh installation of candidate source in the isolated runtime.',),
            actions=(r.statement,), observations=(r.observable,), expected_relation=r.statement,
            input_domain='Exercise representative inputs and important edge cases from the requirement.',
            oracle_origin=r.evidence[0], reset_needs=()) for index, r in enumerate(requirements, 1)),
        seed_policy=seed_policy)
