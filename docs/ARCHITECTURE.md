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
Rule based intent parsing ships first; model driven planning is a later,
carefully gated upgrade.

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
* No silent imports. A user controlled import path arrives in a later milestone.

## D. Skills and tool system

Files: `src/mitra/tools/`

`Tool` defines name, description, read only flag, and an `invoke` contract.
`ToolRegistry` holds registration and discovery. Milestone two ships six:

* `read_file`: read only, confined to configured allowed directories.
* `propose_write_file`: stages a write proposal, never writes directly.
* `list_dir`, `search_files`: read only discovery inside scope.
* `fetch_url`: read only http fetch with size and time caps.
* `run_shell`: stages an allowlisted command. Disabled unless config
  enables it. Every run needs approval, runs argv only with no shell
  interpreter, and stays pinned to the first allowed directory.

Staged proposals persist in SQLite with expiry and random single use
tokens. Chat and API decide through one shared `decide` path.

Future tools (shell, GitHub, calendar, REST, MCP) plug into the same
registry and inherit policy plus audit without orchestrator changes.

## E. Security and permission layer

Files: `src/mitra/security/`

* Least privilege: tools declare needs, policy grants narrowly.
* Scope confinement: file tools resolve paths and reject escapes.
* Approvals: writes, shell commands, and sensitive ops pause until the
  user approves or denies. Proposals persist with expiry and random tokens.
* Audit: every proposal, execution, denial, memory mutation, task change,
  fetch, and shell run is logged.
* Secrets: environment only, redacted from logs.
* Trust boundary: tool output and document text are data, never instructions.
  The orchestrator never follows commands found inside retrieved content.
* Cloud sharing is opt in per request, never silent.

## F. Interaction layer

Files: `src/mitra/cli.py`, `src/mitra/api.py`

Milestone two: terminal chat, FastAPI endpoints, and a JARVIS style
browser HUD over the same core. Voice output is mandatory: the HUD speaks
through browser speech, the terminal speaks through a native backend with
`-v`. Speech to text stays limited to an experimental browser mic button;
server side voice input, push to talk, and wake word remain future layers
that talk to this core through the API, never wired into reasoning.

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
