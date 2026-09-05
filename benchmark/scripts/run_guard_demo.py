#!/usr/bin/env python3
"""Compatibility entrypoint for the unified native AutoGuard test suite."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / 'guard-demo.lock.json'


class DemoConfigurationError(ValueError):
    pass


def load_lock(path: Path) -> dict:
    value = json.loads(path.read_text())
    for key in ('demo_commit', 'entrypoint', 'plugin'):
        if not value.get(key):
            raise DemoConfigurationError(f'missing lock field: {key}')
    return value


def inspect_kilo_checkout(repo: Path, lock: dict, *, allow_dirty: bool = False, allow_unpinned: bool = False) -> dict:
    repo = repo.resolve()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
    commit = git('rev-parse', 'HEAD')
    dirty = bool(git('status', '--porcelain'))
    if dirty and not allow_dirty:
        raise DemoConfigurationError('Kilo has uncommitted changes; commit them before a measured run')
    if commit != lock['demo_commit'] and not allow_unpinned:
        raise DemoConfigurationError(f'Kilo is at {commit}, expected {lock["demo_commit"]}')
    for field in ('entrypoint', 'plugin'):
        if not (repo / lock[field]).is_file():
            raise DemoConfigurationError(f'missing {field}: {lock[field]}')
    if not (repo / 'packages/opencode/src/kilocode/autoguard/runtime.ts').is_file():
        raise DemoConfigurationError('the native AutoGuard v2 adapter is required')
    return {'root': str(repo), 'commit': commit, 'dirty': dirty}


def main() -> int:
    import sys
    from run_suite import main as run_suite
    args = sys.argv[1:]
    mode = 'plan' if '--plan-only' in args else 'run'
    return run_suite([mode, *[arg for arg in args if arg != '--plan-only']])


if __name__ == '__main__':
    raise SystemExit(main())
