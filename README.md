# Mitra

A modular, local first AI companion. Mitra remembers per project, reasons
over retrieved evidence, and acts through allowlisted tools with explicit
approvals and a full audit trail.

Model independent by design: swap providers in config, zero core changes.

## Quickstart

Requirements: Python 3.11 or newer, macOS or Linux.

```sh
cd Mitra
cp config.example.toml config.toml
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Chat in the terminal (uses the deterministic mock by default, no keys needed):

```sh
python -m mitra.cli -p CancerLens
```

Run the local API, then open the HUD in your browser:

```sh
python -m mitra.api
```

Open http://127.0.0.1:8931 for the JARVIS style interface with voice output,
approvals, tasks, and memory panels.

Run the tests:

```sh
pytest -q
```

## Configuration

* `config.toml`: provider choice, model names, timeouts, allowed directories,
  database path. See `config.example.toml`.
* Environment (never commit): keys for cloud providers.

```sh
export MITRA_MODEL_KEY="your key here"
```

With Ollama running locally:

```toml
[provider]
name = "ollama"
model = "llama3.1"
base_url = "http://localhost:11434"
```

Switch back to the mock any time with `name = "mock"`. That single line is
the whole of scenario E.

## What Mitra does

* Remember and recall project scoped facts with sources and timestamps.
* Correct earlier facts; the latest explicit correction wins.
* Keep standing instructions that surface in every chat.
* Track tasks with open, doing, and done states.
* Read files inside allowed directories and summarize them.
* List directories, search file names, and fetch pages as text.
* Propose file writes and wait for your approval before touching disk.
* Run allowlisted shell commands only with explicit per command approval.
* Resolve per project profiles for provider, scope, and shell settings.
* Speak replies aloud in the HUD and in the terminal with -v.
* Log every proposal, decision, and execution.
* Fail gracefully when config or models are missing.

Try it:

```
> Remember that CancerLens uses a local first development workflow.
> What did we decide about CancerLens?
> todo: Review the M2 demo.
> standing: Prefer short answers.
> Read the README in my project directory and summarize it.
> run echo hello
```

Writes and shell commands stage a proposal and ask for approval.
Nothing runs first. Pending proposals survive restarts and expire on a timer.

## Project layout

```
Mitra/
  README.md  AGENTS.md  pyproject.toml
  config.example.toml  .env.example
  docs/  PRODUCT_BRIEF  ARCHITECTURE  DECISIONS  SECURITY  ROADMAP
  src/mitra/
    config.py  orchestrator.py  cli.py  api.py  speech.py
    providers/  base  mock  ollama  openai_compat
    memory/  store
    tools/  base  registry  file_tools  safe_tools  shell_tool
    security/  policy  audit
    web/  HUD served at the root URL
  tests/
```

## Docs

* [Product brief](docs/PRODUCT_BRIEF.md)
* [Architecture](docs/ARCHITECTURE.md)
* [Decisions](docs/DECISIONS.md)
* [Security](docs/SECURITY.md)
* [Roadmap](docs/ROADMAP.md)

## Status and limits

Milestone two is done: persistent approvals, tasks, standing instructions,
safe discovery tools, a gated shell tool, project profiles, voice output,
and the HUD. No connectors yet. The shell tool stays disabled unless you
enable it with a narrow allowlist. See [Roadmap](docs/ROADMAP.md) for what
is next and [Security](docs/SECURITY.md) for known gaps.

## Contributing

Keep the core small and the contracts explicit. New capability means a new
tool plus tests plus policy coverage, never a core rewrite. Type hints,
clear errors, no secrets in code or logs.
