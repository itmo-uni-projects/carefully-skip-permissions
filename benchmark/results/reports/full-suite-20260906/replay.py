"""Verify the archived campaign and recompute its metrics without model calls."""
import argparse
import gzip
import hashlib
import json
import shutil
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    hashes = json.loads((source / 'sha256.json').read_text())
    for name, expected in hashes.items():
        path = (source / name).resolve()
        if not path.is_relative_to(source):
            raise ValueError('Archive path escapes its directory')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Archive hash mismatch: ' + name)
    destination = args.output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((source / 'manifest.json').read_text())
    shutil.copyfile(source / 'manifest.json', destination / 'source-manifest.json')
    for unit in manifest['units']:
        filename = Path(unit['output']).name
        archived = source / unit['suite'] / (filename + '.gz')
        output = destination / unit['suite'] / filename
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(gzip.decompress(archived.read_bytes()))
        unit['output'] = str(output)
    for suite in manifest['suites']:
        review = source / suite['id'] / 'action-review.json'
        if review.exists():
            shutil.copyfile(review, destination / suite['id'] / review.name)
    manifest['replay'] = {
        'source_manifest_sha256': hashes['manifest.json'],
        'note': 'Only result output paths were relocated; original commands are provenance and are not executed.',
    }
    (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    benchmark = next(p for p in source.parents if (p / 'scripts/run_suite.py').exists())
    sys.path.insert(0, str(benchmark / 'scripts'))
    from run_suite import report_campaign
    report = report_campaign(destination)
    if not report['collection_complete']:
        raise ValueError('Replayed campaign is incomplete')
    for panel in report['panels']:
        saved = json.loads((source / panel['suite'] / 'scores.json').read_text())
        if panel['metrics'] != saved:
            raise ValueError('Replayed metrics differ: ' + panel['suite'])
    print(json.dumps({'status': 'verified', 'records': sum(p['observed_records'] for p in report['panels']), 'panels': len(report['panels']), 'model_calls': 0}))


if __name__ == '__main__':
    main()
