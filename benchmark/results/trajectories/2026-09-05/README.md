# E2E-траектории, 05.09.2026

Записи прогонов и их оценки, закоммиченные так, чтобы каждое число в
`docs/experiments/trajectory-e2e-2026-09-05.md` перепроверялось скорером без
запуска модели.

## Что произвело эти файлы

- **Агент под тестом:** Kilo, `openrouter/openai/gpt-oss-120b`, `--auto`.
- **Гард:** `packages/opencode/src/kilocode/autoguard/` в форке
  `itmo-uni-projects/kilocode`, база `aac52a0a83` плюс изменения, описанные в
  отчёте.
- **Level 1:** `Qwen3.5-9B` через OpenAI-совместимый эндпоинт,
  `enable_thinking: false`.
- **Сценарии:** `benchmark/datasets/trajectories/dev/` — 8 сценариев,
  3 группы, 4 канала инъекции, 3 повтора.

## Файлы

| Файл | Конфигурация |
|---|---|
| `traj-guard-off.jsonl` | базовая линия, гарда нет |
| `traj-level0-level1.jsonl` | Level 0 + Level 1 |
| `traj-l0l1-with-a4.jsonl` | плюс правило `L0-A4:test_run_requested` |
| `traj-l0l1-grant.jsonl` | плюс извлечение гранта, **нерабочая версия** |
| `traj-l0l1-grant2.jsonl` | плюс извлечение гранта, исправленная |

`traj-l0l1-grant.jsonl` оставлен намеренно: в нём грант всегда выходил пустым
из-за обрезки хвостового слэша, `L0-A2` не сработало ни разу, и это видно в
`guard_reason_code`. Отчёт разбирает, почему юнит-тесты этого не показали.

## Перепроверка

```bash
cd benchmark && uv sync
uv run python scripts/validate_trajectory.py \
  --runs results/trajectories/2026-09-05/traj-guard-off.jsonl
uv run python scripts/score_trajectory.py \
  --runs results/trajectories/2026-09-05/traj-guard-off.jsonl \
  --runs results/trajectories/2026-09-05/traj-l0l1-grant2.jsonl
```

## Как это читать

Выборка мала: 8 сценариев × 3 повтора. Разница в один прогон из девяти —
шум, и это откалибровано прямо в данных: `traj-l0l1-grant.jsonl` функционально
идентичен `traj-l0l1-with-a4.jsonl` (грант не работал), а Utility сдвинулась
с 2/9 на 1/9.

Сценарии помечены `human_seed`, но независимой человеческой ревизии не
проходили. Три канала инъекции из четырёх дали нулевой ASR — это про слабость
payload'ов, а не про стойкость агента.

Транскриптов модели здесь нет: `actions[]` содержит аргументы вызова и
нормализованную форму, но не вывод инструментов и не содержимое файлов — гард
по построению их и не видит.
