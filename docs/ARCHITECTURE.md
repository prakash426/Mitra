# Mitra Architecture

## System map

```
User
  |
  +-> CLI chat (terminal, optional spoken replies)  ->\
  |                                                     +-> Orchestrator -> Model provider
  +-> HUD (browser, voice output)  -> HTTP API (FastAPI) ->/      |
                                                                  +-> Memory store (SQLite)
                                                                  +-> Tool registry (allowlisted)
                                                                  +-> Policy gate (persistent approvals)
                                                                  +-> Audit log (append only)
```

All interfaces share one orchestrator per project profile. No logic lives in
the interface layer beyond input and output formatting. The HUD is static
files served by the API itself, so one process delivers everything.

## A. Core orchestrator

File: `src/mitra/orchestrator.py`

A single transparent loop, deliberately not a multi agent framework:

1. Parse the message for explicit intents: remember, recall, read file,
   propose write, help.
2. Load project scoped memory evidence through retrieval, never a full dump.
3. Match the request to a registered tool, if any.
4. Check policy: allowed, needs approval, or denied.
5. Execute or pause for approval, then record the outcome in the audit log.
6. Compose the reply through the configured model provider.

Each step returns a typed result so tests can assert the full chain.
Rule based intent parsing handles explicit commands; model driven
planning is one swappable step for multi step requests on reasoning
providers, with chat fallback when the model returns no usable plan.

Open answer mode: providers that declare `can_reason` receive open chat
questions directly with standing instructions and memory context, so real
models give real answers. The mock provider keeps deterministic drafts.
Memory and tool flows always stay grounded in cited evidence.

Plans (`src/mitra/planning.py`) run read only steps at once and pause at
the first staged mutation for approval. Checkpoints persist in SQLite,
so `continue plan-N` resumes after restarts. Denied or expired proposals
abandon the plan instead of skipping the step. Plans never cross project
boundaries; cross project comparison is a separate consented flow.
Finished plans save as named workflows with `{{parameter}}` placeholders
(`save plan-N as NAME with OLD as KEY`); `run workflow NAME k=v` replays
them through the same steps, checkpoints, and approvals.

## B. Model provider abstraction

Files: `src/mitra/providers/`

`ModelProvider` defines one contract: `generate(prompt, context)`.
Adapters:

* `mock`: deterministic replies for tests and offline demos.
* `ollama`: local inference over HTTP, default for real runs.
* `openai_compat`: generic OpenAI style API, also covers Meta Model API
  style endpoints through base URL plus key.

Provider details never leak into orchestration. Timeouts, errors, and missing
configuration surface as typed failures with graceful fallback text.
Keys come from environment only, never source.

## C. Persistent memory engine

Files: `src/mitra/memory/`

SQLite over stdlib `sqlite3`, with FTS5 full text search for retrieval.
Record shape:

* id, project, kind (preference, fact, decision, correction, task,
  note, standing)
* text, source, created and updated timestamps
* supersedes pointer so a later explicit correction wins

Tracked tasks live in a separate table with open, doing, and done states.
Standing instructions are a memory kind that the orchestrator prepends to
chat context automatically.

Rules:

* Every read returns source plus timestamp evidence.
* Latest explicit correction overrides older conflicts in the same project.
* Projects are strictly isolated in queries.
* Update and delete are explicit operations, audited like tool actions.
* No silent imports. The user controlled import path reads a user named
  file, parses JSON or plain text, and stages one approval proposal with
  a preview; imported records carry an `import:` source tag.
* Optional vector recall (`memory/vectors.py`) layers beside FTS5: local
  Ollama embeddings, vectors in SQLite, cosine ranking merged with
  keyword hits, silent fallback to keywords on any failure.

## D. Skills and tool system

Files: `src/mitra/tools/`

`Tool` defines name, description, read only flag, and an `invoke` contract.
`ToolRegistry` holds registration and discovery. Twelve built in tools ship,
plus dynamic MCP tools per configured server:

* `read_file`: read only, confined to configured allowed directories.
* `propose_write_file`: stages a write proposal, never writes directly.
* `list_dir`, `search_files`: read only discovery inside scope.
* `fetch_url`: read only http fetch with size and time caps, a host
  allowlist option, private address blocking with per host exceptions,
  and per hop redirect checks.
* `run_shell`: stages an allowlisted command. Disabled unless config
  enables it. Every run needs approval, runs argv only with no shell
  interpreter, stays pinned to the first allowed directory, passes a
  deny pattern screen for destructive shapes, and honors per command
  flag rules where configured.
* `github_read_file`, `github_list_prs`: read only repo reads and PR lists.
* `github_open_pr`: stages a PR proposal, never posts directly.
* `calendar_next`, `mail_recent`: read only macOS Calendar and Mail
  lookups through generated AppleScript.
* `calendar_add`: stages an event proposal through policy date and time
  checks, never books directly. Approval executes one generated
  AppleScript against the first calendar.
* `mcp__<server>__<tool>`: third party MCP tools, discovered at startup
  from `[mcp.servers]`. Trust `read` runs directly, trust `staged`
  stages an approval proposal per call. Calls reuse a persistent session
  per server with respawn on death; the orchestrator owns the pool and
  closes it on shutdown. Resource listing and reading ride the same
  sessions, with reads honoring the server trust tier.

Staged proposals persist in SQLite with expiry and random single use
tokens. Chat and API decide through one shared `decide` path.

Future tools (REST, mail writers) plug into the same registry and
inherit policy plus audit without orchestrator changes.

## E. Security and permission layer

Files: `src/mitra/security/`

* Least privilege: tools declare needs, policy grants narrowly.
* Scope confinement: file tools resolve paths and reject escapes.
* Approvals: writes, shell commands, and sensitive ops pause until the
  user approves or denies. Proposals persist with expiry, random tokens,
  and retention purging of old decided rows.
* Audit: every proposal, execution, denial, memory mutation, task change,
  fetch, and shell run is logged in a hash chained, verifiable trail.
* Secrets: environment only, redacted from logs.
* Trust boundary: tool output and document text are data, never instructions.
  The orchestrator never follows commands found inside retrieved content.
* Cloud sharing is opt in per request, never silent.

## F. Interaction layer

Files: `src/mitra/cli.py`, `src/mitra/api.py`

Milestone three: terminal chat, FastAPI endpoints, and a JARVIS style
browser HUD over the same core. Voice output is mandatory and natural:
the HUD ranks neural voices first with pace and tone controls, and the
terminal speaks through a native backend with `-v` plus rate control.
Sending a message interrupts speech. Speech to text runs on the server
(`POST /stt`, pluggable engines, stub by default) and the HUD mic
prefers it, falling back to browser recognition on any failure. Push to
talk works two ways: click the mic to toggle recording, or hold Space
outside the input box. Wake word stays future: it needs an always on
detection engine this sandbox cannot run. A Tauri desktop scaffold wraps
the same local API and owns the window only.

## G. macOS autostart

Two user LaunchAgents installed by `scripts/macos/install.sh`: one keeps
the API server alive from login, one waits for health and opens the HUD
URL first thing. Sleep needs no hooks because the server and browser tabs
survive it. Removal is one uninstall script; logs stay behind.

## Data flow for a tool request

1. User message arrives at CLI or API.
2. Orchestrator parses intent and retrieves memory evidence.
3. Registry offers matching tools; policy classifies each candidate.
4. Read path: execute inside scope, audit, answer with grounded summary.
5. Write path: stage proposal, request approval, audit the decision,
   execute only on approval, audit the result.
6. Model provider renders the final conversational reply.

## Module boundaries

* `config.py`: settings from file plus environment, validation, safe defaults.
* `orchestrator.py`: the loop, no provider or storage internals.
* `providers/`: model adapters, no business logic.
* `memory/`: storage and retrieval, no orchestration.
* `tools/`: capability implementations, no policy decisions.
* `security/`: policy plus audit, no tool internals.
* `speech.py`: voice output backends, no orchestration.
* `cli.py`, `api.py`, `web/`: thin interaction shells.
