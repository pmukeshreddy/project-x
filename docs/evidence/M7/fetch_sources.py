"""Bounded retrieval of inert official pinned source; never imports upstream."""
import concurrent.futures, datetime, hashlib, json, pathlib, urllib.request
ROOT = pathlib.Path('.feature-rl/research/M7')
PINS = {'skyrl': ('NovaSky-AI/SkyRL', 'f5bc3b78dfddfb352870d5d7430cd226e5785838'), 'harbor': ('harbor-framework/harbor', '3de07a0e01f3368921766437fc7afece3ddec23d')}

def fetch(item):
    name, path = item
    repo, commit = PINS[name]
    url = f'https://raw.githubusercontent.com/{repo}/{commit}/{path}'
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError('Source exceeds 2 MB bound')
    destination = ROOT / name / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return dict(url=url, path=f'{name}/{path}', sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), retrieved_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())

if __name__ == '__main__':
    import sys
    items = [tuple(line.split(' ', 1)) for line in pathlib.Path(sys.argv[1]).read_text().splitlines() if line]
    assert len(items) <= 80
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        records = list(pool.map(fetch, items))
    with (ROOT / 'retrieval.jsonl').open('a') as output:
        for record in records:
            output.write(json.dumps(record) + '\n')
    print(f'Retrieved {len(records)} files, {sum(record["bytes"] for record in records)} bytes')
