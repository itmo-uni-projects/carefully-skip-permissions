# AutoGuard L0/L1

Классификация действий агента, сохраняемые полномочия и воспроизводимая оценка.

[Единый тестовый контур](benchmark/docs/unified-suite.md) объединяет наши
изменения, demo PR №4, исходные сценарии Шамиля и его 120 исторических прогонов.
Реестр: [benchmark/suite.json](benchmark/suite.json). Runtime хранится в соседнем
Kilo worktree `codex/autoguard-runtime`, версия закреплена в
[guard-demo.lock.json](benchmark/guard-demo.lock.json).

```sh
uv run --project benchmark python benchmark/scripts/run_suite.py check
uv run --project benchmark python benchmark/scripts/run_suite.py plan
uv run --project benchmark python benchmark/scripts/run_suite.py run
```

`check` и `plan` не вызывают модели. `run` по умолчанию планирует 480 trajectory
попыток и 540 отдельных решений L0/L1; для него нужны настроенные модели и бюджет.
Все новые прогоны используют native runtime v2; L2 выключен.

[Методология benchmark](benchmark/README.md) ·
[Контракты и границы v2](benchmark/AUTOGUARD_V2.md) ·
[Предварительные результаты v2](benchmark/results/reports/AUTOGUARD_V2_REPORT.md)
