# Mitra Security

Security is architecture here, not a later patch. This file states the model,
the enforced rules, and the known gaps through milestone four.

## Trust model

* Trusted: the user, the local config file, explicit approval decisions.
* Semi trusted: the configured model provider (sees only retrieved evidence,
  never the full store).
* Untrusted: file contents, tool outputs, retrieved documents, pasted text,
  connector content (GitHub files, mail subjects, calendar titles), import
  files. Untrusted content is data. It can inform an answer. It can never
  issue instructions to the orchestrator.

## Enforced rules

1. Least privilege tools. Each tool declares read only status. New tools
   default to denied until policy explicitly allows them.
2. Directory confinement. File tools resolve symlinks and absolute paths,
   then require the result to sit inside a configured allowed directory.
   Escapes fail closed with an audit entry.
3. Read is not write. `read_file` can never mutate. Writes go through
   `propose_write_file`, which only stages a proposal.
4. Explicit approvals. Staged writes and shell commands need a user
   approval decision before execution. Denial blocks execution and is
   logged. Proposals persist across restarts and expire on a timer.
5. Append only audit. Proposals, executions, denials, approvals, memory
   mutations, task changes, fetches, and shell runs are recorded with
   timestamp, actor, project, and rationale.
6. Secret hygiene. Keys load from environment only. Config files hold no
   secrets. Logs redact known secret patterns and never print raw keys.
7. Graceful failure. Missing config, unreachable models, and tool errors
   produce clear messages, never stack traces to the chat surface, never
   partial writes.
8. Gated shell. Disabled by default. When enabled: bare command
   allowlist, optional per command flag rules that reject unlisted flags,
   no shell metacharacters, no absolute or parent path arguments, a deny
   pattern screen for destructive shapes, argv execution with no shell
   interpreter, binary resolved through PATH, working directory pinned,
   timeout plus output caps, and per command approval. The injection test
   pack locks this behavior.
9. Fetch guards. http and https only, 15 second timeout, 200 KB body cap,
   non text content skipped, every redirect hop revalidated, private and
   local addresses blocked unless named in `fetch_private_hosts`,
   optional `fetch_allowed_hosts` allowlist, every fetch audited.
10. Tamper evident audit. Each entry hashes the previous hash plus its
    fields. `/audit/verify` recomputes the chain. Older databases migrate
    and backfill on open.
11. Connector scope. GitHub reads need no token for public repos; private
    reads and PR posts need `MITRA_GITHUB_TOKEN` from the environment.
    The token never touches proposals, logs, or stored details. Opening
    a PR always stages an approval proposal first. Calendar and Mail
    tools are read only, run `osascript` with fixed generated scripts
    only, and rely on the macOS Automation prompt for first run consent.
12. Import boundary. Memory import reads a user named file inside the
    allowed directories, parses JSON or plain text, and stages every
    record as one approval proposal with a preview. Nothing imports
    before approval. Imported records carry an `import:` source tag and
    imported text stays untrusted data: it can be recalled, never obeyed.
13. Local embeddings. Vector recall is off by default and runs only
    against a configured local Ollama host. Vectors sit in SQLite beside
    the store they index. Embedding failures fall back to keyword recall
    silently, and disabling the feature needs no data migration.
14. Plan checkpoints. Plans run read only steps directly and pause at
    the first staged mutation. Resume rechecks the staged proposal:
    pending repauses without restaging, denied or expired abandons the
    plan. Plans are project scoped; a plan ID from another project
    refuses to resume.
15. Cross project consent. Comparisons across projects always stage a
    `cross_project` proposal naming the topic and projects. Reads happen
    only after approval, pull at most five hits per project, and stay
    cited per project in the reply.
16. MCP trust tiers. Servers spawn per call over stdio with timeouts and
    output caps. Trust `read` runs directly; everything else stages a
    proposal per call. Server commands come from local config only, never
    from chat or tool output. Unreachable servers skip at startup.
17. Voice input privacy. `POST /stt` accepts raw audio bytes with a type
    allowlist and size cap, transcribes locally, deletes the temp file,
    and audits the call. The stub engine never processes audio content;
    faster-whisper downloads its model on first use, which is the only
    network call in the voice path.

## Approval flow

```
request -> policy check -> allowed | approval required | denied
approval required -> proposal staged -> user approves or denies
approved -> execute inside scope -> audit result
denied -> block -> audit denial
```

Approval tokens are random, single use, and bound to one proposal.
Pending proposals expire after the configured TTL and vanish from queues.

## Audit contents

Each entry: timestamp (UTC), actor (user or system), project, action type,
target, decision, rationale, and result. Stored in SQLite alongside memory
but in a separate table with no update or delete API; decided proposals
purge after the retention window.

## Web interface notes

* The HUD is static files with no secrets and no decision logic. All
  approvals execute server side through the shared decide path.
* Dynamic content renders through textContent only. No innerHTML anywhere,
  so stored memory text cannot inject markup.
* The mic button uses browser speech recognition where available and
  degrades to typing otherwise. No audio leaves the machine except through
  the browser's own recognition service when the user presses the mic.

## Autostart notes

* The installer writes two plists to the user's LaunchAgents folder and
  loads them for the current user only. Nothing runs as root.
* Review `scripts/macos/install.sh` before running it; removal is the
  matching uninstall script.

## Known gaps and limits

* The API binds to localhost by default; binding wider without auth is
  unsupported and undocumented on purpose.
* Flag rules cover only commands you list; unlisted commands still rely
  on per command human review of their full argv preview.
* No per project encryption at rest yet. OS level disk encryption is the
  current answer.
* The GitHub token scope is your choice; prefer a fine grained token
  limited to the repos Mitra needs.
* Calendar and Mail access needs the macOS Automation grant on first run;
  denying it fails those tools closed with guidance.
* The Tauri desktop app compiles and boots but is unsigned and
  unbundled; its window loads the localhost API and HUD only.
* MCP `read` trust runs third party code output directly; grant it only
  to servers you wrote or reviewed.
* Workflow parameters are plain data substitution, never evaluated.
  Workflows inherit plan checkpoints and approvals on every run.
* No wake word listening exists; the mic records only while you hold it.

## Reporting and handling

This is a young personal project. Until a policy file exists, report
suspected issues directly to the maintainer with steps to reproduce and
expected vs actual behavior. Do not open public issues with exploit details.
