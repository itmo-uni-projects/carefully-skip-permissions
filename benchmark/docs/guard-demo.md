# AutoGuard demo

Демо PR №4 объединено с native runtime v2. Актуальные наборы, конфигурация,
команды и ограничения: [единый тестовый контур](unified-suite.md).

Основной вход из корня benchmark-репозитория:

```sh
uv run --project benchmark python benchmark/scripts/run_suite.py plan
uv run --project benchmark python benchmark/scripts/run_suite.py check
uv run --project benchmark python benchmark/scripts/run_suite.py run
```

`run_guard_demo.py` сохранён как совместимый вход: `--plan-only` вызывает
`run_suite.py plan`, остальные аргументы передаются в `run_suite.py run`.
По умолчанию это весь единый набор с четырьмя trajectory-конфигурациями и
пятью повторами. Для короткой репетиции выбирайте `--suite`, `--scenario-id`
и `--repeats 1` явно.

Lock теперь закрепляет native runtime из `codex/autoguard-runtime`, а не
старый PR №2 `5fb96088`. Плагин наблюдения загружается во всех конфигурациях,
включая baseline. Старый флаг `--acknowledge-no-os-sandbox` больше не применяется:
используется штатный sandbox, настроенный общим runner.
