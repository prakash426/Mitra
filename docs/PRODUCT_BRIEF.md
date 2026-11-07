# Mitra Product Brief

## What Mitra is

Mitra is a modular, local first AI companion and intelligent operating layer.
It talks with you naturally, remembers what matters per project, reasons over
retrieved context only, and takes actions through tools that stay inside
explicit permission scopes.

Mitra is not a chatbot skin over one model. The model is replaceable. Memory,
tools, approvals, and audit are first class and live under your control.

## Who it serves

Individual professionals and technical users: developers, Salesforce
practitioners, researchers, managers, owners, and general productivity users.
The core stays domain agnostic. Industry depth arrives later through optional
skills and connectors, never baked into the core.

## Guiding principle

Mitra grows more capable without growing more opaque or unsafe. Every automated
action must be understandable, scoped, approved where it matters, and logged.

## Product objectives

1. Natural, personal conversation across text first, voice later.
2. Persistent, structured, inspectable memory with sources and timestamps.
3. Model independence through a provider abstraction.
4. Safe action execution with approvals and audit.
5. Extensible tools, skills, and connectors without core rewrites.
6. Local first data ownership and privacy.
7. Open source structure others can install and extend.

## First milestone scope

A minimal vertical slice that proves the architecture end to end:

1. Start Mitra locally.
2. Chat through a basic interface.
3. Route the request to a configured model adapter.
4. Store and retrieve explicit project scoped memory.
5. Retrieve relevant memory with source info when needed.
6. Discover and invoke a safe allowlisted read only file tool.
7. Ask for approval before any permitted file write.
8. Log actions and approval decisions in an audit trail.
9. Answer in clear conversational form.
10. Fail gracefully on missing config, down models, and tool errors.

Deterministic mocks cover tests with no credentials required.

## Demonstration scenarios

A. Memory: remember a CancerLens workflow fact, recall it later with no invention.
B. Project context: show saved records for a project with sources.
C. Tool execution: read a README inside the allowed directory and summarize it.
D. Protected action: proposing a README update must wait for explicit approval.
E. Model independence: swap provider by config change, zero orchestration edits.

## Explicit non goals for milestone one

No voice pipeline, no desktop app, no cloud accounts, no multi agent teams,
no calendar or email connectors, no vector database service, no Kubernetes,
no production deployment story. Each has a roadmap slot when the core is solid.

## Second milestone scope (shipped)

Voice output is mandatory from here on. Milestone two added: persistent
approvals with expiry, tasks with state, standing instructions, discovery
tools, a gated shell tool, per project profiles, an adversarial injection
test pack, and a JARVIS style visual HUD with spoken replies. Full duplex
voice with wake word, connectors, and a desktop shell remain future work.

## Third milestone scope (shipped)

Real models answer open questions directly with memory context instead
of sounding like the mock. Voice output turns natural through neural
system voices with pace and tone controls. Mitra autostarts at macOS
login and opens the HUD first thing. Fetch and shell harden with SSRF
guards, deny patterns, proposal retention, and a verifiable audit chain.

## Fourth milestone scope (shipped)

Depth and reach. Shell policy learns per command flag rules and fetch
learns host exceptions. Vector recall layers beside keyword search as
an opt in local feature. The first connectors land: GitHub reads plus
PR proposals, read only macOS calendar and inbox, and an approval
gated memory import path for user supplied JSON or text. A desktop
shell, server side speech input, and MCP stay future work.
