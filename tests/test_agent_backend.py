"""Exact pinned HTTP payload/response contracts with no real server/model call."""
import hashlib,io,json,sys
from types import SimpleNamespace
import pytest
from feature_rl.agents.backend import SkyRLTokenBackend,GenerationUnavailable,InvalidGeneration
from feature_rl.agents import HARNESS
from feature_rl.artifacts import canonical_json
from feature_rl import contracts as c
from feature_rl.training.state import PolicyBarrier,PolicyStamp


def backend(tmp_path,monkeypatch):
    path=tmp_path/'tokenizer';path.mkdir();(path/'tokenizer.json').write_bytes(b'{}')
    digest=hashlib.sha256(canonical_json({'tokenizer.json':hashlib.sha256(b'{}').hexdigest()})).hexdigest()
    class Tokenizer:
        chat_template='DIAGNOSTIC template'
        def __len__(self):return 256
        def decode(self,tokens,**kwargs):return bytes(tokens).decode()
        def encode(self,text,**kwargs):return list(text.encode())
        def apply_chat_template(self,messages,*,tokenize,add_generation_prompt):
            text=json.dumps(messages)+'ASSISTANT:'
            return list(text.encode()) if tokenize else text
    monkeypatch.setitem(sys.modules,'transformers',SimpleNamespace(AutoTokenizer=SimpleNamespace(from_pretrained=lambda *a,**kw:Tokenizer())))
    barrier=PolicyBarrier(('worker',))
    client=SkyRLTokenBackend(tokenizer_directory=path,tokenizer_sha256=digest,endpoint='http://127.0.0.1:9999',
        model_name='diagnostic',barrier=barrier,max_seq_len=8192)
    ref=lambda kind:c.ArtifactRef(sha256='f'*64,kind=kind,schema_version=1,visibility=c.Visibility.PUBLIC,encoding='bytes')
    policy=c.PolicyConfig(identity=c.ModelIdentity(provider='skyrl',model='diagnostic',revision='pin',weights=ref('weights'),tokenizer_digest=digest),
        policy_version='v1',temperature=1.,top_p=1.,seed=13,system_prompt=ref('system-prompt'),harness_version=HARNESS,require_token_probabilities=True)
    stamp=PolicyStamp('v1','f'*64,digest,client.template_digest)
    barrier.begin(stamp,(1,));barrier.acknowledge('worker',stamp,(1,))
    return client,policy


def test_http_sampling_uses_exact_ids_seed_and_behavior_probs(monkeypatch,tmp_path):
    client,policy=backend(tmp_path,monkeypatch);calls=[]
    def post(request,timeout):
        calls.append((request,timeout))
        return io.BytesIO(json.dumps({'choices':[{'token_ids':[123,125],'finish_reason':'stop',
            'logprobs':{'content':[{'logprob':-.4},{'logprob':-.2}]}}]}).encode())
    monkeypatch.setattr('feature_rl.agents.backend.urlopen',post)
    _,context=client.render([{'role':'user','content':'visible only'}])
    result=client.generate(context,policy=policy,max_tokens=4,timeout=2.,session_id='fresh-episode')
    request,timeout=calls[0];payload=json.loads(request.data)
    assert payload['token_ids']==list(context)
    assert payload['sampling_params']==dict(n=1,temperature=1.,top_p=1.,top_k=-1,seed=13,max_tokens=4,logprobs=0)
    assert payload['cache_salt']=='v1' and request.full_url.endswith('/inference/v1/generate')
    assert result.context==context and result.tokens==(123,125) and result.logprobs==(-.4,-.2)
    assert result.text=='{}' and len(calls)==1


def test_http_loss_is_not_retried_and_stale_barrier_prevents_call(monkeypatch,tmp_path):
    client,policy=backend(tmp_path,monkeypatch);calls=[]
    def fail(*a,**kw):calls.append(1);raise OSError('lost reply')
    monkeypatch.setattr('feature_rl.agents.backend.urlopen',fail)
    with pytest.raises(GenerationUnavailable):client.generate((1,),policy=policy,max_tokens=4,timeout=1.,session_id='one')
    assert len(calls)==1
    client.barrier.load_state_dict(client.barrier.state_dict())  # Reset acknowledgements on resume.
    with pytest.raises(ValueError):client.generate((1,),policy=policy,max_tokens=4,timeout=1.,session_id='one')
    assert len(calls)==1


def test_malformed_http_probabilities_are_invalid_without_resampling(monkeypatch,tmp_path):
    client,policy=backend(tmp_path,monkeypatch)
    monkeypatch.setattr('feature_rl.agents.backend.urlopen',lambda *a,**k:io.BytesIO(
        b'{"choices":[{"token_ids":[123,125],"finish_reason":"stop","logprobs":{"content":[{"logprob":0.2}]}}]}'))
    with pytest.raises(InvalidGeneration):client.generate((1,),policy=policy,max_tokens=4,timeout=1.,session_id='one')


@pytest.mark.parametrize('settings',[{'temperature':0.},{'temperature':.5},{'top_p':.9}])
def test_raw_logprobs_do_not_claim_transformed_behavior(monkeypatch,tmp_path,settings):
    client,policy=backend(tmp_path,monkeypatch);calls=[]
    def post(*a,**k):
        calls.append(1)
        return io.BytesIO(b'{"choices":[{"token_ids":[123,125],"finish_reason":"stop","logprobs":{"content":[{"logprob":-0.2},{"logprob":-0.1}]}}]}')
    monkeypatch.setattr('feature_rl.agents.backend.urlopen',post)
    changed=policy.model_copy(update=settings)
    with pytest.raises(InvalidGeneration):client.generate((1,),policy=changed,max_tokens=4,timeout=1.,session_id='one')
    assert not calls
    demonstration=changed.model_copy(update={'require_token_probabilities':False})
    result=client.generate((1,),policy=demonstration,max_tokens=4,timeout=1.,session_id='one')
    assert result.logprobs is None and len(calls)==1


@pytest.mark.parametrize('finish,exception',[('abort',GenerationUnavailable),('aborted',GenerationUnavailable),('mystery',InvalidGeneration)])
def test_interrupted_or_unknown_native_finish_is_not_candidate_malformation(monkeypatch,tmp_path,finish,exception):
    client,policy=backend(tmp_path,monkeypatch)
    payload=json.dumps({'choices':[{'token_ids':[123,125],'finish_reason':finish,
        'logprobs':{'content':[{'logprob':-.2},{'logprob':-.1}]}}]}).encode()
    monkeypatch.setattr('feature_rl.agents.backend.urlopen',lambda *a,**k:io.BytesIO(payload))
    with pytest.raises(exception):client.generate((1,),policy=policy,max_tokens=4,timeout=1.,session_id='one')


def test_tokenizer_identity_includes_class_selection_config(monkeypatch,tmp_path):
    client,policy=backend(tmp_path,monkeypatch)
    root=tmp_path/'tokenizer'
    (root/'config.json').write_text('{"model_type":"different"}')
    with pytest.raises(ValueError,match='manifest digest'):
        SkyRLTokenBackend(tokenizer_directory=root,tokenizer_sha256=client.tokenizer_digest,
            endpoint=client.endpoint,model_name=client.model_name,barrier=client.barrier,max_seq_len=8192)
