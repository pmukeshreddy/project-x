"""Grounded requirement-contract proposal and finalization APIs."""

from .finalize import ContractFinalizer, GroundingError, validate_link
from .evidence import AuthoringEvidenceResolver, EvidenceResolutionError
from .discovery import (
    ClickDiscoveryError,
    ClickDiscoveryObservation,
    ClickDiscoveryResult,
    ClickDiscoveryService,
)
from .models import (
    ContractFinalizationInputs,
    GroundedSource,
    RequirementContractProposal,
)
from .service import (
    AuthoringExhausted,
    ContractAuthoringResult,
    ContractAuthoringService,
    GenerationCandidate,
    build_contract_request,
)
from .retrieval import (
    BaselineRetriever,
    RetrievalPolicy,
    RetrievalReceipt,
    RetrievalRejected,
    RetrievalRequest,
    RetrievalResult,
)

__all__ = [
    "AuthoringExhausted",
    "AuthoringEvidenceResolver",
    "BaselineRetriever",
    "ClickDiscoveryError",
    "ClickDiscoveryObservation",
    "ClickDiscoveryResult",
    "ClickDiscoveryService",
    "ContractAuthoringResult",
    "ContractAuthoringService",
    "ContractFinalizationInputs",
    "ContractFinalizer",
    "GenerationCandidate",
    "GroundedSource",
    "GroundingError",
    "EvidenceResolutionError",
    "RequirementContractProposal",
    "RetrievalPolicy",
    "RetrievalReceipt",
    "RetrievalRejected",
    "RetrievalRequest",
    "RetrievalResult",
    "build_contract_request",
    "validate_link",
]
