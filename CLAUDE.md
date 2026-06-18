# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current State of the Repository

This repository currently contains **no application code**. There is no `package.json`, `go.mod`, `Cargo.toml`, `pyproject.toml`, build scripts, tests, or source directories. The only contents are:

- `.agents/` — the operations framework (agent personas, rules, skills, workflows) described below
- `.claude/settings.local.json` — local permission allowlist (currently permits `rtk ls *` and `rtk find *`)

Until application code is added, there are no `build`, `lint`, `test`, or `run` commands to document. Do not invent or guess them. When a stack is chosen, the project-structure rules under `.agents/rules/` (e.g. `project-structure-go-backend.md`, `project-structure-rust-cargo.md`) define the expected layout — read those before scaffolding.

## The `.agents/` Framework (the actual "architecture" right now)

`.agents/` is a structured prompt library that governs how AI agents work in this repo. Treat it as authoritative — its files describe non-negotiable constraints. Four sibling directories, each with a distinct role:

| Directory | Contents | When it applies |
|---|---|---|
| `.agents/rules/` | ~40 always-on or model-decision rules | Hard constraints. Some are tagged `trigger: always_on` in their frontmatter and apply to every task; others are loaded conditionally based on the task. |
| `.agents/agents/` | 15 specialized agent personas (architect, backend-engineer, frontend-engineer, mobile-engineer, database-expert, devops-engineer, security-engineer, qa-analyst, ux-reviewer, performance-engineer, refactoring-specialist, incident-responder, technical-writer, test-automation-engineer, scout) | Used by `workflow-team.md` for `@agent[scope]` dispatch. Each persona has an EXCLUSIVE domain and explicit "DO NOT CROSS" boundaries. |
| `.agents/skills/` | ~50 reusable skill packs (debugging-protocol, code-review, refactoring-patterns, perf-optimization, frontend-design, guardrails, parallel-dispatch-{decomposition,ownership,dag,merge}, sequential-thinking, adr, plus per-language idiom packs) | Loaded on demand by workflows and agents. Many skills have language-specific subfiles under `languages/`. |
| `.agents/workflows/` | Phase definitions + two top-level orchestrators | Entry points for structured work. See below. |

### Entry-Point Workflows

- **`workflows/workflow-solo.md`** — Single agent executes all phases sequentially. The phases are a strict state machine: `Research → Implement → Integrate → (E2E if UI/API changed) → Verify → Ship`. Each phase has its own file (`phase-research.md`, `phase-implement.md`, etc.). Phases must not be skipped; `task.md` tracks state with `[ ]`/`[/]`/`[x]` markers and an item is only `[x]` once Verify passes.
- **`workflows/workflow-team.md`** — Multi-agent pipeline manager. Dispatches sub-agents via `@agent-name` (single) or `@agent-name[scope]` (parallel) across the primitives `SCOUT → DESIGN → PRE-MORTEM → BUILD → TEST → REVIEW → REMEDIATE → VERIFY → DOCUMENT`. Intra-domain parallelism requires running the four-step protocol (decompose → validate ownership → build DAG → execute levels) using the `parallel-dispatch-*` skills, with each builder agent in its own `git worktree`. Includes 12 template workflows (Full Feature, Bug Fix, Audit, Mobile, Perf, Security, Infra, Docs, Incident, Tech Debt, Combined Audit, Pre-Mortem).
- Single-purpose orchestrators also exist (`bugfix.md`, `refactor.md`, `audit.md`, `perf-optimize.md`) that compose the same phases.

### Rule Hierarchy (read `rules/rule-priority.md` first)

When rules conflict, this priority order applies — top wins:

1. Security Mandate (`rules/security-mandate.md`)
2. Rugged Software Constitution (`rules/rugged-software-constitution.md`) — "code will be attacked; generate defensibility"
3. Code Completion Mandate + Logging and Observability Mandate
4. Testability-First architecture (`rules/architectural-pattern.md`) — I/O behind interfaces, pure business logic, dependencies point inward
5. Feature-specific principles, including language idiom files (`{go,typescript,vue,flutter,rust,python}-idioms-and-patterns.md`)
6. **PRD-gated** principles (`feature-flags-principles.md`, `ci-cd-gitops-kubernetes.md`) — only apply if the PRD explicitly requires them; do not introduce on speculation
7. YAGNI / KISS — last resort, only when no security/reliability/maintainability trade-off exists

### Single Source of Truth for Layout

`rules/project-structure.md` is the **single source of truth** for project organization. Philosophy: organize by **feature**, not by technical layer (no top-level `controllers/`, `models/`, `services/`). Each feature is a vertical slice. The default monorepo layout is `apps/backend/`, `apps/frontend/`, `apps/mobile/`; single-app projects flatten this. Language-specific layouts live in the `project-structure-{go-backend,vue-frontend,flutter-mobile,rust-cargo,python-backend}.md` files — read the relevant one before creating directories.

## How to Approach Work Here

1. **Before any non-trivial task**, scan `.agents/rules/` for files tagged `trigger: always_on` and any rules whose `description` matches the task — these are constraints, not suggestions.
2. **For a feature**, pick `workflow-solo.md` (single agent) or `workflow-team.md` (multi-agent dispatch). Do not skip phases for velocity; each phase has a completion gate. Track work in `task.md` at the repo root using `[ ]`/`[/]`/`[x]`.
3. **Research phase output goes in `docs/research_logs/{feature}.md`**; architecture decisions become ADRs in `docs/decisions/NNNN-short-title.md` (use the `adr` skill).
4. **When introducing the first piece of code**, the stack choice is a significant architectural decision — write an ADR and update this `CLAUDE.md` with the chosen build/test/lint commands.
5. **For parallel multi-agent work**, BUILD/TEST/REMEDIATE/OPTIMIZE/REFACTOR sub-agents run in `git worktree`s under `.wt/<agent>-<scope>` per `workflow-team.md`'s lifecycle section; merges are squash-merges in dependency order.

## Repo Conventions Worth Knowing

- Git commits follow conventional format (`<type>(<scope>): <description>`) per `rules/git-workflow-principles.md` and `workflows/phase-commit.md`.
- The repo is not (yet) a git repository — `git init` will be required before the commit phase can run.
- `.claude/settings.local.json` only allows `rtk ls *` and `rtk find *` automatically; other tools will trigger permission prompts.
