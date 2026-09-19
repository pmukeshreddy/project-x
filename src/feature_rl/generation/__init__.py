"""Configured, bounded local generation provider."""

from .models import (
    AuthoringContext,
    GENERATION_IDENTIFIER_MAX_LENGTH,
    GenerationCallRecord,
    GenerationAttemptMetadata,
    GenerationLimits,
    GenerationRequest,
    GenerationResult,
    GenerationStage,
    GenerationUsage,
)
from .provider import (
    GenerationProviderError,
    GenerationPublicationRecovery,
    LocalGenerationProvider,
)
from .runner import BoundedProcessRunner, ProcessBoundaryError, ProcessOutcome
from .backend import (
    BackendConfigurationError,
    BackendConfig,
    PlatformFacts,
    VerifiedBackend,
    VerifiedDependencies,
    VerifiedModel,
    verify_dependency_manifest,
    verify_model_files,
    verify_platform,
)

__all__ = [
    "AuthoringContext",
    "BackendConfigurationError",
    "BackendConfig",
    "BoundedProcessRunner",
    "GenerationLimits",
    "GENERATION_IDENTIFIER_MAX_LENGTH",
    "GenerationCallRecord",
    "GenerationAttemptMetadata",
    "GenerationProviderError",
    "GenerationPublicationRecovery",
    "GenerationRequest",
    "GenerationResult",
    "GenerationStage",
    "GenerationUsage",
    "LocalGenerationProvider",
    "PlatformFacts",
    "ProcessBoundaryError",
    "ProcessOutcome",
    "VerifiedBackend",
    "VerifiedDependencies",
    "VerifiedModel",
    "verify_dependency_manifest",
    "verify_model_files",
    "verify_platform",
]
