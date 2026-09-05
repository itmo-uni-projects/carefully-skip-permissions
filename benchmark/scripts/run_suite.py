#!/usr/bin/env python3
"""Validate, plan, run and report every AutoGuard dataset through one entrypoint."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from review_manifest import source, require_review, digest
from run_guard_demo import inspect_kilo_checkout, load_lock, DEFAULT_LOCK
from run_trajectory import ARMS, private_environment
from suite_catalog import ROOT, CATALOG, select, records, fingerprint, check_catalog

SCRIPTS = ROOT / 'scripts'


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def command_step(command: list[str], cwd: Path, log: Path, environment: dict | None = None) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    print('+ ' + ' '.join(command), flush=True)
    with log.open('w') as handle:
        result = subprocess.run(command, cwd=cwd, env=environment, stdout=handle, stderr=subprocess.STDOUT)
    if result.returncode:
        raise ValueError(f'command exited {result.returncode}; see {log}')


def build_plan(args, entries: list[dict]) -> dict:
    checkout = inspect_kilo_checkout(args.kilo_repo, load_lock(args.lock), allow_dirty=args.allow_dirty_kilo, allow_unpinned=args.allow_unpinned_kilo)
    runtime = source(args.kilo_repo.resolve())
    units = []
    snapshots = []
    for entry in entries:
        rows = records(ROOT / entry['path'])
        if args.scenario_id:
            rows = [row for row in rows if row.get('scenario_id', row.get('case_id')) in args.scenario_id]
        if args.limit:
            rows = rows[:args.limit]
        if not rows:
            continue
        if entry['kind'] == 'action_policy' and args.scenario_id:
            raise ValueError('--scenario-id selects trajectory cases; select policy cases with --limit')
        snapshots.append({**entry, 'dataset': fingerprint(entry), 'cases': rows})
        if entry['kind'] == 'action_policy':
            variants = [('level0_only', 'rules_only', 'full_context'), ('level0_level1_legacy', 'cascade', 'intent_action'), ('level0_level1', 'cascade', 'full_context')]
            for arm, mode, view in variants:
                if arm not in args.arms:
                    continue
                output = args.output_dir / entry['id'] / f'{arm}.jsonl'
                command = [args.bun, 'run', str(args.kilo_repo.resolve() / 'packages/opencode/script/kilocode/autoguard-bench.ts'), '--cases', str(ROOT / entry['path']), '--output', str(output), '--mode', mode, '--view', view, '--model', args.guard_level1_model, '--normalize', '--repeats', str(args.repeats)]
                if args.limit:
                    command += ['--limit', str(args.limit)]
                units.append({'suite': entry['id'], 'kind': entry['kind'], 'arm': arm, 'expected_records': len(rows)*args.repeats, 'output': str(output), 'cwd': str(args.kilo_repo.resolve() / 'packages/opencode'), 'command': command})
        else:
            for arm in args.arms:
                output = args.output_dir / entry['id'] / f'{arm}.jsonl'
                command = [sys.executable, str(SCRIPTS / 'run_trajectory.py'), '--kilo-root', str(args.kilo_repo.resolve()), '--bun', args.bun, '--scenarios', str(ROOT / entry['path']), '--arm', arm, '--interaction', entry['interaction'], '--repeats', str(args.repeats), '--agent-model', args.agent_model, '--guard-level1-model', args.guard_level1_model, '--temperature', str(args.temperature), '--agent-timeout', str(args.agent_timeout), '--diagnostics', str(args.diagnostics), '--stop-on-error', '--output', str(output)]
                if entry.get('review'):
                    command += ['--review', str(ROOT / entry['review'])]
                for row in rows:
                    command += ['--scenario-id', row['scenario_id']]
                units.append({'suite': entry['id'], 'kind': entry['kind'], 'arm': arm, 'interaction': entry['interaction'], 'expected_records': len(rows)*args.repeats, 'output': str(output), 'cwd': str(ROOT.parent), 'command': command})
        for unit in units:
            if unit['suite'] == entry['id']:
                unit['trials'] = [[row.get('scenario_id', row.get('case_id')), repeat] for row in rows for repeat in range(args.repeats)]
    if not units:
        raise ValueError('no matching live trials')
    snapshots = [entry for entry in snapshots if any(unit['suite'] == entry['id'] for unit in units)]
    return {'schema_version': 1, 'created_at_utc': datetime.now(timezone.utc).isoformat(), 'catalog_hash': digest(CATALOG.read_bytes()), 'runtime': runtime, 'checkout': checkout, 'benchmark': source(ROOT.parent), 'agent_model': args.agent_model, 'guard_level1_model': args.guard_level1_model, 'temperature': args.temperature, 'seed': None, 'repeats': args.repeats, 'suites': snapshots, 'units': units, 'total_trials': sum(unit['expected_records'] for unit in units)}


def run_checks(args, entries: list[dict]) -> dict:
    evidence = check_catalog()
    logs = args.output_dir / 'checks'
    environment = {**os.environ, 'GOMAXPROCS': '2'}
    commands = [([sys.executable, '-m', 'pytest', str(ROOT / 'tests'), '-q'], ROOT.parent, 'python')]
    commands.append(([sys.executable, str(SCRIPTS / 'validate_action_policy.py')], ROOT.parent, 'policy-data'))
    for entry in entries:
        if entry['kind'] != 'trajectory':
            continue
        commands.append(([sys.executable, str(SCRIPTS / 'validate_trajectory.py'), '--scenarios', str(ROOT / entry['path'])], ROOT.parent, entry['id']+'-data'))
        commands.append(([sys.executable, str(SCRIPTS / 'check_trajectory_fixtures.py'), '--scenarios', str(ROOT / entry['path']), '--runtime', str(args.kilo_repo.resolve()), '--bun', args.bun], ROOT.parent, entry['id']+'-oracles'))
    runtime = args.kilo_repo.resolve()
    commands += [
        ([args.bun, 'test', 'test/kilocode/autoguard', '--timeout', '30000'], runtime/'packages/opencode', 'autoguard'),
        ([args.bun, 'test', 'test/tool/task.test.ts', 'test/tool/skill.test.ts', 'test/kilocode/sandbox/session-tools.test.ts', 'test/kilocode/sandbox/policy.test.ts', '--timeout', '30000'], runtime/'packages/opencode', 'native-integration'),
        ([args.bun, 'test', 'test/filesystem.test.ts', 'test/context.test.ts', '--timeout', '30000'], runtime/'packages/kilo-sandbox', 'sandbox'),
        ([args.bun, 'test', 'test/kilocode/process-observation.test.ts', '--timeout', '30000'], runtime/'packages/core', 'process-observation'),
        ([args.bun, 'run', 'typecheck'], runtime/'packages/opencode', 'typecheck'),
        ([args.bun, 'run', 'typecheck'], runtime/'packages/kilo-sandbox', 'sandbox-typecheck'),
        ([args.bun, 'run', 'typecheck'], runtime/'packages/core', 'core-typecheck'),
        ([args.bun, 'run', 'script/check-opencode-annotations.ts', '--base', '5fb96088'], runtime, 'annotations'),
        ([args.bun, 'run', 'script/check-opencode-promise-facades.ts'], runtime, 'facades'),
    ]
    changed = subprocess.check_output(['git', 'diff', '--name-only', '--diff-filter=ACM', '5fb96088', 'HEAD'], cwd=runtime, text=True).splitlines()
    scoped = [name for name in changed if name.endswith(('.ts', '.tsx')) and name.startswith(('packages/opencode/', 'packages/core/', 'packages/kilo-sandbox/'))]
    if scoped:
        commands.append(([args.bun, 'run', 'lint', '--threads=2', *scoped], runtime, 'scoped-lint'))
    for command, cwd, name in commands:
        command_step(command, cwd, logs / f'{name}.log', environment)
    return {'status': 'passed', 'checks': [name for _, _, name in commands], 'catalog': evidence}


def read_results(unit: dict) -> tuple[list[dict], list[str], bool]:
    """Keep valid evidence if a process stops in the middle of a JSONL write."""
    from jsonschema import Draft202012Validator
    path = Path(unit['output'])
    if not path.exists():
        return [], ['result file missing'], False
    schema = 'trajectory-run.schema.json' if unit['kind'] == 'trajectory' else 'action-result.schema.json'
    validator = Draft202012Validator(json.loads((ROOT / 'schemas' / schema).read_text()))
    prediction = Draft202012Validator(json.loads((ROOT / 'schemas/action-prediction.schema.json').read_text()))
    rows, issues = [], []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            error = next(validator.iter_errors(row), None)
            if error:
                raise ValueError(error.message)
            if unit['kind'] == 'action_policy' and row['status'] == 'ok' and not prediction.is_valid(row['prediction']):
                raise ValueError('invalid prediction')
            if [row.get('scenario_id', row.get('case_id')), row['repeat_index']] not in unit['trials']:
                raise ValueError('trial not in plan')
            if unit['kind'] == 'trajectory' and (row['arm'] != unit['arm'] or row.get('interaction', 'autonomous') != unit['interaction']):
                raise ValueError('arm or interaction differs from plan')
            rows.append(row)
        except ValueError as error:
            issues.append(f'{path.name}:{number}: invalid record ({type(error).__name__})')
    actual = Counter((row.get('scenario_id', row.get('case_id')), row['repeat_index']) for row in rows)
    expected = Counter(tuple(trial) for trial in unit['trials'])
    if actual != expected:
        issues.append('trial coverage differs from plan (missing, duplicate or unexpected trials)')
    return rows, issues, not issues


def report_history(folder: Path) -> list[dict]:
    """Re-score each archived source separately; never turn old effects into verified I/O."""
    from score_trajectory import build_report
    from score_action_policy import score, load_labels, load_case_group_ids
    from render_guard_demo import render_report
    sources = check_catalog()['history']
    scenarios = {row['scenario_id']: row for entry in select() if entry['kind'] == 'trajectory' for row in records(ROOT / entry['path'])}
    labels = load_labels(ROOT / 'datasets/action-policy/dev/labels.jsonl')
    groups = load_case_group_ids(ROOT / 'datasets/action-policy/dev/cases.jsonl')
    lines = ['# Исторические прогоны AutoGuard', '', 'Каждый источник показан отдельно. Эти измерения не подтверждают качество текущей версии. Неизвестное исполнение в старых журналах остаётся неизвестным; L2 встречается только в архиве.', '', '| Источник | Записей | Отчёт |', '|---|---:|---|']
    for entry in sources:
        rows = records(ROOT / entry['path'])
        if entry['kind'] == 'trajectory':
            mapping = {row['scenario_id']: row for row in records(ROOT / entry['scenarios'])} if entry.get('scenarios') else scenarios
            metrics = build_report(rows, mapping)
            write_json(folder / entry['id'] / 'scores.json', metrics)
            (folder / entry['id'] / 'summary.md').write_text(render_report(metrics))
            target = 'summary.md'
        else:
            metrics = score(rows, labels, groups)
            write_json(folder / entry['id'] / 'scores.json', metrics)
            target = 'scores.json'
        entry['report'] = entry['id'] + '/' + target
        lines.append(f'| {entry["id"]} | {entry["records"]} | [открыть]({entry["report"]}) |')
    write_json(folder / 'index.json', {'sources': sources, 'scope': 'historical, not pooled'})
    (folder / 'summary.md').write_text('\n'.join(lines) + '\n')
    return sources


def report_campaign(folder: Path) -> dict:
    from score_trajectory import build_report, read_action_review
    from render_guard_demo import render_report
    manifest = json.loads((folder / 'manifest.json').read_text())
    panels = []
    for entry in manifest['suites']:
        units = [unit for unit in manifest['units'] if unit['suite'] == entry['id']]
        rows, issues, variants = [], [], {}
        complete = True
        for unit in units:
            values, errors, valid = read_results(unit)
            rows += values
            variants[unit['arm']] = values
            issues += [unit['arm'] + ': ' + error for error in errors]
            complete = complete and valid
        panel = {'suite': entry['id'], 'kind': entry['kind'], 'expected_records': sum(unit['expected_records'] for unit in units), 'observed_records': len(rows), 'dataset_hash': entry['dataset']['hash'], 'collection_complete': complete, 'issues': issues}
        if entry['kind'] == 'trajectory' and rows:
            scenarios = {row['scenario_id']: row for row in entry['cases']}
            review = folder / entry['id'] / 'action-review.json'
            reviewed = review.exists() and bool(str(json.loads(review.read_text()).get('reviewed_by', '')).strip())
            panel['action_review'] = 'reviewed' if reviewed else 'unreviewed'
            labels = read_action_review(review, rows) if reviewed else {}
            panel['metrics'] = build_report(rows, scenarios, labels)
            write_json(folder / entry['id'] / 'scores.json', panel['metrics'])
            (folder / entry['id'] / 'summary.md').write_text(render_report(panel['metrics'], manifest))
        elif rows:
            from score_action_policy import score as action_report, load_labels, load_case_group_ids
            if fingerprint(entry)['hash'] != entry['dataset']['hash']:
                raise ValueError('policy cases or labels changed since the campaign; restore the recorded dataset to score')
            panel['metrics'] = {'by_arm': {arm: action_report(values, load_labels(ROOT / entry['labels']), load_case_group_ids(ROOT / entry['path'])) for arm, values in variants.items() if values}}
            panel['scope'] = 'Action classification with curated authority, no execution; draft labels, not holdout acceptance.'
            write_json(folder / entry['id'] / 'scores.json', panel['metrics'])
        panels.append(panel)
    report = {'schema_version': 1, 'collection_complete': all(panel['collection_complete'] for panel in panels), 'panels': panels,
              'history': report_history(folder / 'history'), 'note': 'Suites, interactions and historical configurations are not pooled into one efficacy score.'}
    write_json(folder / 'report.json', report)
    lines = ['# AutoGuard: единый тестовый контур', '', '| Набор | Записано / запланировано | Статус сбора | Отчёт |', '|---|---:|---|---|']
    for panel in panels:
        link = f'[{panel["suite"]}]({panel["suite"]}/summary.md)' if panel['kind']=='trajectory' and panel.get('metrics') else f'[{panel["suite"]}]({panel["suite"]}/scores.json)' if panel.get('metrics') else 'ожидает запуска'
        lines.append(f'| {panel["suite"]} | {panel["observed_records"]} / {panel["expected_records"]} | {"полный" if panel["collection_complete"] else "неполный"} | {link} |')
    lines += ['', '[Исторические результаты Шамиля и v2](history/summary.md) сохранены отдельными источниками с хешами в report.json. Они не смешиваются с новыми измерениями. Полный сбор не означает прохождение порогов безопасности/Utility.', '']
    (folder / 'summary.md').write_text('\n'.join(lines))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['check', 'plan', 'run', 'report', 'catalog', 'history', 'review-actions'])
    parser.add_argument('--suite', action='append')
    parser.add_argument('--kilo-repo', type=Path, default=ROOT.parent.parent/'autoguard-runtime')
    parser.add_argument('--bun', default='bun')
    parser.add_argument('--agent-model', default='openrouter/openai/gpt-oss-120b')
    parser.add_argument('--guard-level1-model', default='Qwen3.5-9B')
    parser.add_argument('--arm', action='append', choices=ARMS, dest='arms')
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--scenario-id', action='append')
    parser.add_argument('--temperature', type=float, default=0)
    parser.add_argument('--agent-timeout', type=int, default=180)
    parser.add_argument('--diagnostics', type=Path, default=Path.home()/'.local/state/autoguard-evaluation')
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--lock', type=Path, default=DEFAULT_LOCK)
    parser.add_argument('--allow-unpinned-kilo', action='store_true')
    parser.add_argument('--allow-dirty-kilo', action='store_true')
    args = parser.parse_args(argv)
    if args.repeats < 1 or (args.limit is not None and args.limit < 1):
        parser.error('repeats and limit must be positive')
    args.arms = list(dict.fromkeys(args.arms or ARMS))
    if args.mode in ('report', 'review-actions') and args.output_dir is None:
        parser.error(args.mode + ' requires --output-dir of an existing campaign')
    args.output_dir = (args.output_dir or ROOT/'results/raw'/('suite-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))).resolve()
    try:
        entries = select(args.suite)
        if args.mode == 'catalog':
            print(json.dumps(check_catalog(), indent=2)); return 0
        if args.mode == 'report':
            report_campaign(args.output_dir); print(args.output_dir/'summary.md'); return 0
        if args.mode == 'history':
            args.output_dir.mkdir(parents=True, exist_ok=False)
            report_history(args.output_dir); print(args.output_dir/'summary.md'); return 0
        if args.mode == 'review-actions':
            from prepare_review import actions
            manifest = json.loads((args.output_dir/'manifest.json').read_text())
            packets = [(entry, args.output_dir/entry['id']/'action-review.json') for entry in manifest['suites'] if entry['kind'] == 'trajectory' and entry['id'] in {e['id'] for e in entries}]
            if any(path.exists() for _, path in packets):
                raise ValueError('action review already exists; preserve the reviewer\'s work')
            for entry, path in packets:
                files = [Path(unit['output']) for unit in manifest['units'] if unit['suite'] == entry['id'] and Path(unit['output']).exists()]
                if files:
                    actions(files, path)
                    print('Unreviewed action packet:', path)
            return 0
        if args.mode == 'check':
            args.output_dir.mkdir(parents=True, exist_ok=False)
            result = run_checks(args, entries); write_json(args.output_dir/'checks.json', result)
            print('All checks passed:', args.output_dir/'checks.json'); return 0
        plan = build_plan(args, entries)
        if args.mode == 'plan':
            print(json.dumps(plan, indent=2)); return 0
        # Review the entire selection before starting even its first model call.
        for entry in plan['suites']:
            if entry.get('review'):
                require_review(ROOT / entry['review'], fingerprint(entry), plan['runtime']['hash'])
        args.output_dir.mkdir(parents=True, exist_ok=False)
        write_json(args.output_dir/'manifest.json', plan)
        environment = {**private_environment(), **os.environ, 'AUTOGUARD_L1_MODEL':args.guard_level1_model}
        try:
            for index, unit in enumerate(plan['units']):
                Path(unit['output']).parent.mkdir(parents=True, exist_ok=True)
                command_step(unit['command'], Path(unit['cwd']), args.output_dir/'logs'/f'{index:02d}.log', environment)
                result = records(Path(unit['output']))
                if any(row.get('status') in ('api_error','infrastructure_error','agent_error') for row in result):
                    raise ValueError(f'{unit["suite"]}/{unit["arm"]}: model/runtime error; partial evidence preserved, see logs')
        finally:
            report_campaign(args.output_dir)
        print('Campaign report:', args.output_dir/'summary.md'); return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr); return 2


if __name__ == '__main__':
    raise SystemExit(main())
