# Архив полной серии AutoGuard

Основная серия `full-suite-20260906-02`: 1 020 испытаний, 6 панелей, 23 worker.
[Выводы и дефекты](../FULL_EVALUATION_2026-09-06.md).

`manifest.json` — неизменённый manifest запуска на benchmark `a28df87` и runtime
`fc43a2c`. Он содержит исходные абсолютные пути как provenance, а не как требование
к месту распаковки. `*.jsonl.gz` — точные записи, gzip с mtime 0. Все остальные
JSON/Markdown — сводки и неподписанные пакеты review; `sha256.json` проверяет файлы
архива, кроме самого индекса хешей. Сырые stdout/stderr и HTTP-ответы не публикуются.
Перед созданием архива проверено отсутствие значений локальных API-ключей.

## Пересчитать без моделей

Из корня benchmark-репозитория, с неизменёнными datasets и историческими архивами:

```sh
uv run --project benchmark python benchmark/results/reports/full-suite-20260906/replay.py \
  --output-dir benchmark/results/raw/replay-full-suite-20260906
```

Каталог назначения должен отсутствовать. Скрипт проверяет хеши, распаковывает
записи, меняет только пути `units[].output` в копии manifest, запускает штатный
scorer и сравнивает все метрики с сохранёнными шестью панелями. Исходный manifest
дополнительно сохраняется как `source-manifest.json`. Никакие команды исполнения
из manifest не запускаются; ключи моделей не нужны.

## Повторить модельную серию

Нужны runtime и зависимости из [runbook](../../../docs/unified-suite.md),
конфигурация моделей в `~/.config/autoguard/models.env` и новый каталог результатов:

```sh
uv run --project benchmark python benchmark/scripts/run_suite.py run \
  --suite all --bun /absolute/path/to/bun \
  --output-dir benchmark/results/raw/new-full-suite
```

Для точного повторения исходников используйте benchmark `a28df87` и runtime
`fc43a2c`. Temperature 0 и пять повторов не гарантируют одинаковую генерацию:
native seed не задавался, ответы провайдера и уникальные workspace-пути могут
отличаться. Повтор после изменений не считается новым независимым holdout.

## Метки и диагностика

[Порядок review действий](ACTION_REVIEW.md). Пакеты не подписаны и не дают права
объявлять FP или критические ALLOW проверенными.

`diagnostics.json` содержит разбор сохранённых событий и локальных protocol logs.
Для повторного локального разбора можно вызвать `collect_diagnostics.py` с путём
к распакованной кампании. Приватные stdout-пути могут отсутствовать на другом
компьютере: поле `private_protocol_logs_available` показывает это покрытие;
отсутствие логов не является отсутствием ошибок импорта или пустых ответов.

`credits-after.json` — счётчик всего аккаунта, не стоимость этой серии.
`pooled-latency-diagnostic.json` объединяет только операционные задержки native
вызовов; эффективность, autonomous/scripted и исторические источники разделены.
