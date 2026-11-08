# Mitra user guide

Everything in one place: daily use, every command, voice, launch options,
and switching models. For the two minute version, see the README
quickstart. For internals, see Architecture.

## Contents

1. Install and first launch
2. The HUD at a glance
3. Talking to Mitra
4. Memory
5. Tasks and standup
6. Approvals
7. Files and web
8. Shell
9. GitHub
10. Calendar, mail, and booking
11. Morning routine
12. Plans and workflows
13. Comparing projects and MCP
14. Voice
15. Providers and switching models
16. Autostart and the desktop app
17. API reference
18. Troubleshooting

## 1. Install and first launch

Requirements: Python 3.11 or newer, macOS or Linux. macOS unlocks
Calendar, Mail, desktop, and autostart features.

```sh
cd Mitra
cp config.example.toml config.toml
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
bash scripts/macos/mitra-launch.sh
```

The launcher starts the API when it is down, waits for health, then opens
the desktop shell if it is built, else the HUD in your browser at
http://127.0.0.1:8931 (host and port come from config).

Teach Mitra its own manual once per project you care about:

```sh
python -m mitra.seed -p Mitra --docs
```

## 2. The HUD at a glance

Top bar: project picker, clock, voice and sound toggles, autostart toggle.

Left rail: connectors (every capability with Connect and Start Task),
system status, tasks with due dates and one click Done.

Center: your portrait in the reactor ring with orbiting details, persona
and voice controls, the transcript, and the composer with mic.

Right rail: briefing (last session recap plus suggestions and the
morning scan button), approvals, memory.

The HUD holds no secrets and makes no decisions. Every answer and every
approval runs through the local API.

## 3. Talking to Mitra

Pick a project first. Memory, tasks, and approvals are all scoped to it.

Mitra keeps short term chat context, so followups work. If it asks which
file to read or what content to write, just reply plainly; your answer
fills the pending question. Short replies with it, that, or same resolve
against the last topic.

Ask how to do anything and you get the literal command:

```text
How do I track a task?
How can I use github?
what is the command for approvals?
```

Type `help` any time for the full command list.

## 4. Memory

```text
Remember that Atlas uses Postgres.
Remember that Atlas deploys on Fridays.
What did we decide about Atlas?
Show me what is saved for my project.
Correction: Atlas deploys on Thursdays.
standing: Prefer short answers.
```

One message can save several facts; each becomes its own record and
recall returns the whole topic. The latest explicit correction wins.
Standing instructions apply to every chat in that project automatically.

Projects never see each other memories. Cross project comparison needs
explicit per query consent (see section 13).

## 5. Tasks and standup

```text
todo: Ship the Atlas demo.
tasks
start 1
due 1 2026-11-10
due 1 clear
done 1
standup
```

Due dates show in chat, the HUD task list, and the API. The briefing
warns about overdue and due today tasks, and nudges tasks open longer
than a week. Standup prints done in the last day, now, and blocked
(pending approvals plus overdue items) in one view.

## 6. Approvals

Writes, shell commands, imports, pull requests, calendar events, MCP
calls on staged trust servers, and cross project reads all stage a
proposal and wait. Nothing runs first.

```text
pending
approve prop-1
deny prop-1
```

Proposals survive restarts, expire on a timer, and every decision lands
in the audit log. The HUD approvals panel offers the same one click
decide buttons.

## 7. Files and web

```text
list
list notes
search *.md
search README in .
read README.md
fetch https://example.com/article
remember page https://example.com/article
save hello mitra to notes.txt
update doc.txt to brand new text
export notes
```

Reads stay inside allowed directories from config. Writes stage a
proposal first. Update without content reads the file, then asks for the
exact new text; your next reply becomes that content. Remember page
fetches a URL and stores the head as a note citing the source. Export
notes stages a markdown backup of memories plus tasks.

## 8. Shell

Disabled by default. Enable with a narrow allowlist in config:

```toml
[shell]
enabled = true
allowlist = ["ls", "pwd", "echo", "git"]
```

Then:

```text
run echo hello
approve prop-1
```

Every command needs per command approval. Metacharacters, off allowlist
binaries, deny patterns (recursive deletes, disk writes), and path
escapes are all denied before staging. Deny a proposal and nothing runs.

## 9. GitHub

```text
github read octocat/Hello-World README.md
github prs octocat/Hello-World
github open pr octocat/Hello-World from feat to main titled Cool thing
```

Public reads need no token. Private repos need a fine grained token in
the environment:

```sh
export MITRA_GITHUB_TOKEN="your token here"
```

Opening a PR stages a proposal; approve to publish. To watch repos,
list them in config and the morning scan shows their open PRs:

```toml
[github]
watch_repos = ["octocat/Hello-World"]
```

## 10. Calendar, mail, and booking

```text
what is on my calendar in the next 7 days?
check my inbox
addevent Team lunch on 2026-11-10 at 12:30 for 45
```

Reads use macOS Calendar and Mail through Automation; the first run asks
permission in System Settings under Privacy, Automation. Booking stages
a proposal first and books into your first calendar on approval. Date is
YYYY-MM-DD, time is HH:MM, minutes are optional and default to 30.

## 11. Morning routine

Three layers, cheapest first:

1. Briefing. Every launch greets you with a recap of your last session,
   open tasks, pending approvals, and suggestions. It lives in the right
   rail and doubles as the spoken greeting.
2. Morning scan. The RUN MORNING SCAN button pulls today calendar,
   fresh inbox subjects, open work with due dates, a first move, and
   watched repo PRs in one read only pass.
3. Standup. Say `standup` for done, now, and blocked as plain text you
   can paste anywhere.

## 12. Plans and workflows

Describe multi step work with `then` and a reasoning provider plans it:

```text
list the notes directory and then read the README
plans
continue plan-1
abandon plan-1
save plan-1 as brief with README.md as file
run workflow brief file=NOTES.md
workflows
forget workflow brief
```

Read steps run at once; the first mutation pauses for approval and the
plan resumes after you approve plus continue. Saved workflows replay
with parameters. Planning needs a reasoning provider; the mock and any
failed plan fall back to plain chat.

## 13. Comparing projects and MCP

```text
compare deploy steps across CancerLens and Demo
mcp notes search {"query": "release"}
mcp resources notes
mcp resource notes memory://latest
```

Comparison reads outside your project, so it stages a consent proposal
first. MCP servers spawn per call over stdio; trust `read` runs tools
directly while trust `staged` (default) asks approval per call. See the
example config for server entries.

## 14. Voice

The HUD speaks every reply through your browser voices and prefers
neural, Siri, Premium, or Enhanced voices when installed. For the best
voice, download one in System Settings under Accessibility, Spoken
Content, System Voice, Manage Voices, then restart the browser.

The persona picker offers:

* AUTO: best system voice, neutral pace.
* JARVIS: measured, prefers Daniel, slower pace.
* FRIDAY: warm, prefers Moira, brighter pace.

Pace and tone sliders plus TEST sit beside it; choices persist. Replies
are shaped for speech with contractions and breath pauses.

In the terminal:

```sh
python -m mitra.cli -p Demo -v
```

Set backend to `say`, voice to a name or `auto`, persona to `jarvis` or
`friday`, and tune rate in the speech config section.

## 15. Providers and switching models

One setting chooses the brain. Everything else stays the same: memory,
tools, approvals, and audit never change with the model.

```toml
[provider]
name = "mock"      # or ollama, or openai_compat
model = "llama3.1"
base_url = "http://localhost:11434"
timeout_seconds = 120
```

Mock: deterministic and offline. Proves the wiring; replies stay basic.
Use it for tests and demos.

Ollama: local inference, no key. Install once, pull a model, set the
name, restart:

```sh
brew install ollama
ollama pull llama3.1
```

Good local picks by machine size: `llama3.1:8b` for most Macs,
`qwen2.5:14b` when you have the RAM and want sharper reasoning,
`llama3.1:70b` only on large rigs. The first question loads the model
and can take up to a minute; later ones are fast. Timeout covers that
load; raise it for big models.

OpenAI compatible: any server speaking the OpenAI chat API. Set the
base URL and keep the key in the environment only:

```toml
[provider]
name = "openai_compat"
model = "your-model-name"
base_url = "https://your-endpoint/v1"
```

```sh
export MITRA_MODEL_KEY="your key here"
```

Environment overrides for quick switching without editing files:

```sh
export MITRA_PROVIDER="ollama"
export MITRA_MODEL="qwen2.5:14b"
```

Per project profiles pin different brains per project:

```toml
[profiles.CancerLens]
provider = "ollama"
model = "qwen2.5:14b"
allowed_dirs = ["."]
shell_enabled = false
```

Optional vector recall layers beside keyword search. Opt in and pull an
embedding model:

```toml
[memory]
embeddings = "ollama"
embed_model = "nomic-embed-text"
```

```sh
ollama pull nomic-embed-text
```

Failures fall back to keyword recall silently.

If a model call fails, Mitra says which model failed and shows the
direct result instead of guessing.

## 16. Autostart and the desktop app

One command runs everything:

```sh
bash scripts/macos/mitra-launch.sh
```

Login service (server starts at login, HUD opens first thing):

```sh
bash scripts/macos/install.sh
bash scripts/macos/uninstall.sh   # to remove later
```

Or toggle the same login items from the HUD header AUTOSTART pill. Or
build a clickable app and add it under System Settings, General, Login
Items:

```sh
bash scripts/macos/build-app.sh
```

Drag `dist/Mitra.app` to Applications. It is unsigned, so the first
launch needs a right click, Open approval. Server logs live at
`~/Library/Logs/mitra/server.log`.

Desktop shell developers: `cargo tauri dev` from `desktop/` boots the
API first through the same launcher.

## 17. API reference

Base URL is the configured host and port. All bodies are JSON.

| Method and path | Purpose |
|---|---|
| GET /health | Liveness plus provider name |
| POST /chat | `{project, message}` returns reply, actions, approval, evidence |
| GET /briefing | Launch greeting, recap, suggestions |
| GET /morning | Morning scan text plus open count |
| GET /connectors | Connector cards for the left rail |
| GET /voices | Voice persona presets |
| GET /memories | List or search project memories |
| GET /tasks | Project tasks with states and due dates |
| GET /approvals/pending | Open proposals, optional project filter |
| POST /approvals/{id}/decision | `{project, approve}` shared decide path |
| GET /audit | Recent audit entries |
| GET /audit/verify | Audit chain check |
| POST /stt | Server transcription for HUD voice input |
| GET /status | Provider, counts, tools, shell flag |
| GET, POST /autostart | Login item status and toggle |

## 18. Troubleshooting

Generic replies: you are on the mock provider. Install Ollama, pull a
model, set provider name to ollama, restart.

Model timeouts on first question: normal while the model loads. Raise
provider timeout seconds for big models.

Calendar or mail errors: grant Automation permission in System Settings
under Privacy, Automation, then retry.

GitHub private failures: export a fine grained MITRA_GITHUB_TOKEN
limited to the repos needed.

Port in use: change api port in config, restart, and reopen the HUD at
the new port.

Blank HUD panels: the API is down. Run the launcher, which starts it
and waits for health before opening anything.

Lost context after a restart: conversation turns persist in SQLite, so
the briefing recaps your last session on return.
