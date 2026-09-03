# Kilo Code: repository structure, existing security controls, and where to intervene

Working notes for the `--carefully-skip-permissions` case (Alfa-Bank).
All file paths and line numbers verified against `Kilo-Org/kilocode` @ v7.5.9 (checked out 2026-09-03).

**Our fork:** https://github.com/itmo-uni-projects/kilocode

---

## 1. Two different agents live in one repository

`Kilo-Org/kilocode` is a Bun/Turbo monorepo containing **two separate coding agents** with **two separate permission engines**. Picking the wrong one costs days.

| | VS Code extension | **Kilo CLI** |
|---|---|---|
| Package | `packages/kilo-vscode` | `packages/opencode` (npm name `@kilocode/cli`) |
| Lineage | Cline → Roo Code → Kilo | fork of **opencode** (`anomalyco/opencode`, ex-`sst/opencode`) |
| Binary | — | `kilo`, `kilocode` |
| Upstream sync | none | Kilo regularly merges upstream opencode |
| Invocation for benchmarks | hard (needs VS Code host) | `kilo run "<task>"` — scriptable |

**We target Kilo CLI (`packages/opencode/`).** Reasons: it is the only one that can be driven headlessly by a benchmark harness; its permission core is small; and an upstream-clean patch lands in opencode as well as Kilo.

Note that Roo Code (`RooCodeInc/Roo-Code`) is **archived** as of 2026-05-15, so the extension's own lineage is effectively frozen upstream.

### Source layout convention

Kilo keeps its fork mergeable with a strict rule (see `AGENTS.md`, enforced by `script/check-kilocode-duplication.ts`):

> Everything is shared code from OpenCode, except folders that contain `kilo` in the name or have a parent directory that contains `kilo` in the name.

So:
- `packages/opencode/src/**` — upstream opencode code. Inline edits are marked `// kilocode_change`.
- `packages/opencode/src/kilocode/**` — Kilo-only code.
- `packages/opencode/test/kilocode/**` — Kilo-only tests.

Follow this convention in our fork too: it keeps `git merge upstream` cheap and makes any PR back to Kilo reviewable.

---

## 2. Monorepo layout

35 workspace packages. The ones that matter to us:

| Package | Role |
|---|---|
| **`opencode`** | The CLI agent itself: sessions, tools, permission engine, TUI. ~158k LOC of TS/TSX in `src/`. |
| **`plugin`** | Public plugin API (`@kilocode/plugin`). Hook type definitions. **Our cheapest integration surface.** |
| **`kilo-sandbox`** | OS-level sandboxing (`@kilocode/sandbox`). 2403 LOC, 18 files. Wraps `@anthropic-ai/sandbox-runtime` 0.0.63. |
| `core` | Shared primitives (`@opencode-ai/core`): config schemas, globals, errors, glob/wildcard utils. |
| `server` | HTTP API server for the agent. |
| `tui` | Terminal UI (OpenTUI/Solid). |
| `sdk`, `sdk-next`, `protocol`, `schema` | Client SDK and wire protocol. |
| `kilo-vscode`, `kilo-jetbrains` | IDE hosts. |
| `kilo-gateway`, `kilo-indexing`, `kilo-memory`, `kilo-telemetry` | Kilo cloud services, codebase indexing, memory, telemetry. |
| `kilo-docs` | The published documentation site. |

### Can we fork only `packages/opencode`?

**No.** `packages/opencode/package.json` declares 17 sibling `workspace:*` dependencies:

```
@opencode-ai/core  /script  /llm  /ui  /server  /tui  /protocol  /schema
                   /codemode  /http-recorder
@kilocode/kilo-gateway  kilo-indexing  kilo-memory  kilo-telemetry
                        plugin  plugin-atomic-chat  sandbox  sdk
```

These are unpublished workspace links, not registry versions. Extracting the package is a multi-day port and would destroy the upstream-PR path.

This does not matter. Forking the whole repo costs one command and ~266 MB of disk. Scope is determined by **what we write**, not what we clone — and Section 7 shows we may not need to modify the repo at all.

---

## 3. What already exists: the permission engine

### 3.1 Core model

| File | LOC | Role |
|---|---|---|
| `packages/opencode/src/permission/index.ts` | 557 | The engine: `evaluate`, `resolve`, `ask`, pending-request state, ruleset merge |
| `packages/opencode/src/permission/arity.ts` | 163 | `BashArity` — how many leading tokens of a shell command form its identity (`git branch` vs `npm install`) |
| `packages/opencode/src/kilocode/permission/*.ts` | ~630 (9 files) | Kilo additions: provenance, allow-everything, config paths, external-directory, headless, drain |

A **rule** is `{ permission, pattern, action }` where `action ∈ {allow, deny, ask}`. `permission` is a tool id (`bash`, `edit`, `read`, `webfetch`, `task`, `skill`, …) or a pseudo-tool. `pattern` is a glob matched against the command string or path.

Resolution is **last-match-wins** (`evaluate`, `index.ts:102`), so specificity comes from rule *ordering*, not from pattern length. Rulesets are layered: agent defaults → global config → project config → session → manual replies.

The service interface (`index.ts:75`):

```ts
export interface Interface {
  readonly ask: (input: AskInput) => Effect.Effect<AskOutcome, Error> // kilocode_change - was Effect<void>
  readonly reply: (input: ReplyInput) => Effect.Effect<void, NotFoundError>
  readonly list: () => Effect.Effect<ReadonlyArray<Request>>
  readonly saveAlwaysRules: ...
  readonly allowEverything: ...
  readonly pending: (id: string) => Effect.Effect<Request | undefined>
}

export interface AskOutcome {
  manual: boolean   // true = user was prompted; false = a rule auto-approved
  rule?: Rule       // the winning rule, carries a `source` marker
}
```

Kilo already widened `ask` to return the decision. Useful for us: friction/FP metrics fall out of this for free.

### 3.2 Pseudo-tools

Two permissions are not real tools but tripwires:

- **`external_directory`** — raised whenever a tool touches a path outside the workspace/worktree. Default `ask`, with an allowlist of whitelisted dirs (tmp, skills dirs, global config, reference dirs) — `agent/agent.ts:122-134`.
- **`doom_loop`** — raised when the agent repeats an identical call three times. Default `ask`. Raised at `session/processor.ts:502`.

### 3.3 Default rulesets

**Default (build) agent** — `agent/agent.ts:136`:

```ts
Permission.fromConfig({
  "*": "allow",                    // <- everything allowed by default
  doom_loop: "ask",
  external_directory: { "*": "ask", ...whitelistedDirs },
  suggest: "deny", question: "deny", interactive_terminal: "deny",
  plan_enter: "deny", plan_exit: "deny",
  repo_clone: "deny", repo_overview: "deny",
  read: { "*": "allow", "*.env": "ask", "*.env.*": "ask", "*.env.example": "allow" },
})
```

Read that first line carefully: **the default agent's baseline is `"*": "allow"`.** Arbitrary `bash` runs without a prompt in the default configuration. The only guards are the `.env` read ask, the external-directory ask, and the doom-loop ask. This is the baseline our benchmark measures against, and it is close to `--dangerously-skip-permissions` already.

**Restricted (plan / explore / ask) agents** — `kilocode/agent/index.ts:140`:

```ts
Permission.fromConfig({
  "*": "deny",
  bash: readOnlyBash,
  read: { "*": "allow", "*.env": "ask", "*.env.*": "ask", "*.env.example": "allow" },
  grep: "allow", glob: "allow", list: "allow", skill: "allow",
  question: "allow", webfetch: "allow", websearch: "allow", semantic_search: "allow",
  external_directory: { [Truncate.GLOB]: "allow" },
  ...mcp, ...guardedDenies,
  task: "deny",
})
```

`readOnlyBash` (`kilocode/agent/index.ts:69`) is a deny-by-default allowlist of ~30 read-only binaries (`cat`, `head`, `ls`, `rg`, `jq`, …) plus ~20 read-only `git` subcommands, layered with a **shell-metacharacter blocklist**:

```ts
"*\n*": "deny",  "*<(*": "deny",  "*|*": "deny",  "*;*": "deny",
"*&*":  "deny",  "*$(*": "deny",  "*`*":  "deny",  "*>*": "deny",
// plus flags that turn read-only tools into exec primitives:
"sort *--compress-program*", "rg *--pre *", "man *-P*", "ag *--pager*", ...
```

The in-repo comment on that block is worth quoting verbatim in our presentation:

> This is defense-in-depth, not a sandbox — the durable fix is OS-level sandboxing, not command-line string matching.

### 3.4 Pattern generation and provenance

- `kilocode/bash-hierarchy.ts` — turns `npm install lodash` into the ladder `npm *` → `npm install *` → `npm install lodash`, which is what the "always allow" UI offers. Directly reusable for a slopsquatting hook: the arity prefix already isolates the package-manager verb.
- `kilocode/permission/provenance.ts` (163 LOC) — records **why** a call was approved: `agent | global | project | yolo | session | manual | default`, plus `outsideWorkspace` and the target path. This is our friction / false-positive instrumentation, already built.
- `kilocode/permission/config-paths.ts` (227 LOC) — gates `edit` permissions and bash-originated `external_directory` requests against config file paths.

### 3.5 YOLO mode

`kilocode/permission/allow-everything.ts` (61 LOC) — the entire bypass is one rule:

```ts
const rules: Permission.Ruleset = [{ permission: "*", pattern: "*", action: "allow" }]
```

written into session or global config. This is the `kilo --auto` / bypass-permissions baseline our benchmark starts from, and (per the case brief) every attack passes against it.

---

## 4. What already exists: the sandbox

`packages/kilo-sandbox/` is a real OS-level confinement layer, and it is more than we expected to find:

```
seatbelt.ts, seatbelt-base.ts, seatbelt-network.ts   macOS sandbox-exec profiles
bubblewrap.ts                                        Linux bubblewrap
network.ts, proxy.ts, tls-client-hello.ts            egress relay, SNI-based host filtering
filesystem.ts, mutation.ts, mutation-worker.ts       mediated filesystem writes
profile.ts, backend.ts, destination.ts, path.ts      policy model
dependency: @anthropic-ai/sandbox-runtime 0.0.63
```

Wired into the CLI at `packages/opencode/src/kilocode/sandbox/` (activation, config, policy, network-tools, inheritance, state, store, git, preference).

**Configuration** (`kilocode/sandbox/config.ts:39`):

```ts
{
  enabled: false,        // default: OFF
  network: "deny",       // when enabled, outbound denied unless allowed_hosts is set
  writable_paths: [],
  allowed_hosts: [],     // exact host:port, e.g. "api.github.com:443"
}
```

Resolved network mode is `allow` / `deny` / `proxy` (proxy when an explicit host allowlist exists).

**This matters a lot for the case.** The brief requires at least one control that is *architecturally* immune to prompt injection. Kernel-enforced filesystem and egress policy is exactly that — an injected instruction cannot talk `sandbox-exec` into permitting a connection. The control exists; it is simply **disabled by default and has no policy authoring story**. "Enable, tune, and measure it" is a far cheaper path to that requirement than building one.

---

## 5. What already exists: the plugin API

`packages/plugin/src/index.ts` — plugins are JS/TS modules loaded in-process, resolved from config by local path or npm spec (`packages/opencode/src/plugin/loader.ts`). Verified hook signatures:

```ts
"permission.ask"?:      (input: Permission,
                         output: { status: "ask" | "deny" | "allow" }) => Promise<void>   // :261

"tool.execute.before"?: (input: { tool: string; sessionID: string; callID: string },
                         output: { args: any }) => Promise<void>                          // :266

"tool.execute.after"?:  (input: { tool, sessionID, callID, args },
                         output: { title: string; output: string; metadata: any })
                         => Promise<void>                                                  // :274

"chat.params"?, "chat.headers"?, "chat.message"?, "tool.definition"?, "shell.env"?, ...
```

Map this onto the Claude Code auto-mode architecture:

| Anthropic component | Kilo plugin hook |
|---|---|
| Transcript classifier gating tool calls (output layer) | `permission.ask` → set `output.status` |
| Prompt-injection probe screening tool results (input layer) | `tool.execute.after` → rewrite `output.output` |
| Pre-execution inspection (slopsquatting, package installs) | `tool.execute.before` → read `output.args` |

Exposing the permission decision itself as an extension point is unusual — most harnesses only expose the tool call. It means **the full auto-mode shape is reachable without forking.**

### Tool inventory

Registered at `packages/opencode/src/tool/registry.ts:258`:

`invalid, shell, read, glob, grep, edit, write, task, fetch, todo, search, clone*, overview*, skill, patch, question, lsp, plan, suggest, execute*` (`*` = kilocode additions), plus MCP tools and plugin-contributed tools.

---

## 6. What is **not** implemented

Gap analysis against the case requirements and the closed spec in [Kilo issue #9138](https://github.com/Kilo-Org/kilocode/issues/9138) (status: **closed as not planned**, assignee `shssoichiro`, no linked PRs — it is a free, detailed TZ, but a merge conversation has to be reopened before any PR is written).

| Missing | Notes |
|---|---|
| **LLM classifier on outgoing tool calls** | Nothing. Rule-based patterns or `allowEverything`, no middle ground. This is the core of auto mode. |
| **Prompt-injection screening of tool results** | Nothing. Untrusted content from `read`/`fetch`/`shell` output enters the agent context unfiltered. No trusted/untrusted data separation anywhere. |
| **Tool-call tiering by latency/risk** | No notion of "safe tools skip the check" vs "in-project edits skip the check because git makes them reviewable". Without this, any classifier destroys the Latency metric. |
| **Deny-and-continue semantics** | A denial today is a rejection error, not a structured tool result the agent can reason about and route around. Case requirement #5. |
| **Escalation backstop** | No denial counters, no "3 consecutive / 20 total → hand back to human" circuit breaker. |
| **Package-install interception** | Nothing checks a package name before `npm/pip/uv install` runs. Slopsquatting is wide open. `bash-hierarchy.ts` already isolates the verb, so the hook point exists. |
| **Action-sequence analysis** | Each permission ask is evaluated independently. An attack split across several individually-benign steps (`ls` → read secret → `curl`) is invisible. |
| **Security telemetry** | `provenance.ts` records the deciding source but nothing aggregates allow/deny/ask rates, latency, or FP for evaluation. |
| **Security module** | `src/kilocode/security/` contains one 26-line file (`github.ts`). There is no security subsystem to extend — we would be creating it. |
| **Sandbox by default / policy authoring** | Sandbox exists but `enabled: false`, and there is no guidance or generated policy for a given project. |

---

## 7. What we can implement, in order of cost

### Tier 0 — external plugin, zero fork

Our repo becomes one small TS package (~500–1500 LOC), installed into an unmodified `kilo` from npm via config. Benchmark runs plugin-on vs plugin-off — a clean A/B with no monorepo build.

1. **Two-stage classifier on `permission.ask`.**
   Stage 1: fast single-token allow/deny, biased toward blocking. Stage 2: chain-of-thought, only on stage-1 flags, sharing a cached prompt prefix.
   **Critical design rule, copied from Anthropic:** the classifier sees **only user messages and the bare tool-call payload**. Strip assistant prose and strip *all tool results* — tool output is precisely where hostile content enters. This, not the second model per se, is what makes the reviewer injection-resistant. (Goose's Adversary Mode shows the reviewer recent messages and is correspondingly weaker.)
2. **Tiering.** Tier 1: `read`, `grep`, `glob`, `list` never reach the classifier. Tier 2: in-project edits bypass it (git makes them reviewable). Tier 3: `shell`, `fetch`, external/out-of-project, `task` → classifier.
3. **Injection probe on `tool.execute.after`.** Screen tool output; on suspicion, prepend a warning rather than dropping the content (Anthropic's choice — dropping breaks utility).
4. **Deny-and-continue + escalation counters.** Return the denial with a reason so the agent can find another route; halt at 3 consecutive / 20 total.
5. **Slopsquatting check on `tool.execute.before`.** Parse install commands, resolve the package name against the registry *before* execution: existence, age, download counts, maintainer history, name-similarity to popular packages. Fully deterministic — no LLM in the decision path.

### Tier 1 — fork, touch two files

When the plugin API is not enough (it cannot cleanly express hard rulesets or intercept before ruleset assembly):

- `packages/opencode/src/kilocode/session/prompt.ts:372` — the single funnel where the ruleset is assembled and `permission.ask({...request, ruleset, hardRuleset})` fires.
- `packages/opencode/src/permission/index.ts:75` — the `Interface.ask` contract.

Other call sites for reference: `tool/shell.ts:749`, `session/processor.ts:502`, `session/tools.ts:492`, `kilocode/tool/interactive-terminal.ts:58`, `kilocode/tool/board.ts:52`, `tool/code-mode.ts:155`.

Keep the diff inside `src/kilocode/` where possible, per the fork convention.

### Tier 1.5 — the injection-proof control

Turn on `@kilocode/sandbox` with `network: "deny"` plus a generated `allowed_hosts` allowlist derived from the project's own manifests (registry hosts, git remote), and `writable_paths` scoped to the worktree. Measure ASR with the classifier disabled to show the deterministic layer's standalone contribution — that separation is exactly the "what we guarantee always vs. best-effort" honesty the brief asks for.

### Out of scope

Rewriting the permission engine, adding new workspace packages, touching the VS Code extension, action-sequence modelling. Four days.

---

## 8. Development loop

```bash
# repo pins bun@1.3.14 — check `bun --version` before installing
bun install

# run the CLI from source (no extension / web UI build needed)
bun run --cwd packages/opencode --conditions=node src/index.ts
# equivalently, from the repo root:
bun run dev

bun turbo typecheck                    # scope to one package to avoid whole-tree builds
bun run --cwd packages/opencode test
bun run lint                           # oxlint
bun run check:duplication              # enforces the kilocode_change convention
```

Headless invocation for the benchmark harness: `kilo run "<task>"`.

Repository facts: 266 MB checked out, ~158k LOC of TS/TSX under `packages/opencode/src`, MIT licensed, Bun 1.3.14 + Turbo 2.10, Effect-TS idioms throughout (`Effect.gen`, `Layer`, `Schema`) — budget ramp-up time for that if nobody on the team has used Effect.

---

## 9. Known issues to account for in the benchmark

- **Settings caching** ([issue #7247](https://github.com/Kilo-Org/kilocode/issues/7247)): project-level settings are read when the workspace loads and not re-read per prompt. **The harness must restart the process between runs**, or it measures stale policy.
- **CVE-2026-33068**: before v2.1.53, permission mode was resolved from settings files *before* the workspace-trust dialog was shown, letting a malicious repository bypass the trust prompt. Relevant as a threat-model example: config-file injection is a real vector in this codebase.
- **No isolation by default.** Kilo's own docs state the permission system is a UX feature to keep users aware of agent actions, **not** security isolation, and recommend Docker or a VM for true isolation. Our threat model should adopt the same honesty.
- **Model selection** must be changed globally in Kilo settings, not per-session, or it is not picked up (per the case-owner briefing).

---

## 10. References

- Kilo auto-mode/gatekeeper spec — https://github.com/Kilo-Org/kilocode/issues/9138 (closed as not planned)
- Anthropic, *How we built Claude Code auto mode* — https://www.anthropic.com/engineering/claude-code-auto-mode
- opencode permission system — https://deepwiki.com/sst/opencode/5.2-permission-system
- Kilo security & privacy — https://deepwiki.com/Kilo-Org/kilocode/18-security-and-privacy
- Kilo CLI vs OpenCode CLI — https://kilo.ai/cli/opencode
- Goose Adversary Mode (closest existing OSS reviewer-agent implementation) — https://goose-docs.ai/docs/guides/security/adversary-mode/
- OpenHands security analyzer (counter-example: agent self-annotates its own risk) — https://docs.openhands.dev/sdk/guides/security
- Cline safety-check bypass, CVE-2026-52024 / CVE-2026-52025 — https://www.manifold.security/blog/cline-code-execution-bypass
- TRAP benchmark — https://arxiv.org/html/2512.23128v3
