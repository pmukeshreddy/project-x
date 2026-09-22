"""Only requirement extraction retains a model-generation stage."""
from feature_rl.generation import GenerationStage
from feature_rl.pipeline.models import BuildInputs
from feature_rl.pipeline.authoring_models import AuthoringCall
from feature_rl.requirements import ContractFinalizationInputs


def test_workflow_has_no_generated_verifier_or_control_path():
    assert set(GenerationStage)=={GenerationStage.DISCOVERY,GenerationStage.INITIAL_AUTHORING}
    assert AuthoringCall.model_fields['inputs'].annotation is ContractFinalizationInputs
    assert 'scenario_plan' not in BuildInputs.model_fields
