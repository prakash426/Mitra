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

## M3. Answers, voice, autostart, hardening (shipped)

* Open answer mode: real models answer directly with memory context
* Natural voice output: neural ranking, pace and tone, speech shaping
* macOS autostart: login service opens the HUD first thing
* Fetch SSRF guards: hop revalidation, private block, host allowlist
* Shell deny patterns over the allowlist
* Proposal retention purge plus tamper evident audit chain with verify
* HUD barge in: sending interrupts spoken replies

Exit criteria: real answers on Ollama, natural voice, first at login,
plus a clean security review of shell and fetch.

## M4. Depth and reach (shipped)

* Per flag shell rules plus fetch host exceptions (allow and private)
* Optional vector recall layered beside FTS5 for paraphrase queries,
  off by default, lexical fallback always available
* GitHub connector: read repos, list PRs, propose PRs, approvals stay
  mandatory
* Calendar and email: read only macOS tools, each future action approved
* User controlled memory import: JSON or text file, staged, approved,
  source tagged
* Voice never touches reasoning internals; it calls the stable API

Exit criteria: first connectors live with per connector scopes,
paraphrase recall without weakening approvals or audit.

## M5. Desktop, voice input, intelligence (shipped, desktop scaffold only)

* Model driven planning as one swappable orchestrator step, chat fallback
* Multi step execution with persisted checkpoints and per step approval
* Cross project synthesis with explicit consent per comparison
* MCP client for third party tools through the same policy gate
* Server side speech to text with pluggable engines, HUD mic graduates
* Tauri desktop shell scaffold, compiling needs user side cargo

Exit criteria: multi step tasks run with approvals intact, first MCP
server live behind trust tiers, voice input reaches text parity for
read and recall flows.

Rule kept: every M5 feature stays grounded in cited evidence and never
weakens the approval or audit guarantees.

## M6. Polish and autonomy (shipped, hardware blocked items deferred)

* Saved reusable workflows with parameters, same checkpoint rules
* Push to talk in the HUD (click toggle plus hold Space)
* Richer MCP: resources plus longer lived sessions with respawn
* Heavier autonomy only behind the same approvals and audit

Deferred to M7 for want of hardware and toolchain in the build
environment: compiling and signing the Tauri app, and wake word
detection. MCP prompts stay future work pending a sampling UI design.

## M7. Release hardening (in progress)

* Desktop shell compiles and boots (done this session)
* Sign, bundle, and autostart the Tauri desktop app
* Wake word detection on top of server speech to text
* MCP prompts with user visible sampling approvals
* First real daily use review across all milestones
