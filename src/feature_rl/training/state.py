"""Persistent policy identity and nonpersistent inference readiness acknowledgments."""
from dataclasses import asdict, dataclass
import re


@dataclass(frozen=True)
class PolicyStamp:
    version: str
    weights: str
    tokenizer: str
    template: str

    def __post_init__(self):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', self.version):
            raise ValueError('Invalid policy version')
        if any(not re.fullmatch('[0-9a-f]{64}', v) for v in (self.weights, self.tokenizer, self.template)):
            raise ValueError('Exact policy/tokenizer/template digests required')


class PolicyBarrier:
    """Admission barrier around a trusted inference control channel.

    Acknowledgments must be collected by the controller from every configured
    worker after actual weight sync and a fixed-input inference check. This object
    validates them; it does not authenticate endpoints or claim a counter is a hash.
    """
    def __init__(self, workers: tuple[str, ...]):
        if not workers or len(set(workers)) != len(workers) or any(not w for w in workers):
            raise ValueError('Unique nonempty inference worker roster required')
        self.workers = workers
        self.stamp: PolicyStamp | None = None
        self.probe: tuple[int, ...] | None = None
        self._acknowledged: set[str] = set()

    def begin(self, stamp: PolicyStamp, probe: tuple[int, ...]):
        if not probe or any(type(t) is not int or t < 0 for t in probe):
            raise ValueError('Exact fixed-input inference result required')
        PolicyStamp(**asdict(stamp))
        self.stamp, self.probe = stamp, tuple(probe)
        self._acknowledged.clear()

    def acknowledge(self, worker: str, stamp: PolicyStamp, probe: tuple[int, ...]):
        if not isinstance(stamp, PolicyStamp) or self.stamp is None or worker not in self.workers or stamp != self.stamp or probe != self.probe:
            raise ValueError('Stale/unknown worker or inference mismatch')
        self._acknowledged.add(worker)

    def require(self, stamp: PolicyStamp):
        if not isinstance(stamp, PolicyStamp) or self.stamp is None or stamp != self.stamp or self._acknowledged != set(self.workers):
            raise ValueError('Policy synchronization and fixed-input inference are incomplete')

    def state_dict(self):
        return dict(workers=list(self.workers), stamp=asdict(self.stamp) if self.stamp else None,
                    probe=list(self.probe) if self.probe else None)

    def load_state_dict(self, state):
        if state['workers'] != list(self.workers) or state['stamp'] is None or state['probe'] is None:
            raise ValueError('Checkpoint inference roster/identity missing or changed')
        self.begin(PolicyStamp(**state['stamp']), tuple(state['probe']))
