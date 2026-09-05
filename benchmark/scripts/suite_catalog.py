"""One registry for live datasets and immutable historical evidence."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

from review_manifest import dataset, digest

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'suite.json'


def records(path: Path) -> list[dict]:
    raw = gzip.decompress(path.read_bytes()) if path.suffix == '.gz' else path.read_bytes()
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def load_catalog() -> dict:
    value = json.loads(CATALOG.read_text())
    ids = [entry['id'] for entry in value['suites']]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate suite id')
    return value


def select(names: list[str] | None = None) -> list[dict]:
    catalog = load_catalog()
    requested = names or ['all']
    wanted = set()
    for name in requested:
        wanted.update(catalog['groups'].get(name, [name]))
    known = {entry['id'] for entry in catalog['suites']}
    if wanted - known:
        raise ValueError('unknown suites: ' + ', '.join(sorted(wanted - known)))
    return [entry for entry in catalog['suites'] if entry['id'] in wanted]


def fingerprint(entry: dict) -> dict:
    file = ROOT / entry['path']
    if entry['kind'] == 'trajectory':
        return dataset(file, ROOT)
    files = {entry['path']: digest(file.read_bytes())}
    if entry.get('labels'):
        files[entry['labels']] = digest((ROOT / entry['labels']).read_bytes())
    return {'hash': digest(json.dumps(files, sort_keys=True).encode()), 'files': files}


def check_catalog() -> dict:
    catalog = load_catalog()
    owners: dict[str, str] = {}
    split_fixtures: dict[str, set[str]] = {}
    counts = {}
    for entry in catalog['suites']:
        rows = records(ROOT / entry['path'])
        counts[entry['id']] = len(rows)
        for row in rows:
            identifier = row.get('scenario_id', row.get('case_id'))
            if identifier in owners:
                raise ValueError(f'duplicate live case {identifier}: {owners[identifier]}, {entry["id"]}')
            owners[identifier] = entry['id']
            if entry['kind'] == 'trajectory':
                split_fixtures.setdefault(row['fixture'], set()).add(row['split'])
        fingerprint(entry)
    if any(len(splits) > 1 for splits in split_fixtures.values()):
        raise ValueError('fixture leaks across dev and holdout')
    # Every original Shamil scenario is represented once, with its task,
    # payload and assertions preserved. Host-role metadata can be added.
    original = records(ROOT / catalog['shamil_source']['snapshot'])
    current = {row['scenario_id']: row for row in records(ROOT / 'datasets/trajectories/dev/scenarios.jsonl')}
    for row in original:
        if any(current.get(row['scenario_id'], {}).get(key) != value for key, value in row.items()):
            raise ValueError('Shamil source scenario changed: ' + row['scenario_id'])
    snapshot = ROOT / catalog['shamil_source']['snapshot']
    if digest(snapshot.read_bytes()) != catalog['shamil_source']['sha256']:
        raise ValueError('Shamil provenance snapshot changed')
    behavioral = {row['trajectory_id']: row for row in records(ROOT / 'datasets/trajectories/dev/trajectories.jsonl')}
    for entry in catalog['suites']:
        if not entry['id'].startswith('behavioral'):
            continue
        for row in records(ROOT / entry['path']):
            base = behavioral[row['behavior']['source_case_id']]
            followup = base['follow_up']['message'] if base['follow_up'] else None
            if row['task_prompt'] != base['issue']['title'] + '\n\n' + base['issue']['body'] or row['behavior']['follow_up'] != followup:
                raise ValueError('behavioral task or follow-up changed: ' + row['scenario_id'])
            if any(row['behavior'].get(key) != value for key, value in base['oracle'].items()):
                raise ValueError('behavioral assertion changed: ' + row['scenario_id'])
    history = []
    for entry in catalog['history']:
        file = ROOT / entry['path']
        if digest(file.read_bytes()) != entry['sha256']:
            raise ValueError('historical evidence changed: ' + entry['id'])
        history.append({**entry,
                        'records': len(records(file)), 'sha256': digest(file.read_bytes())})
    return {'live_cases': counts, 'shamil_cases_preserved': len(original), 'history': history}
