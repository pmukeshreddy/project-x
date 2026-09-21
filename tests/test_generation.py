"""Stage isolation and local Codex process resource boundaries; no model calls."""
import json
import os
import sys
from unittest.mock import patch
import pytest
from pydantic import ValidationError
from feature_rl.contracts import ArtifactRef, Visibility
from feature_rl.generation import AuthoringContext, GenerationLimits, GenerationRequest, GenerationStage, BoundedProcessRunner
import feature_rl.generation.runner as runner_module

def artifact(
    kind: str,
    visibility: Visibility = Visibility.AUTHORING,
    *,
    encoding: str = "bytes",
) -> ArtifactRef:
    return ArtifactRef(
        sha256="a" * 64,
        kind=kind,
        schema_version=1,
        visibility=visibility,
        encoding=encoding,
    )


def context(
    *,
    context_id: str = "SRC_1",
    role: str = "request",
    kind: str = "request-snapshot",
    visibility: Visibility = Visibility.AUTHORING,
) -> AuthoringContext:
    return AuthoringContext(
        context_id=context_id,
        role=role,
        source=artifact(kind, visibility),
        locator="issue.body",
        text="Add an observable behavior.",
        provenance_label="historical_request",
    )


def limits(**updates) -> GenerationLimits:
    values = dict(

        wall_seconds=120.0,
        cpu_seconds=120,
        stdin_bytes=1_048_576,
        output_bytes=1_048_576,
        input_tokens=2048,
        output_tokens=128,
        physical_footprint_kill_bytes=4_294_967_296,
        physical_footprint_poll_seconds=0.02,
        declared_memory_ceiling_bytes=5_368_709_120,
    )
    return GenerationLimits(**(values | updates))


def request(
    *, stage=GenerationStage.INITIAL_AUTHORING, contexts=None,
    generation_limits: GenerationLimits | None = None,
) -> GenerationRequest:
    return GenerationRequest(
        request_id="REQ_CALL_1",
        response_id="RESP_1",
        prompt_id="PROMPT_1",
        stage=stage,
        system_prompt="Return only the required JSON envelope.",
        instruction="Extract the requested structured data.",
        contexts=tuple(contexts or (context(),)),
        allowed_requirement_ids=("FEATURE_1",),
        limits=generation_limits or limits(),
    )


def test_initial_authoring_rejects_private_or_reference_context():
    """Removing the stage allowlist would expose H/private reference material."""
    for forbidden in (
        context(role="baseline", kind="reference-tree"),
        context(kind="RequirementContract", visibility=Visibility.PRIVATE),
    ):
        with pytest.raises(ValidationError):
            request(contexts=(forbidden,))


def test_checker_stage_accepts_only_explicit_contract_and_scenario_context():
    """Broadening checker input beyond frozen contract/scenario data is a leak."""
    admitted = (
        context(role="contract", kind="RequirementContract", visibility=Visibility.PRIVATE),
        context(
            context_id="SRC_2",
            role="scenario",
            kind="ScenarioPlan",
            visibility=Visibility.EVALUATION,
        ),
        context(
            context_id="SRC_3", role="public_check", kind="public-check",
            visibility=Visibility.PUBLIC,
        ),
    )
    built = request(stage=GenerationStage.CHECKER_GENERATION, contexts=admitted)
    assert tuple(item.role for item in built.contexts) == ("contract", "scenario", "public_check")
    with pytest.raises(ValidationError):
        request(stage=GenerationStage.CHECKER_GENERATION, contexts=(context(role="baseline"),))


def test_control_authoring_requires_frozen_contract_and_baseline_with_explicit_reference():
    contract = context(
        context_id="CONTRACT", role="contract", kind="RequirementContract"
    ).model_copy(update={"source": artifact("RequirementContract", encoding="json")})
    baseline = context(context_id="BASELINE", role="baseline", kind="source-archive")
    scenario = context(
        context_id="SCENARIO", role="scenario", kind="ScenarioPlan",
        visibility=Visibility.EVALUATION,
    ).model_copy(
        update={"source": artifact("ScenarioPlan", Visibility.EVALUATION, encoding="json")}
    )
    reference = context(
        context_id="REFERENCE", role="reference", kind="source-archive",
        visibility=Visibility.PRIVATE,
    )
    built = request(
        stage=GenerationStage.CONTROL_AUTHORING,
        contexts=(contract, baseline, scenario, reference),
    )
    assert tuple(item.role for item in built.contexts) == (
        "contract", "baseline", "scenario", "reference"
    )
    second_reference = reference.model_copy(update={"context_id": "REFERENCE_2"})
    assert request(
        stage=GenerationStage.CONTROL_AUTHORING,
        contexts=(contract, baseline, reference, second_reference),
    ).contexts[-1] == second_reference

    invalid = (
        (baseline,),
        (contract,),
        (contract, contract.model_copy(update={"context_id": "CONTRACT_2"}), baseline),
        (contract, baseline, scenario, scenario.model_copy(update={"context_id": "SCENARIO_2"})),
        (contract, baseline, reference.model_copy(
            update={"source": artifact("source-archive", Visibility.AUTHORING)}
        )),
        (contract, baseline, reference.model_copy(
            update={"source": artifact("SourcePair", Visibility.PRIVATE, encoding="json")}
        )),
        (contract, baseline, scenario.model_copy(
            update={"source": artifact("ScenarioPlan", Visibility.AUTHORING, encoding="json")}
        )),
    )
    for contexts in invalid:
        with pytest.raises(ValidationError):
            request(stage=GenerationStage.CONTROL_AUTHORING, contexts=contexts)


def test_alternative_authoring_accepts_only_visible_contract_and_solver_safe_context():
    contract = context(
        context_id="CONTRACT", role="contract", kind="RequirementContract"
    ).model_copy(update={"source": artifact("RequirementContract", encoding="json")})
    baseline = context(context_id="BASELINE", role="baseline", kind="source-archive")
    admitted = (
        contract,
        baseline,
        context(context_id="REQUEST", role="request", kind="authoring-request"),
        context(
            context_id="PUBLIC_CHECK", role="public_check", kind="public-check",
            visibility=Visibility.PUBLIC,
        ),
        context(
            context_id="SOLVER_SAFE", role="solver_safe", kind="solver-safe-context",
            visibility=Visibility.PUBLIC,
        ),
    )
    built = request(stage=GenerationStage.ALTERNATIVE_AUTHORING, contexts=admitted)
    assert tuple(item.role for item in built.contexts) == (
        "contract", "baseline", "request", "public_check", "solver_safe"
    )

    invalid = (
        (baseline,),
        (contract,),
        (contract, contract.model_copy(update={"context_id": "CONTRACT_2"}), baseline),
        (contract, baseline, context(
            context_id="REFERENCE", role="reference", kind="source-archive",
            visibility=Visibility.PRIVATE,
        )),
        (contract, baseline, context(
            context_id="SCENARIO", role="scenario", kind="ScenarioPlan",
            visibility=Visibility.EVALUATION,
        ).model_copy(
            update={"source": artifact("ScenarioPlan", Visibility.EVALUATION, encoding="json")}
        )),
        (contract, baseline.model_copy(
            update={"source": artifact("source-archive", Visibility.PRIVATE)}
        )),
        (contract, baseline, context(
            context_id="CHECKER", role="solver_safe", kind="checker-output",
            visibility=Visibility.PUBLIC,
        )),
        (contract, baseline, context(
            context_id="PRIVATE_SAFE", role="solver_safe", kind="solver-safe-context",
            visibility=Visibility.PRIVATE,
        )),
    )
    for contexts in invalid:
        with pytest.raises(ValidationError):
            request(stage=GenerationStage.ALTERNATIVE_AUTHORING, contexts=contexts)


def test_reference_role_is_exclusive_to_control_authoring():
    reference = context(
        context_id="REFERENCE", role="reference", kind="source-archive",
        visibility=Visibility.PRIVATE,
    )
    contract = context(
        context_id="CONTRACT", role="contract", kind="RequirementContract"
    ).model_copy(update={"source": artifact("RequirementContract", encoding="json")})
    scenario = context(
        context_id="SCENARIO", role="scenario", kind="ScenarioPlan",
        visibility=Visibility.EVALUATION,
    ).model_copy(
        update={"source": artifact("ScenarioPlan", Visibility.EVALUATION, encoding="json")}
    )
    for stage, contexts in (
        (GenerationStage.INITIAL_AUTHORING, (reference,)),
        (GenerationStage.SCENARIO_PLANNING, (contract, reference)),
        (GenerationStage.CHECKER_GENERATION, (contract, scenario, reference)),
    ):
        with pytest.raises(ValidationError):
            request(stage=stage, contexts=contexts)


def test_scenario_planning_requires_one_authoring_contract_and_admitted_evidence():
    """The first plan is grounded in one frozen contract without admitting H or a plan."""
    contract = context(
        context_id="CONTRACT_1",
        role="contract",
        kind="RequirementContract",
        visibility=Visibility.AUTHORING,
    ).model_copy(update={"source": artifact("RequirementContract").model_copy(update={"encoding": "json"})})
    built = request(
        stage=GenerationStage.SCENARIO_PLANNING,
        contexts=(context(), context(context_id="B_1", role="baseline"), contract),
    )
    assert tuple(item.role for item in built.contexts) == ("request", "baseline", "contract")

    invalid_context_sets = (
        (context(),),
        (contract, contract.model_copy(update={"context_id": "CONTRACT_2"})),
        (
            contract.model_copy(
                update={"source": artifact("ScenarioPlan").model_copy(update={"encoding": "json"})}
            ),
        ),
        (
            contract.model_copy(
                update={
                    "source": artifact(
                        "RequirementContract", Visibility.PRIVATE
                    ).model_copy(update={"encoding": "json"})
                }
            ),
        ),
        (
            contract,
            context(
                context_id="PLAN_1",
                role="scenario",
                kind="ScenarioPlan",
                visibility=Visibility.PRIVATE,
            ),
        ),
        (contract, context(context_id="H_1", role="baseline", kind="reference-tree")),
    )
    for contexts in invalid_context_sets:
        with pytest.raises(ValidationError):
            request(stage=GenerationStage.SCENARIO_PLANNING, contexts=contexts)


def test_request_rejects_duplicate_ids_placeholders_and_relaxed_resource_policy():
    """IDs must be fixed before inference and the qualified bounds cannot drift."""
    with pytest.raises(ValidationError):
        request(contexts=(context(), context()))
    with pytest.raises(ValidationError):
        GenerationRequest.model_validate(request().model_dump() | {"response_id": "{{response_id}}"})
    for update in (
        {"output_tokens": 262145},
        {"input_tokens": 262145},
        {"wall_seconds": 3600.1},
        {"cpu_seconds": 121},
        {"physical_footprint_poll_seconds": 1.01},
        {"physical_footprint_kill_bytes": 5_368_709_120},
    ):
        with pytest.raises(ValidationError):
            limits(**update)
    larger = limits(
         input_tokens=8192, output_tokens=1024
    )
    assert larger.input_tokens == 8192


def test_request_actual_serialization_is_strict_and_stable():
    """Serialization drift would break immutable request identity."""
    encoded = request().model_dump_json()
    decoded = json.loads(encoded)
    assert decoded["request_id"] == "REQ_CALL_1"
    assert decoded["contexts"][0]["source"]["visibility"] == "authoring"
    with pytest.raises(ValidationError):
        GenerationRequest.model_validate(decoded | {"unexpected": True})


def test_runner_enforces_combined_output_cap_and_cleans_process_group(tmp_path):
    """Unbounded event output must be killed and retained bytes stay bounded."""
    outcome = BoundedProcessRunner().run(
        command=(sys.executable, "-c", "import sys; sys.stdout.write('x'*200000); sys.stdout.flush()"),
        stdin=b"",
        cwd=tmp_path,
        response_path=tmp_path / "response.json",
        environment={"PATH": os.environ["PATH"]},
        limits=limits(output_bytes=4096),
    )
    assert outcome.termination == "output_cap"
    assert len(outcome.stdout) + len(outcome.stderr) <= 4096
    assert outcome.process_group_cleanup_verified is True


def test_runner_enforces_deadline_and_records_resource_policy(tmp_path):
    """A stuck worker must not survive its external wall deadline."""
    outcome = BoundedProcessRunner().run(
        command=(sys.executable, "-c", "import time; time.sleep(10)"),
        stdin=b"",
        cwd=tmp_path,
        response_path=tmp_path / "response.json",
        environment={"PATH": os.environ["PATH"]},
        limits=limits(wall_seconds=0.1),
    )
    assert outcome.termination == "deadline"
    assert outcome.wall_seconds < 2
    assert outcome.cpu_limit_enforcement == "kernel_rlimit_cpu"
    assert outcome.output_limit_enforcement == "captured_output_and_final_response"
    assert outcome.memory_limit_enforcement in {"sampled_proc_pid_rusage", "sampled_linux_rss"}
    assert outcome.process_group_cleanup_verified is True


@pytest.mark.parametrize('filename,expected', [('response.json', 'output_cap'), ('state.sqlite', 'process_exit')])
def test_runner_limits_response_without_capping_codex_shared_state(tmp_path, filename, expected):
    outcome = BoundedProcessRunner().run(
        command=(sys.executable, '-c', f"from pathlib import Path; Path({filename!r}).write_bytes(b'x'*2000000)"),
        stdin=b'', cwd=tmp_path, response_path=tmp_path / 'response.json',
        environment={'PATH': os.environ['PATH']}, limits=limits(output_bytes=4096),
    )
    assert outcome.termination == expected
    assert outcome.process_group_cleanup_verified


def test_runner_monitor_exception_returns_partial_outcome_after_cleanup(tmp_path):
    """A watchdog exception must not escape while its owned child remains alive."""
    with patch.object(
        runner_module, "_footprint", side_effect=OSError("injected observation failure")
    ):
        outcome = BoundedProcessRunner().run(
            command=(sys.executable, "-I", "-c", "import time; time.sleep(3)"),
            stdin=b"",
            cwd=tmp_path,
        response_path=tmp_path / "response.json",
            environment={"PATH": "/usr/bin:/bin"},
            limits=limits(wall_seconds=0.25),
        )
    assert outcome.termination == "monitoring_failure"
    assert outcome.monitoring_failures == 1
    assert outcome.monitor_error_type == "OSError"
    assert outcome.process_group_cleanup_verified is True
    assert outcome.exit_status is not None


def test_runner_single_lost_watchdog_observation_fails_closed(tmp_path):
    """A later successful sample cannot erase an earlier watchdog gap."""
    with patch.object(runner_module, "_footprint", return_value=None):
        outcome = BoundedProcessRunner().run(
            command=(sys.executable, "-I", "-c", "import time; time.sleep(3)"),
            stdin=b"",
            cwd=tmp_path,
        response_path=tmp_path / "response.json",
            environment={"PATH": "/usr/bin:/bin"},
            limits=limits(wall_seconds=0.25),
        )
    assert outcome.termination == "monitoring_failure"
    assert outcome.monitoring_failures == 1
    assert outcome.memory_samples == 0
    assert outcome.process_group_cleanup_verified is True


def test_runner_interruption_still_cleans_and_returns_partial_outcome(tmp_path):
    """Controller interruption is classified only after bounded group cleanup."""
    with patch.object(runner_module, "_footprint", side_effect=KeyboardInterrupt):
        outcome = BoundedProcessRunner().run(
            command=(sys.executable, "-I", "-c", "import time; time.sleep(3)"),
            stdin=b"",
            cwd=tmp_path,
        response_path=tmp_path / "response.json",
            environment={"PATH": "/usr/bin:/bin"},
            limits=limits(wall_seconds=0.25),
        )
    assert outcome.termination == "interrupted"
    assert outcome.monitoring_failures == 1
    assert outcome.monitor_error_type == "KeyboardInterrupt"
    assert outcome.process_group_cleanup_verified is True
    assert outcome.exit_status is not None
