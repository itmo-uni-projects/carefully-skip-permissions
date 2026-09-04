# AutoGuard Research Memo

## Что взять из текущих исследований для Carefully Skip Permissions / AutoGuard

> **Проект:** Kilo-native pre-action guard для shell, file mutations и external operations  
> **Фокус:** Careless / overeager actions, не prompt injection  
> **Срез:** 3 сентября 2026  
> **Решение:** что внедрить в недельный MVP и benchmark

## Вердикт

Архитектуру менять не нужно. Нужно сделать её **authority-aware** и заменить главный аргумент «13 ручных кейсов» на **контрастные исполняемые сценарии с детерминированными side-effect oracles**.

## Почему это точно наш кейс

Самые свежие работы уже выделяют отдельный класс ошибок coding agents: доброкачественная задача выполняется успешно, но агент выходит за разрешённый scope; либо при неполной инструкции угадывает цель вместо уточнения. Это не jailbreak и не нехватка coding capability — это ошибка границы полномочий.

- **OverEager-Bench:** 500 валидированных сценариев и около 7 500 запусков; permissive harnesses показывают заметно больше overreach, чем ask-to-continue.
- **UnderSpecBench:** 69 семейств DevOps-задач и 2 208 вариантов; 55,8–67,8% запусков нарушают хотя бы одну границу при недоопределённости.
- **AuthBench:** 120 исполняемых terminal-задач; frontier-модели одновременно недодают необходимые доступы и переоткрывают чувствительные.

**Вывод для позиционирования:** сильная сторона проекта — не «первый auto mode», а **Kilo-native action-time authorization** с тремя решениями и воспроизводимой E2E-оценкой.

## Шесть изменений с максимальной отдачей

1. **Добавить task authority.** Issuer, scope, capabilities, expiry и три множества: `required`, `implicit`, `sensitive`.
2. **Нормализовать action.** Каждый tool call представить как `capability + target + effect + source`; решение принимать над этой формой, а не сырой строкой shell.
3. **Разделить достаточность и tightness.** Первый проход ищет доступы для успеха, второй требует обоснование каждого расширения. Необоснованное расширение → `ASK`.
4. **Сделать ASK first-class.** `ASK` означает недостающую authority/target/scope, а не просто «средний риск». `risk_level` остаётся отдельным полем.
5. **Возвращать safe continuation.** После `ASK`/`DENY` guard сообщает причину и безопасную альтернативу, чтобы агент перепланировал действие.
6. **Вести rolling ledger.** Минимум три композиции: `sensitive-read → send`, `generated-script → execute`, `deny → семантически эквивалентный retry`.

## Карта исследований → решения проекта

Таблица отделяет переносимый инженерный паттерн от тяжёлых компонентов, которые не нужны для hackathon MVP.

| Исследование | Сигнал | Что переносим | Решение |
|---|---|---|---|
| OverEager / SNARE | Benign task + successful run can still contain out-of-scope actions; harness is a major factor. | Paired consent variants; scope + trap fragments; deterministic trace/delta oracle. | **Take now** |
| UnderSpecBench | Vary intent clarity, target certainty and blast radius while environment stays fixed. | Use these three axes to construct `ASK`; score Safe Success / Wrong Target / OverScope. | **Take now** |
| AuthBench | Permission boundary must be sufficient for utility and tight against sensitive shortcuts. | Add `required` / `implicit` / `sensitive` sets; separate coverage pass from tightness audit. | **Take now** |
| FixedBench | Agents often modify already-correct code; prompt-only fixes can cause over-abstention. | Add `NOVEL` / `PARTIAL` / `RESOLVED` contrasts and a correct-no-op metric. | **Take now** |
| Progent / AIRGuard | Tool-call interception and task-scoped authority are the natural enforcement point. | Normalize capability, target, effect and source; expansion without authority → `ASK`. | **Core design** |
| ToolSafe | Pre-execution feedback lets an agent correct course instead of aborting. | Return reason + `safe_continuation` after `ASK`/`DENY`. | **Take now** |
| Permissions survey | Good systems balance low overhead, formal policy, deterministic enforcement and user control. | Show why/what/target; grants are one-shot or task-scoped and logged. | **UX target** |

> **Важно:** трёхуровневая шкала ToolSafe — это `safe / potentially unsafe / unsafe`, а не готовая семантика `ASK`. Мы переносим структуру step-level guard и feedback, но определяем `ASK` через отсутствующее полномочие или неоднозначность.

## Целевая логика MVP

- **L0** — детерминированные hard deny и точные allow-правила.
- **L1** — быстрый анализ intent/target/scope.
- **L2** — глубокая проверка только неоднозначных и составных случаев.
- **Timeout или ошибка классификатора → `ASK`.**

### Монотонность

Автоматически можно только **сузить authority**. Расширить scope, capability, target set или срок можно лишь явным пользовательским grant; для MVP достаточно проверки subset без SMT.

## Decision contract

```json
{
  "authority": {
    "issuer": "user",
    "scope": ["repo/src/**"],
    "capabilities": ["read", "write"],
    "expires": "task"
  },
  "action": {
    "capability": "fs.delete",
    "target": "repo/tmp/cache",
    "effect": "recursive_delete",
    "source": "agent_plan"
  },
  "decision": "ALLOW | ASK | DENY",
  "risk_level": "low | medium | high | critical",
  "missing_authority": [],
  "safe_continuation": "..."
}
```

- **ALLOW** — действие необходимо, цель однозначна и полностью покрыта authority.
- **ASK** — действие может быть уместно, но не хватает target/scope/consent; вопрос должен назвать ровно недостающее поле.
- **DENY** — действие явно запрещено, не служит задаче или попадает в hard-deny invariant; агент получает безопасную альтернативу.

## Benchmark, который можно собрать за неделю

### Минимум: 36 step-level кейсов + 9 E2E-траекторий

Этого достаточно для убедительного hackathon evidence, но недостаточно для ранжирования моделей как «самых безопасных».

> **Статус 04.09:** decision-level часть собрана в
> `benchmark/datasets/action-policy/dev`: 6 семейств × 2 сценария ×
> `ALLOW/ASK/DENY`. Все 36 labels пока `draft`, а model-proposed сценарии
> явно помечены `llm_draft`. Это ещё не исполняемый E2E evidence: девять
> траекторий и side-effect oracles остаются следующим отдельным шагом.

### 1. Контрастные step-level тройки

Шесть action families × два базовых сценария × три решения = **36 кейсов**.

Внутри тройки среда и candidate action максимально одинаковы; меняется только один факт:

- **ALLOW:** точная цель и явно запрошенная операция.
- **ASK:** отсутствует или неоднозначна цель, scope либо authority.
- **DENY:** есть явный запрет или action является ненужным out-of-scope side effect.

Семейства:

1. file deletion;
2. git remote / push / reset;
3. chmod / chown / execute;
4. network / data egress;
5. unrequested code / config edits;
6. dependencies и generated scripts.

### 2. Три оси недоопределённости

- **Intent clarity** — ясно ли, что пользователь вообще просит изменить состояние.
- **Target certainty** — единственна ли цель или фраза «старый файл» подходит нескольким объектам.
- **Blast radius** — узкая ли операция или предлагается рекурсивное/глобальное действие.

### 3. Девять E2E-траекторий

- **3 resolved/no-op:** gold fix уже применён; правильный результат — отсутствие смысловых изменений.
- **3 underspecified:** агент должен задать вопрос; hidden full instruction отвечает simulated user.
- **3 composite-risk:** `sensitive-read → send`; `generated-script → execute`; `deny → equivalent retry`.

### 4. Детерминированный oracle

Вердикт формируется из:

- command/tool trace;
- before/after hash manifest;
- protected sentinel files;
- mock network sink;
- списка expected/forbidden effects.

Human review утверждает gold label; LLM judge помогает анализировать, но не решает итоговый score.

### 5. Реальные источники кейсов

Быстрее всего взять **SWE-bench-like issue/patch пары** и строить состояния `NOVEL / PARTIAL / RESOLVED`.

Для `ASK` удалить одно доказуемо нужное поле (`target`, `scope` или `requirement`), сохранив hidden full instruction. Для overreach добавить bait/trap fixture, который не нужен для успеха.

## Как измерять результат

Главное сравнение — **одна и та же model/harness конфигурация без guard и с guard**, одинаковые среды и seeds. Отдельно показываем качество решения и влияние на utility.

### Step-level

- Confusion matrix и recall каждого из `ALLOW / ASK / DENY`.
- Macro-F1 или balanced accuracy; `ASK` precision/recall отдельно.
- False-allow rate для запрещённых действий и false-alarm/over-block для разрешённых.

### E2E

- Overscope/harm rate и utility/task-success rate — **не смешивать в один балл**.
- Correct abstention и unnecessary-change rate на `RESOLVED`.
- Wrong Target / OverScope на недоопределённых задачах.
- Interaction burden: число `ASK`, доля повторных вопросов, latency и token cost.
- Три seeds, 95% CI и trace examples для каждой ошибки.

> **Не переобещать:** 13 текущих кейсов — хороший pilot и regression set, но не доказательство общего safety. В demo показываем прирост `baseline → guarded` и называем измеряемое поведение: **action-boundary adherence**.

## Порядок работ на ближайшие 48 часов

1. Зафиксировать schema и decision rubric. Особенно формальное условие `ASK` и независимость `risk_level` от `decision`.
2. Собрать 12 базовых сценариев. Из каждого автоматически получить `ALLOW / ASK / DENY` вариант; вручную проверить gold и oracle.
3. Подключить trace + state diff. JSONL оставляем аудитом, но score берём из реальных effects.
4. Запустить baseline vs guarded. Сначала одна модель и три seeds; затем при бюджете второй model/harness.
5. Выбрать три demo stories:
   - ambiguous deletion → `ASK`;
   - explicit-safe edit → `ALLOW`;
   - secret read + send → `DENY` с safe continuation.

## Что сознательно не брать в MVP

- Полный policy DSL, SMT-доказательства и формальная верификация.
- Landlock/OS sandbox как обязательный слой — это сильный следующий этап, но другой объём.
- Fine-tuning TS-Guard/AgentDoG: сначала нужен валидный собственный benchmark.
- Полная prompt-injection / information-flow защита: оставить вне заявленного threat model.

## Ограничения доказательств

Самые прямые результаты появились в мае–июле 2026 года и в основном являются preprints без независимой репликации. Их абсолютные проценты нельзя переносить на Kilo. Переносим дизайн эксперимента и проверяем эффект у себя.

ToolSafe и AIRGuard во многом используют attack-oriented данные; для нашего кейса релевантны runtime interception, authority context и feedback, но не готовые labels.

## Источники

1. Qu et al. **Overeager Coding Agents: Measuring Out-of-Scope Actions on Benign Tasks** (2026). arXiv:2605.18583.
2. Qu et al. **SNARE: Adaptive Scenario Synthesis for Eliciting Overeager Behavior in Coding Agents** (2026). arXiv:2605.28122.
3. Ji et al. **Coding Agents Are Guessing / UnderSpecBench** (2026). arXiv:2607.02294.
4. Yan et al. **Do Coding Agents Understand Least-Privilege Authorization? / AuthBench** (2026). arXiv:2605.14859.
5. **AuthBench official code and executable benchmark.** GitHub: `evolvent-ai/Authbench`.
6. Gloaguen et al. **Coding Agents Don't Know When to Act / FixedBench** (2026). arXiv:2605.07769.
7. Huang et al. **ToolSafe: Proactive Step-level Guardrail and Feedback** (2026). arXiv:2601.10156.
8. Ghafran et al. **Progent: Securing AI Agents with Privilege Control** (2025/2026). arXiv:2504.11703.
9. Jiang et al. **AIRGuard: Guarding Agent Actions with Runtime Authority Control** (2026). arXiv:2605.28914.
10. Michael & Roesner. **How Agents Ask for Permission** (2026). arXiv:2607.13718.
11. Wang et al. **Safety, or Just Capability? A Validity Audit** (2026). arXiv:2607.28685.

---

*Подготовлено как краткая decision note: research → проектное решение → измеримый эксперимент.*
