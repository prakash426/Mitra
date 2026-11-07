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
export MITRA_GITHUB_TOKEN="github token for private repos and PRs"
```

Public GitHub reads work without a token. Prefer a fine grained token
limited to the repos Mitra needs.

With Ollama running locally:

```toml
[provider]
name = "ollama"
model = "llama3.1"
base_url = "http://localhost:11434"
```

Switch back to the mock any time with `name = "mock"`. That single line is
the whole of scenario E.

## Real answers need a real model

The default mock provider is deterministic and offline, which is why its
replies stay basic. It proves the wiring; it cannot think. For actual
answers, install Ollama and pull a model:

```sh
brew install ollama
ollama pull llama3.1
```

Then set `name = "ollama"` and `model = "llama3.1"` in config.toml and
restart. Chat questions then go to the model with your standing
instructions and memory context, while remembered facts stay grounded in
cited evidence.

## Voice

The HUD speaks every reply. It automatically prefers natural neural
voices (Siri, Premium, Enhanced) when your system has them. For the best
voice, download one in System Settings under Accessibility, Spoken
Content, System Voice, Manage Voices, then restart the browser. Pace and
tone sliders plus a TEST button sit under the reactor; your choices persist.

In the terminal, `python -m mitra.cli -p Demo -v` speaks replies through
macOS speech. Set `backend = "say"`, pick a `voice`, and tune `rate` in
the `[speech]` config section. Sending a message interrupts speech, so
you can always cut Mitra off.

## Start Mitra with your Mac

One command installs Mitra as a login service. After any restart or
shutdown, the server starts at login and the HUD opens in your browser
first thing. Sleep needs nothing: the server and your tabs survive it, so
Mitra is already waiting when the Mac wakes.

```sh
bash scripts/macos/install.sh
```

To remove it later: `bash scripts/macos/uninstall.sh`. Server logs live
at `~/Library/Logs/mitra/server.log`.

## What Mitra does

* Remember and recall project scoped facts with sources and timestamps.
* Correct earlier facts; the latest explicit correction wins.
* Keep standing instructions that surface in every chat.
* Track tasks with open, doing, and done states.
* Read files inside allowed directories and summarize them.
* List directories, search file names, and fetch pages as text.
* Propose file writes and wait for your approval before touching disk.
* Run allowlisted shell commands only with explicit per command approval.
* Read GitHub repos and PRs, and stage PR proposals for approval.
* Read your macOS calendar and inbox (first run asks permission).
* Import memories from your own JSON or text files, always with approval.
* Recall by meaning too, with optional local vector search beside keywords.
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
> import memories from notes.md
> github read octocat/Hello-World README.md
> what is on my calendar in the next 7 days?
> check my inbox
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
    memory/  store  vectors
    tools/  base  registry  file_tools  safe_tools  shell_tool
      github_tools  macos_tools
    security/  policy  audit
    web/  HUD served at the root URL
  scripts/macos/  login install plus uninstall
  tests/
```

## Docs

* [Product brief](docs/PRODUCT_BRIEF.md)
* [Architecture](docs/ARCHITECTURE.md)
* [Decisions](docs/DECISIONS.md)
* [Security](docs/SECURITY.md)
* [Roadmap](docs/ROADMAP.md)

## Status and limits

Milestone four is done: everything in three (open answers, natural
voice, autostart, SSRF guards, deny patterns, retention, audit chain)
plus per flag shell rules, fetch host exceptions, optional local vector
recall, a GitHub connector, read only calendar and inbox tools, and an
approval gated memory import path. No desktop shell, server side speech
input, or MCP client yet. The shell tool stays disabled unless you
enable it with a narrow allowlist. See [Roadmap](docs/ROADMAP.md) for
what is next and [Security](docs/SECURITY.md) for known gaps.

Vector recall stays off until you opt in: set `embeddings = "ollama"`
in config and pull an embedding model (`ollama pull nomic-embed-text`).
It layers beside keyword search and falls back silently on failure.

## Contributing

Keep the core small and the contracts explicit. New capability means a new
tool plus tests plus policy coverage, never a core rewrite. Type hints,
clear errors, no secrets in code or logs.
