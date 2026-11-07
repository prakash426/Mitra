# Mitra Technical Decisions

Format: numbered records with context, decision, tradeoffs, and status.
Confirmed requirements from the brief are marked [REQUIRED]. Architect
recommendations are marked [RECOMMENDED].

## D1. Python monolith with clean module boundaries [RECOMMENDED]

Context: solo maintainer, macOS first, Linux later. The brief suggests Python
plus FastAPI plus SQLite.

Decision: one Python package, `mitra`, with strict module boundaries and no
microservices. FastAPI serves the local API. Stdlib carries as much weight as
possible (sqlite3, tomllib, dataclasses).

Tradeoffs: a single process cannot scale horizontally, which milestone one
does not need. We gain simple installs, easy debugging, and fast iteration.
If a component ever needs isolation (voice, device drivers), it becomes a
separate process talking to the stable API.

Status: accepted for milestone one.

## D2. Single transparent orchestrator, no agent framework [RECOMMENDED]

Context: the brief asks for a testable loop and warns against premature
multi agent complexity.

Decision: one rule based orchestration loop with typed step results. Model
driven planning is deferred until memory, tools, and approvals are proven.

Tradeoffs: less flashy autonomy today. Much higher debuggability,
deterministic tests, and a clear upgrade path: the planner becomes one
swappable step, not a rewrite.

Status: accepted for milestone one.

## D3. SQLite plus FTS5 for memory [REQUIRED with note]

Context: the brief starts with SQLite unless a better store is justified.

Decision: keep SQLite. Retrieval uses FTS5 full text search with project
filtering, which gives traceable ranked evidence with zero extra services.
Embeddings stay optional future work, not a day one dependency.

Tradeoffs: lexical search misses paraphrase matches that vectors would catch.
For explicit remembered facts with user wording, lexical plus recency plus
correction override is accurate and fully inspectable. A vector index can
layer on later without changing the record model.

Status: accepted for milestone one.

## D4. Provider interface with mock, Ollama, OpenAI compatible [REQUIRED]

Context: the brief requires model independence across Meta Model API, Ollama,
and OpenAI compatible endpoints.

Decision: `ModelProvider.generate(prompt, context)` is the whole contract.
Three adapters: deterministic mock, Ollama over local HTTP, and a generic
OpenAI compatible client (base URL plus key) that also serves Meta style
endpoints. Provider choice is config only.

Tradeoffs: the generic client cannot expose every vendor extension. That is
intentional: vendor specifics stay out of the core. Streaming is deferred;
first get correctness, timeouts, and error mapping right.

Status: accepted for milestone one.

## D5. Tools behind a registry with read only flags [REQUIRED]

Context: the brief wants registration without core changes, starting small.

Decision: `Tool` plus `ToolRegistry`. Each tool declares read only status.
Milestone one ships `read_file` and `propose_write_file` only. Writes never
execute without an approval token minted by the policy gate.

Tradeoffs: narrow capability today. Every future tool inherits policy and
audit for free, which is the point.

Status: accepted for milestone one.

## D6. Explicit approval gate with audit trail [REQUIRED]

Context: reading must not imply permission to modify. Destructive and
sensitive ops need consent.

Decision: policy classifies every candidate action as allowed, approval
required, or denied. Denials and approvals both enter the append only audit
log with timestamps, actor, and rationale.

Tradeoffs: extra round trips for the user on writes. That friction is the
product promise: Mitra never surprises you with a mutation.

Status: accepted for milestone one.

## D7. CLI plus API sharing one core [RECOMMENDED]

Context: the brief allows a simple chat UI first, desktop later.

Decision: ship a terminal chat and FastAPI endpoints over the same
orchestrator in milestone one. No web frontend yet; curl plus docs suffice
for API verification. Tauri stays a future shell around this API.

Tradeoffs: two thin shells to maintain, but zero duplicated logic and an
early stable API for the future desktop and voice layers.

Status: accepted for milestone one.

## D8. TOML config plus environment secrets [RECOMMENDED]

Context: the brief forbids embedded keys and wants example configuration.

Decision: `config.toml` (TOML, stdlib parsed) for non secret settings,
environment variables for keys, `.env.example` documenting names. Startup
validates config and reports precise errors instead of crashing obscurely.

Tradeoffs: one more file for users to copy. Clear errors plus an example
file keep setup under five minutes.

Status: accepted for milestone one.

## D9. Deferred items and why

* Vector memory: useful for paraphrase recall, but adds a dependency and
  opacity. Revisit when FTS5 demonstrably misses real queries.
  (Resolved in D20: shipped as an optional local layer in milestone four.)
* Shell execution tool: the highest risk capability. Ships only with strict
  allowlists, working directory confinement, and per command approval.
  (Shipped in D12, hardened with flag rules in D19.)
* Voice pipeline: architecturally separate by design. Starts after the text
  core is trusted. (Output shipped in D13 and D16; server input stays future.)
* Multi agent routing: no evidence the single loop is insufficient yet.

## D10. Persistent approvals with expiry and random tokens [REQUIRED]

Context: milestone one approvals lived in process memory, so restarts
dropped them, and tokens were predictable.

Decision: proposals persist in SQLite with created and expiry timestamps.
Lazy sweeps expire stale rows on every gate operation. Tokens are random
per approval and single use. Decided rows stay readable so retries report
"already decided" instead of vanishing.

Tradeoffs: the proposals table grows over time. A retention purge is future
work; the table is small and inspectable until then.

Status: accepted for milestone two.

## D11. Per project profiles [REQUIRED]

Context: one global config cannot express per project providers, scopes,
or shell policies.

Decision: `[profiles.NAME]` sections override provider, model, allowed
directories, and shell enablement. The CLI resolves at startup; the API
caches one orchestrator per project over a shared database. Unknown
projects fall back to base settings unchanged.

Tradeoffs: profiles that differ only slightly still duplicate a few lines.
Explicit beats clever here; a typo fails loudly at load time.

Status: accepted for milestone two.

## D12. Gated shell tool, disabled by default [REQUIRED]

Context: shell execution is the highest risk capability in the roadmap.

Decision: `run_shell` stages a proposal for every invocation. Policy
requires config enablement, a bare command allowlist, zero shell
metacharacters, and path arguments pinned inside the working directory.
Execution uses argv with no shell interpreter, resolves the binary through
PATH, and applies timeout plus output caps.

Tradeoffs: no pipes, redirects, or flags filtering yet. Power users will
feel the walls. The walls are the feature until flag level policy lands.

Status: accepted for milestone two.

## D13. Voice output in two layers [REQUIRED]

Context: voice output became mandatory in milestone two.

Decision: the HUD speaks through browser speech synthesis with a voice
picker, which needs no server round trip, no keys, and no new dependency.
The terminal speaks through a `speech` module with silent and macOS `say`
backends behind one protocol. Server side synthesis stays future work.

Tradeoffs: browser voices vary by machine, and the mic button depends on
browser speech recognition. Both are honest about availability and degrade
to text silently.

Status: accepted for milestone two.

## D14. HUD as local static files, Tauri deferred [RECOMMENDED]

Context: the founder asked for a running JARVIS style visual app.

Decision: serve a dependency free HUD (plain HTML, CSS, JS, no external
assets) directly from the FastAPI process. One command starts core plus
interface. A Tauri desktop shell remains an option later around the same
stable API.

Tradeoffs: a browser tab is not a native app with tray or global hotkeys.
What it buys is immediate delivery, zero install friction, and a UI the
whole team can already run.

Status: accepted for milestone two.

## D15. Open answer mode for reasoning providers [RECOMMENDED]

Context: every reply was an orchestrator draft that the model only
rephrased, so even capable models sounded basic on open questions.

Decision: providers declare `can_reason`. Open chat with a reasoning
provider sends the question plus standing instructions and memory context
straight to the model. Mock keeps deterministic drafts for tests. Memory
and tool flows stay evidence grounded on all providers, with draft
fallback when the model errors or returns empty. Both prompt blocks ban
greetings and stock openers so replies read as continuing conversation,
never announcements.

Tradeoffs: open answers can reflect model mistakes on general knowledge.
Mitra never presents them as remembered facts; stored facts always carry
cited sources.

Status: accepted for milestone three.

## D16. Natural voice through neural system voices [REQUIRED]

Context: the default compact voice sounded robotic.

Decision: rank installed voices toward neural tiers (Siri, Premium,
Enhanced, neural) and demote compact ones, with pace and tone sliders,
a test button, persisted choices, and speech shaped text (links spoken
as "link", pauses at breaks, markup stripped). Terminal speech gains a
rate setting. No new dependencies, no cloud, no keys.

Tradeoffs: top quality needs a voice download in System Settings and a
browser restart. Cloud studio voices would sound better still, at the
cost of keys, spend, and uploads; they stay a future option.

Status: accepted for milestone three.

## D17. macOS autostart through user LaunchAgents [REQUIRED]

Context: Mitra should greet the user first thing after login.

Decision: a server agent with KeepAlive plus a one shot opener agent
that waits for health and opens the HUD URL. Install and removal are one
script each, everything runs as the user with user permissions, logs go
to the standard log folder. Sleep is covered by persistence, not hooks.

Tradeoffs: the HUD opens in the default browser rather than a dedicated
window. A Tauri shell would own its window; that waits for demand.

Status: accepted for milestone three.

## D18. M3 hardening set [REQUIRED]

Context: fetch and shell are the two tools that reach outside the box.

Decision: fetch validates every redirect hop, blocks private and local
addresses always, and supports an optional host allowlist. Shell adds a
configurable deny pattern screen over the allowlist. Decided proposals
purge after a retention window, and the audit trail gains hash chaining
with a verify endpoint.

Tradeoffs: private address blocking also blocks fetching your own LAN
services through Mitra. That is intentional; allowlist your hosts if you
need them, once the private block learns exceptions.

Status: accepted for milestone three.

## D19. Per command flag rules plus fetch host exceptions [REQUIRED]

Context: D12 left flags to human review, and D18 blocked LAN fetches
entirely.

Decision: `[shell.flag_rules]` maps a command to its accepted flags;
anything unlisted is rejected before staging. Fetch gains
`fetch_allowed_hosts` plus `fetch_private_hosts`, so named LAN hosts
work while the private block stays default deny. Both ship with tests
locking the deny first behavior.

Tradeoffs: flag rules are configured per command, so unlisted commands
still lean on the argv preview at approval time. Host exceptions are a
small hole the user drills on purpose; every exception stays visible in
config and every fetch stays audited.

Status: accepted for milestone four.

## D20. Optional local vector recall beside FTS5 [RECOMMENDED]

Context: keyword search misses paraphrase queries ("how do we deploy?"
vs a note saying "release process").

Decision: an opt in embeddings mode (`embeddings = "ollama"`) embeds
memories through a local Ollama model, stores vectors in SQLite, and
merges cosine hits with FTS5 results. Default stays off. Any embedding
failure falls back to keywords silently, so recall never depends on the
vector path.

Tradeoffs: recall quality now depends partly on a model outside the
repo, and vectors add storage per record. Keeping FTS5 primary bounds
both risks: vectors can only add candidates, never remove keyword hits.

Status: accepted for milestone four.

## D21. GitHub connector with PR proposals [REQUIRED]

Context: the first external connector, touching other people's repos.

Decision: repo reads and PR lists run read only with no token for
public repos. Private access and PR posts use `MITRA_GITHUB_TOKEN`
from the environment only, and the token never touches proposals,
logs, or stored details. Opening a PR always stages an approval
proposal with repo, branches, and title in the preview.

Tradeoffs: no push, merge, or issue writes yet; each new write needs
its own proposal shape. API rate limits and outages surface as plain
tool errors.

Status: accepted for milestone four.

## D22. Read only macOS Calendar and Mail tools [REQUIRED]

Context: schedule and inbox reads are daily companion bread and butter,
but Calendar and Mail hold sensitive personal data.

Decision: two read only tools generate fixed AppleScript with no user
text interpolated except a calendar name and numeric bounds, executed
through `osascript`. First run consent comes from the macOS Automation
prompt, not from Mitra. No event creation, no mail sending, no deletions;
any writer later must stage proposals like every other mutation.

Tradeoffs: AppleScript output parsing is stringly typed, and macOS can
revoke Automation grants at any time. Both failure modes return guidance
instead of crashing.

Status: accepted for milestone four.

## D23. Approval gated memory import [REQUIRED]

Context: the brief demands a user controlled import mechanism and
forbids silent access to conversation history.

Decision: `import memories from <file>` reads a user named file inside
the allowed directories, parses Mitra JSON (`{"memories": [...]}` or a
bare list) or plain text (one note per line), and stages a single
approval proposal with a record preview. Approval writes the records
with an `import:` source tag; denial discards everything. Imported text
is untrusted data under the same boundary as tool output.

Tradeoffs: no ChatGPT export converter yet; users reshape exports into
the documented JSON first. Large files cap at `MAX_IMPORT_RECORDS` per
proposal to keep previews reviewable.

Status: accepted for milestone four.

## Open questions for the founder

1. Which Ollama model should be the documented default (size vs quality)?
2. Server side speech input next, or deeper connectors (MCP, calendar writers)?
3. Tauri shell next, or is the browser HUD enough for now?
4. (Answered in M4: yes, `fetch_private_hosts` ships. Which LAN hosts do
   you actually need?)
