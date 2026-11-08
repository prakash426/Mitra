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

Open http://127.0.0.1:8931 for the JARVIS style interface with your
portrait in the reactor, connectors on the left rail, briefing and
greeting on launch, plus voice output, approvals, tasks, and memory panels.

One command does both steps (API plus app):

```sh
bash scripts/macos/mitra-launch.sh
```

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
Content, System Voice, Manage Voices, then restart the browser. A
persona picker under the reactor offers JARVIS (measured, prefers
Daniel) and FRIDAY (warm, prefers Moira), each with its own pace and
greeting line. Pace and tone sliders plus a TEST button sit beside it;
your choices persist. Replies are shaped for speech with contractions
and breath pauses, so they sound like a person, not a reader.

In the terminal, `python -m mitra.cli -p Demo -v` speaks replies through
macOS speech. Set `backend = "say"`, pick a `voice` (or `auto` to let
the persona choose), set `persona = "jarvis"` or `"friday"`, and tune
`rate` in the `[speech]` config section. Sending a message interrupts
speech, so you can always cut Mitra off.

## Start Mitra with your Mac

One command installs Mitra as a login service. After any restart or
shutdown, the server starts at login and the HUD opens in your browser
first thing. Sleep needs nothing: the server and your tabs survive it, so
Mitra is already waiting when the Mac wakes.

```sh
bash scripts/macos/install.sh
```

To remove it later: `bash scripts/macos/uninstall.sh`. Server logs live
at `~/Library/Logs/mitra/server.log`. The HUD header also carries an
AUTOSTART pill that toggles the same login items without the terminal.

Prefer a clickable app instead: `bash scripts/macos/build-app.sh` builds
`dist/Mitra.app`, which you can keep in `/Applications` and add under
Login Items. It runs the same launcher, so Mitra greets you with a
recap of your last session, open tasks, pending approvals, and a
suggestion for what to do first.

## What Mitra does

* Remember and recall project scoped facts with sources and timestamps.
* Save several facts from one message; recall groups the whole topic.
* Answer how to questions with the exact command to send.
* Run a one click morning scan: today, inbox, open work, first move.
* Keep short term chat context, so followups resolve without repeats.
* Correct earlier facts; the latest explicit correction wins.
* Keep standing instructions that surface in every chat.
* Greet each launch with a recap plus suggestions for what is next.
* Track tasks with open, doing, and done states, plus due dates.
* Print standup: done, now, and blocked in one view.
* Book calendar events through the approval flow.
* Watch GitHub repos; open PRs join the morning scan.
* Save pages to memory and export notes to markdown.
* Read files inside allowed directories and summarize them.
* List directories, search file names, and fetch pages as text.
* Propose file writes and wait for your approval before touching disk.
* Run allowlisted shell commands only with explicit per command approval.
* Read GitHub repos and PRs, and stage PR proposals for approval.
* Read your macOS calendar and inbox (first run asks permission).
* Import memories from your own JSON or text files, always with approval.
* Recall by meaning too, with optional local vector search beside keywords.
* Plan multi step tasks with a reasoning model, pausing for approvals.
* Resume paused plans after restarts with `continue plan-N`.
* Compare memories across projects only with your explicit consent.
* Call third party MCP servers through read or staged trust tiers.
* Read MCP resources, with staged trust asking approval first.
* Save finished plans as reusable workflows with parameters.
* Transcribe voice input on the server; the HUD mic prefers it.
* Talk hands busy: click the mic, or hold Space outside the input.
* Resolve per project profiles for provider, scope, and shell settings.
* Speak replies aloud in the HUD and in the terminal with -v.
* Offer JARVIS and FRIDAY voice personas with human pacing.
* Show every connector on the left rail with connect and start task.
* Toggle autostart from the HUD header or install a Login Item app.
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
> addevent Team lunch on 2026-11-10 at 12:30 for 45
> check my inbox
> due 1 2026-11-10
> standup
> remember page https://example.com/article
> export notes
> plan: list the notes directory and then read the README
> compare deploy steps across CancerLens and Demo
> mcp notes search {"query": "release"}
> save plan-1 as brief with README.md as file
> run workflow brief file=NOTES.md
> mcp resources notes
```

Writes and shell commands stage a proposal and ask for approval.
Nothing runs first. Pending proposals survive restarts and expire on a timer.

Mitra learns its own manual from the `knowledge/` pack plus the repo docs.
Seed any project (run once per project):

```sh
python -m mitra.seed -p Mitra --docs
```

Reruns add only what is new, so seeding never duplicates records.

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
      github_tools  macos_tools  mcp_tools
    security/  policy  audit
    planning  mcp  stt  autostart  seed
    web/  HUD plus avatar and profile served at the root URL
  knowledge/  seed pack for project memory
  scripts/macos/  login install, launcher, app builder, opener
  desktop/  Tauri shell with dev hook that boots the API
  tests/
```

## Docs

* [User guide](docs/USER_GUIDE.md)
* [Product brief](docs/PRODUCT_BRIEF.md)
* [Architecture](docs/ARCHITECTURE.md)
* [Decisions](docs/DECISIONS.md)
* [Security](docs/SECURITY.md)
* [Roadmap](docs/ROADMAP.md)

## Status and limits

Milestone six is done: everything in five plus saved parameterized
workflows, persistent MCP sessions with resource reads, and HUD
push to talk. Milestone seven is nearly done: the desktop shell compiles
and boots, one command launches API plus app, and an unsigned Mitra.app
plus HUD autostart toggle cover login starts. Still ahead: signing and
notarization, wake word detection, and MCP prompts. Real transcription
needs the optional faster-whisper install. The shell tool stays disabled
unless you enable it with a narrow allowlist. See [Roadmap](docs/ROADMAP.md)
for what is next and [Security](docs/SECURITY.md) for known gaps.

Vector recall stays off until you opt in: set `embeddings = "ollama"`
in config and pull an embedding model (`ollama pull nomic-embed-text`).
It layers beside keyword search and falls back silently on failure.

## Contributing

Keep the core small and the contracts explicit. New capability means a new
tool plus tests plus policy coverage, never a core rewrite. Type hints,
clear errors, no secrets in code or logs.
