"""Host-owned evaluation manifests and the independent holdout review gate."""
from __future__ import annotations
import hashlib
import json
import subprocess
from pathlib import Path


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def source(root: Path) -> dict:
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    delta = subprocess.check_output(['git', 'diff', '--binary', 'HEAD'], cwd=root)
    names = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=root).decode().split('\0')
    untracked = {name: digest((root / name).read_bytes()) for name in sorted(names) if name and (root / name).is_file()}
    return {'commit': commit, 'diff_hash': digest(delta), 'untracked': untracked,
            'hash': digest(json.dumps([commit, digest(delta), untracked], sort_keys=True).encode())}


def dataset(file: Path, benchmark: Path) -> dict:
    rows = [json.loads(line) for line in file.read_text().splitlines() if line.strip()]
    files = {str(file.relative_to(benchmark)): digest(file.read_bytes())}
    for row in rows:
        for path in sorted((benchmark / 'fixtures' / row['fixture']).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                files[str(path.relative_to(benchmark))] = digest(path.read_bytes())
        payload = row['injection'].get('payload_id')
        if payload:
            path = benchmark / 'fixtures/payloads' / f'{payload}.md'
            files[str(path.relative_to(benchmark))] = digest(path.read_bytes())
    return {'files': files, 'hash': digest(json.dumps(files, sort_keys=True).encode()), 'scenario_ids': [r['scenario_id'] for r in rows]}


def require_review(review: Path | None, manifest: dict, policy: str) -> dict:
    if review is None: raise ValueError('Reviewed holdout requires --review pointing to an independent human review manifest')
    value = json.loads(review.read_text())
    if value.get('approved') is not True or not str(value.get('reviewed_by', '')).strip():
        raise ValueError('Holdout is unreviewed: a named human reviewer must approve the scenarios and labels')
    if value.get('dataset_hash') != manifest['hash'] or value.get('policy_hash') != policy:
        raise ValueError('Holdout review is stale: dataset or policy changed')
    decisions = value.get('scenarios', {})
    if set(decisions) != set(manifest['scenario_ids']) or not all(x.get('approved') is True for x in decisions.values()):
        raise ValueError('Every selected holdout scenario and its labels require human review')
    return value
