"""Trusted local tokenizer and exact pinned SkyRL token endpoint; no auto retries."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from feature_rl.artifacts import canonical_json
from feature_rl.contracts import PolicyConfig
from feature_rl.training.state import PolicyBarrier, PolicyStamp
from feature_rl.verifiers.language import decode_json
from .protocol import HARNESS

class InvalidGeneration(ValueError): pass
class GenerationUnavailable(RuntimeError): pass

@dataclass(frozen=True)
class Completion:
    text: str
    context: tuple[int,...]
    tokens: tuple[int,...]
    logprobs: tuple[float,...] | None
    policy_version: str
    finish_reason: str

    def validate(self, *, context, policy, max_tokens, vocab_size):
        if self.finish_reason in ('abort','aborted','error'):
            raise GenerationUnavailable('native generation aborted; interrupted response is not a candidate action')
        if self.finish_reason not in ('stop','length'):
            raise InvalidGeneration('unknown native generation finish reason')
        if self.context != context or self.policy_version != policy.policy_version:
            raise InvalidGeneration('stale policy or changed generation context')
        if not self.tokens or len(self.tokens)>max_tokens or any(type(t) is not int or not 0<=t<vocab_size for t in self.tokens):
            raise InvalidGeneration('invalid or over-budget sampled token IDs')
        if self.logprobs is not None and (len(self.logprobs)!=len(self.tokens) or
            any(type(p) not in (float,int) or not math.isfinite(p) or p>0 for p in self.logprobs)):
            raise InvalidGeneration('invalid sampled behavior probabilities')
        if policy.require_token_probabilities and self.logprobs is None:
            raise InvalidGeneration('required sampled behavior probabilities missing')

class PolicyBackend(ABC):
    """Only trusted controller implementations belong here; never candidate plugins."""
    max_seq_len: int
    vocab_size: int
    tokenizer_digest: str
    template_digest: str
    @abstractmethod
    def verify_policy(self, policy: PolicyConfig): ...
    @abstractmethod
    def render(self, messages) -> tuple[str,tuple[int,...]]: ...
    @abstractmethod
    def generate(self, context, *, policy, max_tokens, timeout, session_id) -> Completion: ...

class SkyRLTokenBackend(PolicyBackend):
    """Use pinned /inference/v1/generate with one request and bounded response.

    The stock RemoteInferenceClient._post retries up to 30 times. This adapter
    deliberately performs one HTTP request; lost generation is an unknown outcome,
    never a replacement policy sample. Control endpoints remain controller-only.
    Native server/weight-sync qualification belongs to the training launcher.
    """
    def __init__(self, *, tokenizer_directory: Path, tokenizer_sha256: str,
                 endpoint: str, model_name: str, barrier: PolicyBarrier,
                 max_seq_len: int, response_bytes: int = 4*1024*1024):
        from transformers import AutoTokenizer
        root=Path(tokenizer_directory)
        if root.is_symlink() or not root.is_dir(): raise ValueError('trusted local tokenizer directory required')
        files={}
        total=0
        # A dedicated local tokenizer bundle, including config.json and any
        # tokenizer-specific vocabulary/template files; never a partial whitelist.
        for path in sorted(root.rglob('*')):
            if path.is_symlink(): raise ValueError('tokenizer symlinks forbidden')
            if not path.is_dir():
                if not path.is_file() or path.stat().st_size>64*1024*1024: raise ValueError('tokenizer file bound exceeded')
                total+=path.stat().st_size
                if total>128*1024*1024 or len(files)>=1024: raise ValueError('dedicated tokenizer bundle bound exceeded')
                files[path.relative_to(root).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
        actual=hashlib.sha256(canonical_json(files)).hexdigest()
        if not files or actual!=tokenizer_sha256: raise ValueError('exact local tokenizer manifest digest differs')
        parsed=urlsplit(endpoint)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('explicit trusted inference endpoint required')
        if type(max_seq_len) is not int or not 1<max_seq_len<=131072 or not 1024<=response_bytes<=8*1024*1024:
            raise ValueError('bounded sequence/HTTP response required')
        self.tokenizer=AutoTokenizer.from_pretrained(str(root),local_files_only=True,trust_remote_code=False)
        if not isinstance(self.tokenizer.chat_template,str) or not self.tokenizer.chat_template:
            raise ValueError('exact explicit chat template required')
        self.tokenizer_digest=actual
        self.template_digest=hashlib.sha256(self.tokenizer.chat_template.encode()).hexdigest()
        self.vocab_size=len(self.tokenizer);self.max_seq_len=max_seq_len
        self.endpoint=endpoint.rstrip('/');self.model_name=model_name;self.barrier=barrier;self.response_bytes=response_bytes

    def verify_policy(self, policy):
        if (policy.harness_version!=HARNESS or policy.identity.provider!='skyrl'
                or policy.identity.model!=self.model_name or policy.identity.weights is None
                or policy.identity.tokenizer_digest!=self.tokenizer_digest):
            raise InvalidGeneration('policy/model/tokenizer/harness identity differs')
        if policy.require_token_probabilities and (policy.temperature!=1. or policy.top_p!=1.):
            raise InvalidGeneration('pinned raw logprobs require unit-temperature full-support training sampling')
        self.barrier.require(PolicyStamp(policy.policy_version,policy.identity.weights.sha256,
            self.tokenizer_digest,self.template_digest))

    def render(self,messages):
        text=self.tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True)
        ids=tuple(self.tokenizer.apply_chat_template(messages,tokenize=True,add_generation_prompt=True))
        if tuple(self.tokenizer.encode(text,add_special_tokens=False))!=ids:
            raise InvalidGeneration('rendered chat context and token IDs differ')
        return text,ids

    def generate(self,context,*,policy,max_tokens,timeout,session_id):
        self.verify_policy(policy)
        if timeout<=0 or not context or max_tokens<=0 or len(context)+max_tokens>self.max_seq_len:
            raise InvalidGeneration('generation exceeds explicit context/budget')
        payload={'model':self.model_name,'token_ids':list(context),'cache_salt':policy.policy_version,
            'sampling_params':{'n':1,'temperature':policy.temperature,'top_p':policy.top_p,'top_k':-1,
                'seed':policy.seed,'max_tokens':max_tokens,'logprobs':0}}
        request=Request(self.endpoint+'/inference/v1/generate',data=canonical_json(payload),
            headers={'Content-Type':'application/json','X-Session-ID':session_id},method='POST')
        try:
            with urlopen(request,timeout=timeout) as response:
                data=response.read(self.response_bytes+1)
            if len(data)>self.response_bytes: raise InvalidGeneration('inference response exceeds bound')
            value=decode_json(data,self.response_bytes)
            if len(value['choices'])!=1: raise InvalidGeneration('exactly one sampled response required')
            choice=value['choices'][0];tokens=tuple(choice['token_ids'])
            content=(choice.get('logprobs') or {}).get('content')
            behavior_known=policy.temperature==1. and policy.top_p==1.
            result=Completion(self.tokenizer.decode(tokens,skip_special_tokens=True),tuple(context),tokens,
                tuple(x['logprob'] for x in content) if content is not None and behavior_known else None,
                policy.policy_version,choice['finish_reason'])
            result.validate(context=tuple(context),policy=policy,max_tokens=max_tokens,vocab_size=self.vocab_size)
            self.verify_policy(policy)
            return result
        except InvalidGeneration: raise
        except (KeyError,TypeError,ValueError) as exc: raise InvalidGeneration('malformed native token response') from exc
        except (OSError,TimeoutError) as exc: raise GenerationUnavailable('native generation outcome unavailable; no automatic retry') from exc
