# AutoGuard L0/L1

Классификация действий агента, сохраняемые полномочия и воспроизводимая оценка.

[Единый тестовый контур](benchmark/docs/unified-suite.md) объединяет наши
изменения, demo PR №4, исходные сценарии Шамиля и его 120 исторических прогонов.
Реестр: [benchmark/suite.json](benchmark/suite.json). Runtime хранится в соседнем
Kilo worktree `codex/autoguard-runtime`, версия закреплена в
[guard-demo.lock.json](benchmark/guard-demo.lock.json).

[PR №3 в Kilo Code](https://github.com/itmo-uni-projects/kilocode/pull/3) — ревью
последнего runtime-коммита `fc43a2c7` относительно его родителя. Коммит уже
опубликован в `main`; PR сохраняет отдельный diff для обсуждения.

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
[Проверка объединённого контура](benchmark/results/reports/UNIFIED_SUITE_REPORT.md)

Полная оценка из 1020 испытаний и воспроизводимые метрики:
[Метрики для презентации](benchmark/results/reports/PRESENTATION_METRICS_2026-09-06.md) ·
[Отчёт и ограничения](benchmark/results/reports/FULL_EVALUATION_2026-09-06.md).
Пороги приёмки этой серии не пройдены; результаты и причины приведены в отчёте.
