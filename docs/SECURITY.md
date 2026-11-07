# Mitra Security

Security is architecture here, not a later patch. This file states the model,
the enforced rules, and the known gaps for milestone one.

## Trust model

* Trusted: the user, the local config file, explicit approval decisions.
* Semi trusted: the configured model provider (sees only retrieved evidence,
  never the full store).
* Untrusted: file contents, tool outputs, retrieved documents, pasted text.
  Untrusted content is data. It can inform an answer. It can never issue
  instructions to the orchestrator.

## Enforced rules in milestone one

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
   allowlist, no shell metacharacters, no absolute or parent path
   arguments, argv execution with no shell interpreter, binary resolved
   through PATH, working directory pinned, timeout plus output caps, and
   per command approval. The injection test pack locks this behavior.
9. Fetch caps. http and https only, 15 second timeout, 200 KB body cap,
   non text content skipped, every fetch audited.

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
but in a separate table with no update or delete API in milestone one.

## Web interface notes

* The HUD is static files with no secrets and no decision logic. All
  approvals execute server side through the shared decide path.
* Dynamic content renders through textContent only. No innerHTML anywhere,
  so stored memory text cannot inject markup.
* The mic button uses browser speech recognition where available and
  degrades to typing otherwise. No audio leaves the machine except through
  the browser's own recognition service when the user presses the mic.

## Known gaps and limits

* Audit table has no cryptographic chaining yet, so local tampering is
  detectable only by inspection. Consider hash chaining later.
* The API binds to localhost by default; binding wider without auth is
  unsupported and undocumented on purpose.
* Fetch has no private IP block or host allowlist yet. Treat fetched
  content as untrusted data, which the orchestrator already does.
* Shell policy is command level, not flag level. Risky flags on allowed
  commands rely on per command human review until flag filtering lands.
* Proposals have no retention purge yet; decided rows accumulate slowly.
* No per project encryption at rest yet. OS level disk encryption is the
  current answer.

## Reporting and handling

This is a young personal project. Until a policy file exists, report
suspected issues directly to the maintainer with steps to reproduce and
expected vs actual behavior. Do not open public issues with exploit details.
