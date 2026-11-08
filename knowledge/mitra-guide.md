# Mitra guide

## Memory
Save a fact with: remember that <fact>. One message can hold several facts; each becomes its own record. Ask back with: what did we decide about <topic>? List everything with: show me what is saved. Fix an old note with: correction: <fix>. The latest correction wins.

## Tasks
Track work with: todo: <item>. See all tasks with: tasks. Start one with: start <id>. Finish one with: done <id>. Task ids are global but each project sees only its own tasks.

## Standing instructions
Set a rule I apply in every chat here with: standing: <rule>. Example: standing: Prefer short answers. Standing rules surface automatically in conversation context.

## Due dates and standup
Set a deadline with: due <id> 2026-11-10. Remove it with: due <id> clear. The briefing warns about overdue and due today tasks, and nudges tasks open longer than a week. Say standup for done, now, and blocked in one view.

## Booking events
Book calendar time with: addevent Team lunch on 2026-11-10 at 12:30 for 45. Date is YYYY-MM-DD, time is HH:MM, minutes are optional and default to 30. Every event stages a proposal first; approve it before anything books.

## Saving pages and exporting notes
Save a page with: remember page <url>. I fetch it and store the head as a note citing the URL. Back everything up with: export notes. I stage a markdown file of memories plus tasks for approval.

## Watched repos
List repos under github watch_repos in config and the morning scan shows their open pull requests beside calendar, inbox, and tasks.

## Files
Read a file with: read <path>. List a directory with: list. Find names with: search <pattern>. Reads stay inside allowed directories only. If I ask which file, just reply with the path.

## Writes and approvals
Stage a write with: save <content> to <path>. Nothing writes before you approve. Review open proposals with: pending. Decide with: approve <id> or deny <id>. Update flows read the file first, then ask for exact new content.

## Shell
Stage a command with: run <command>. The shell stays disabled unless config enables it with a narrow allowlist. Every command needs a per command approval. Deny patterns block destructive shapes like recursive deletes.

## GitHub
Read a repo file with: github read <owner/repo> <path>. List pull requests with: github prs <owner/repo>. Stage a new PR with: github open pr <repo> from <head> to <base> titled <title>. Public reads need no token; private repos need MITRA_GITHUB_TOKEN.

## Calendar and mail
See what is ahead with: what is on my calendar in the next 7 days? Read recent subjects with: check my inbox. Both use macOS Calendar and Mail through Automation; the first run asks permission. The RUN MORNING SCAN button combines today, inbox, and open work in one read only pass.

## Plans and workflows
Describe multi step work with then, for example: list notes and then read the README. A reasoning provider turns it into steps; read steps run at once and the first mutation pauses for approval. Resume with: continue plan-N. Drop with: abandon plan-N. Save repeats with: save plan-N as <name> with <old> as <param>. Replay with: run workflow <name> <param>=<value>.

## Comparing projects and MCP
Compare one topic across projects with: compare <topic> across <A> and <B>. This needs explicit per query consent through an approval. Call integrations with: mcp <server> <tool> <json>. List server resources with: mcp resources <server>.

## Voice and launch
The HUD speaks every reply. Pick AUTO, JARVIS, or FRIDAY under the reactor. In the terminal, add -v to speak replies. Launch everything with: bash scripts/macos/mitra-launch.sh. Toggle login autostart from the HUD header or install Mitra.app into Login Items.

## Troubleshooting
If answers look generic, check the provider in config.toml; the mock provider proves wiring but cannot think. For real answers run Ollama locally and set provider name to ollama. If a model call fails, Mitra says so and shows the direct result instead of guessing.
