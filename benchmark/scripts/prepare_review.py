#!/usr/bin/env python3
"""Prepare evidence for an independent human; never approve labels automatically."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from review_manifest import dataset, digest, source


def packet(file: Path, runtime: Path, output: Path) -> None:
    benchmark = Path(__file__).resolve().parents[1]
    rows = [json.loads(line) for line in file.read_text().splitlines() if line.strip()]
    manifest = dataset(file.resolve(), benchmark)
    policy = source(runtime.resolve())
    output.mkdir(parents=True, exist_ok=True)
    review = {
        "approved": False,
        "reviewed_by": "",
        "dataset_hash": manifest["hash"],
        "policy_hash": policy["hash"],
        "scenarios": {row["scenario_id"]: {"approved": False, "notes": ""} for row in rows},
    }
    (output / "review.json").write_text(json.dumps(review, indent=2, ensure_ascii=False) + "\n")
    (output / "manifest.json").write_text(json.dumps({"dataset": manifest, "runtime": policy}, indent=2) + "\n")
    lines = [
        "# AutoGuard: независимая проверка holdout",
        "",
        "Статус: **unreviewed**. Сценарии и метки предложены моделью; это не независимая проверка.",
        "",
        "Проверьте для каждого случая: понятность пользовательской задачи, допустимые изменения, достижимость решения только разрешёнными действиями, реалистичность внедрённой инструкции и соответствие harm/utility assertions. Исправления вносятся до запуска holdout и требуют нового пакета с актуальными хешами.",
        "",
        "После проверки заполните `reviewed_by`, отметьте проверенные сценарии и общий `approved` в `review.json`. Runner проверяет полный набор и совпадение хешей данных и политики. Автоматического одобрения нет.",
        "",
        f"Политика: `{policy['hash']}`. Данные: `{manifest['hash']}`.",
        "",
        "Dev использует исходные fixtures flaky-json-parse и stale-build-cleanup (см. dev/scenarios.jsonl); holdout использует три новых fixtures и отдельные механизмы: делегирование, перенос patch и shell-подстановку skill. Чистая и внедрённая версия одного fixture всегда в одном split. Reviewer должен дополнительно проверить отсутствие семантических копий.",
    ]
    for row in rows:
        fixture = benchmark / "fixtures" / row["fixture"]
        lines += ["", f"## {row['scenario_id']}", "", f"Задача: {row['task_prompt']}", "",
                  f"Предложенная категория: `{row.get('kind')}`; механизм: `{row.get('mechanism')}`; канал: `{row['injection']['channel']}`.", "",
                  "Допустимы изменения: " + ", ".join(row["oracles"]["collateral"]["allowed_change_globs"]) + ". Тесты служат проверкой; изменять или удалять их нельзя.", "",
                  "Каталог и assertions:", "", "```json", json.dumps({"catalog": row.get("trusted_context"), "oracles": row["oracles"]}, indent=2, ensure_ascii=False), "```"]
        for path in sorted(fixture.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or ".git" in path.parts:
                continue
            lines += ["", f"`{row['fixture']}/{path.relative_to(fixture)}`", "", "````text", path.read_text(), "````"]
        payload = row["injection"].get("payload_id")
        if payload:
            lines += ["", "Внедряемые данные (не полномочие пользователя):", "", "````text", (benchmark / "fixtures/payloads" / f"{payload}.md").read_text(), "````"]
    (output / "REVIEW.md").write_text("\n".join(lines) + "\n")


def actions(files: list[Path], output: Path) -> None:
    runs = [json.loads(line) for file in files for line in file.read_text().splitlines() if line.strip()]
    labels = {
        f'{run["run_id"]}:{action["sequence"]}': {
            "approved": False, "expected": None, "severity": None,
            "scenario_id": run["scenario_id"], "actions": action.get("normalized_actions", []),
            "notes": "",
        }
        for run in runs for action in run.get("actions", [])
        if action.get("guard_decision") is not None and run.get("audit", {}).get("valid")
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"reviewed_by": "", "runs_hash": digest(json.dumps(runs, sort_keys=True).encode()), "labels": labels}, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--runs", action="append", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.runs:
        actions(args.runs, args.output)
    elif args.scenarios and args.runtime:
        packet(args.scenarios, args.runtime, args.output)
    else:
        parser.error("provide --scenarios and --runtime, or one or more --runs")
