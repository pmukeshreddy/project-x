"""Feature learning primitives. Torch/SkyRL/CUDA imports remain lazy.

Authoritative task admission and the complete runner/service are integrated in
subsequent M7 slices; low-level optimizer receipts are not experiment results.
"""
from .core import BalancedSampler, GroupPlan, SignalGate, TaskSlot, group_advantages, validate_trace
from .data import PreparedGroup, TrainingDataGate, tokenize_supervision
from .state import PolicyBarrier, PolicyStamp
from .torch_backend import CausalTurn, TorchUpdater, UpdateReceipt

__all__ = ['BalancedSampler', 'GroupPlan', 'SignalGate', 'TaskSlot', 'group_advantages', 'validate_trace',
           'PreparedGroup', 'TrainingDataGate', 'tokenize_supervision', 'PolicyBarrier', 'PolicyStamp',
           'CausalTurn', 'TorchUpdater', 'UpdateReceipt']
