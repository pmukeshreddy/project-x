"""Feature learning and native service. Torch/SkyRL/CUDA imports remain lazy.

CPU diagnostics are not feature-learning or native-stack execution evidence.
"""
from .core import BalancedSampler, GroupPlan, TaskSlot, group_advantages, validate_trace
from .data import PreparedGroup, TrainingDataGate
from .state import PolicyBarrier, PolicyStamp
from .torch_backend import CausalTurn, TorchUpdater, UpdateReceipt

__all__ = ['BalancedSampler', 'GroupPlan', 'TaskSlot', 'group_advantages', 'validate_trace',
           'PreparedGroup', 'TrainingDataGate', 'PolicyBarrier', 'PolicyStamp',
           'CausalTurn', 'TorchUpdater', 'UpdateReceipt']

def __getattr__(name):
    import importlib
    modules={'TrainingService':'service',
        'NativeSessionFactory':'factory','NativeSettings':'native','validate_selected_checkpoint':'checkpoints'}
    if name not in modules:raise AttributeError(name)
    return getattr(importlib.import_module('.'+modules[name],__name__),name)

__all__ += ['TrainingService','NativeSessionFactory',
    'NativeSettings','validate_selected_checkpoint']
