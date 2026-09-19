# M7 source-inspection command record

All commands were run from `/Users/mukeshreddypochamreddy/Desktop/project x`, except the deferred smoke which was **not run**. No upstream module was imported or executed. HTTPS reads used Python standard-library `urllib.request` with 30-second timeouts. Official source retrieval was bounded at six concurrent requests. Total unique retrieved content: 59 files, 4,994,373 bytes (two tree JSONs, 56 ordinary source files, one lockfile). SHA-256 validation is recorded below.

## Local reads

```sh
cat docs/briefs/M7.md docs/evidence/preflight.md
sed -n '338,519p' feature_rl_pipeline.md
sed -n '579,650p' feature_rl_pipeline.md
rg --files -g AGENTS.md -g '*skyrl*' -g '*harbor*' .feature-rl docs
```

Inspection used `rg -n` for symbol names and `sed -n` / `nl -ba` for cited source sections. The complete selected-file set and stable line excerpts are preserved alongside this file.

## Exact official metadata retrieval

```python
import urllib.request, pathlib, hashlib, json, datetime
root = pathlib.Path('.feature-rl/research/M7')
root.mkdir(parents=True, exist_ok=True)
for name, repo, commit in [
    ('skyrl', 'NovaSky-AI/SkyRL', 'f5bc3b78dfddfb352870d5d7430cd226e5785838'),
    ('harbor', 'harbor-framework/harbor', '3de07a0e01f3368921766437fc7afece3ddec23d'),
]:
    url = f'https://api.github.com/repos/{repo}/git/trees/{commit}?recursive=1'
    data = urllib.request.urlopen(url, timeout=30).read(5_000_001)
    assert len(data) <= 5_000_000
    (root / f'{name}-tree.json').write_bytes(data)
    with (root / 'retrieval.jsonl').open('a') as output:
        output.write(json.dumps(dict(url=url, path=f'{name}-tree.json', sha256=hashlib.sha256(data).hexdigest(), retrieved_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())) + '\n')
```

## Raw source retrieval

Initial source selection was made from the pinned trees and fetched using the same URL construction, byte bound and manifest fields as the checked-in utility. Later dependent selections were fetched with:

```sh
python3 docs/evidence/M7/fetch_sources.py docs/evidence/M7/additional-sources.txt
python3 docs/evidence/M7/fetch_sources.py docs/evidence/M7/final-sources.txt
python3 docs/evidence/M7/fetch_sources.py docs/evidence/M7/sync-sources.txt
python3 docs/evidence/M7/fetch_sources.py docs/evidence/M7/contracts-sources.txt
```

These temporary request lists were consolidated to `sources.txt` for reproducibility. Initial `additional-sources.txt` included the nonexistent `src/harbor/llms/litellm.py` and returned HTTP 404. Corrected to the tree-observed `lite_llm.py`; all successful selected sources were re-fetched with manifest entries. Reproduce the final ordinary-source set with:

```sh
python3 docs/evidence/M7/fetch_sources.py docs/evidence/M7/sources.txt
```

The larger lockfile was fetched separately:

```python
import pathlib, urllib.request, hashlib, json, datetime
root = pathlib.Path('.feature-rl/research/M7')
url = 'https://raw.githubusercontent.com/NovaSky-AI/SkyRL/f5bc3b78dfddfb352870d5d7430cd226e5785838/uv.lock'
with urllib.request.urlopen(url, timeout=30) as response:
    data = response.read(4_000_001)
assert len(data) < 4_000_000
(root / 'skyrl/uv.lock').write_bytes(data)
with (root / 'retrieval.jsonl').open('a') as output:
    output.write(json.dumps(dict(url=url, path='skyrl/uv.lock', sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), retrieved_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())) + '\n')
```

## Executed evidence checks

```sh
bash -n docs/evidence/M7/smoke-import.sh
```

Result: exit 0. This parses the shell script only; it did not run uv, imports or CUDA code.

```python
import ast, pathlib, json, hashlib
root = pathlib.Path('.feature-rl/research/M7')
evidence = pathlib.Path('docs/evidence/M7')
records = [json.loads(line) for line in (evidence / 'source-inventory.jsonl').read_text().splitlines()]
for record in records:
    assert hashlib.sha256((root / record['path']).read_bytes()).hexdigest() == record['sha256']
assert len(records) == 59
ast.parse((evidence / 'fetch_sources.py').read_text())
embedded = (evidence / 'smoke-import.sh').read_text().split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
ast.parse(embedded)
```

Result: all 59 recorded hashes matched; both local Python blocks parsed. `git check-ignore .feature-rl/research/M7/skyrl/pyproject.toml` returned that path, confirming raw source is ignored. These checks establish evidence integrity and syntax, not library/runtime compatibility.

## Deferred commands

`smoke-import.sh` is the smallest proposed future Linux/NVIDIA gate. It requires preinstalled exact dependencies and cannot establish model/agent/trainer operation. No package installation, model download, GPU invocation or upstream test was executed. There is no runnable project feature-update command until the actual adapter exists and is reviewed.
