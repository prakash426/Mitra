# Mitra Roadmap

## M1. Trusted vertical slice (shipped)

Goal: prove memory, tools, approvals, and audit end to end.

* Single orchestrator loop with typed steps
* Providers: mock, Ollama, OpenAI compatible
* SQLite memory with FTS5, project isolation, correction override
* Tools: allowlisted read plus staged write with approval
* Audit log for actions and decisions
* CLI chat plus FastAPI over one core
* Pytest suite covering the contract list in the brief

Exit criteria: all demo scenarios A through E run locally, full test suite
green, docs complete, no secrets in repo.

## M2. Usable daily companion (shipped)

* Persistent approval queue with expiry plus random single use tokens
* Richer memory: tasks with state, standing instructions that auto surface
* Safe tools: list directory, search files, fetch URL (read only, capped)
* Gated shell tool: allowlisted commands, confined cwd, per command approval
* Config profiles per project for provider, scope, and shell policy
* Adversarial prompt injection test pack, all green
* Mandatory voice output: browser speech in the HUD, native speech in CLI
* JARVIS style HUD served locally with approvals, tasks, and memory panels

Exit criteria: a week of real daily use with zero unapproved mutations.

## M3. Hardening and reach (next)

* Server side speech to text behind a media service, HUD mic graduates
* Interruption handling and barge in for spoken replies
* Flag level shell policy plus fetch host allowlist and private IP block
* Proposal retention purge plus audit hash chaining
* Optional vector recall layered beside FTS5 for paraphrase queries
* Voice never touches reasoning internals; it calls the stable API

Exit criteria: full text parity through voice for read and recall flows,
plus a clean security review of shell and fetch.

## M4. Desktop and connectors

* Lightweight desktop shell (Tauri candidate) over the local API
* GitHub connector: read repos, propose PRs, approvals stay mandatory
* Calendar and email: read first, act later, each action approved
* MCP client for third party tools through the same policy gate
* User controlled memory import path

Exit criteria: installable app with connector gallery and per connector scopes.

## M5. Intelligence upgrades (only after trust is proven)

* Model driven planning as one swappable orchestrator step
* Optional vector recall layered beside FTS5
* Multi step workflows with checkpoints and per step approval
* Cross project synthesis with explicit consent per query

Rule: each M5 feature must keep answers grounded in cited evidence and must
not weaken the approval or audit guarantees.
