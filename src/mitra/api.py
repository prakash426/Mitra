"""Local HTTP API for Mitra. Same core as the CLI, no extra logic.

Project profiles resolve per request, so each project gets its own provider,
scope, and shell settings over one shared database.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import ConfigError, MitraConfig, load_config, resolve_profile
from .orchestrator import Orchestrator, build_orchestrator
from .providers.base import ProviderError

WEB_DIR = Path(__file__).parent / "web"


class ChatIn(BaseModel):
    project: str = Field(default="default")
    message: str = Field(default="")


class EvidenceOut(BaseModel):
    text: str
    source: str
    updated: str


class ApprovalOut(BaseModel):
    proposal_id: str
    action: str
    target: str
    preview: str


class ChatOut(BaseModel):
    reply: str
    actions: list[str] = []
    approval: ApprovalOut | None = None
    evidence: list[EvidenceOut] = []


class MemoryOut(BaseModel):
    id: int
    project: str
    kind: str
    text: str
    source: str
    updated_at: str


class AuditOut(BaseModel):
    id: int
    created_at: str
    actor: str
    project: str
    action: str
    target: str
    decision: str
    rationale: str
    result: str


class ProposalOut(BaseModel):
    id: str
    action: str
    target: str
    project: str
    created_at: str


class DecisionIn(BaseModel):
    project: str = Field(default="default")
    approve: bool = Field(default=True)


class DecisionOut(BaseModel):
    ok: bool
    message: str


class TaskOut(BaseModel):
    id: int
    project: str
    title: str
    state: str
    updated_at: str


class StatusOut(BaseModel):
    status: str
    provider: str = ""
    model: str = ""
    project: str = ""
    profile: str = "default"
    memory_count: int = 0
    standing_count: int = 0
    tasks_open: int = 0
    pending_count: int = 0
    tools: list[str] = []
    shell_enabled: bool = False
    error: str = ""


def build_app(
    orch: Orchestrator | None = None, config_path: str | None = None
) -> FastAPI:
    """Create the API. Pass an orchestrator in tests, lazy load otherwise."""
    app = FastAPI(title="Mitra")
    state: dict = {"fixed": orch, "config_path": config_path, "base": None, "orchs": {}}

    def base_config() -> MitraConfig:
        if state["base"] is None:
            try:
                state["base"] = load_config(state["config_path"])
            except ConfigError as exc:
                raise HTTPException(status_code=503, detail=str(exc))
        return state["base"]

    def current_for(project: str) -> Orchestrator:
        if state["fixed"] is not None:
            return state["fixed"]
        cfg = base_config()
        key = project.strip() or "default"
        if key not in state["orchs"]:
            try:
                resolved = resolve_profile(cfg, key)
                state["orchs"][key] = build_orchestrator(resolved)
            except (ConfigError, ProviderError) as exc:
                raise HTTPException(status_code=503, detail=str(exc))
        return state["orchs"][key]

    def profile_name(project: str) -> str:
        if state["fixed"] is not None:
            cfg = state["fixed"].config
        else:
            cfg = base_config()
        key = project.strip() or "default"
        return key if key in cfg.profiles else "default"

    @app.get("/health")
    def health() -> dict:
        if state["fixed"] is not None:
            return {"status": "ok", "provider": state["fixed"].provider.name}
        try:
            cfg = base_config()
        except HTTPException as exc:
            return {"status": "degraded", "error": exc.detail}
        return {"status": "ok", "provider": cfg.provider.name}

    @app.post("/chat", response_model=ChatOut)
    def chat(body: ChatIn) -> ChatOut:
        active = current_for(body.project)
        response = active.handle_message(body.project, body.message)
        approval = None
        if response.approval_request:
            req = response.approval_request
            approval = ApprovalOut(
                proposal_id=req.proposal_id,
                action=req.action,
                target=req.target,
                preview=req.preview,
            )
        return ChatOut(
            reply=response.text,
            actions=response.actions,
            approval=approval,
            evidence=[
                EvidenceOut(text=e.text, source=e.source, updated=e.updated)
                for e in response.evidence
            ],
        )

    @app.get("/memories", response_model=list[MemoryOut])
    def memories(project: str, q: str = "", limit: int = 20) -> list[MemoryOut]:
        active = current_for(project)
        if q.strip():
            hits = active.memory.recall(project, q, limit=limit)
            records = [hit.record for hit in hits]
        else:
            records = active.memory.list_project(project, limit=limit)
        return [
            MemoryOut(
                id=r.id,
                project=r.project,
                kind=r.kind,
                text=r.text,
                source=r.source,
                updated_at=r.updated_at,
            )
            for r in records
        ]

    @app.get("/tasks", response_model=list[TaskOut])
    def tasks(project: str, state: str = "") -> list[TaskOut]:
        active = current_for(project)
        try:
            items = active.memory.task_list(project, state=state.strip())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return [
            TaskOut(
                id=t.id,
                project=t.project,
                title=t.title,
                state=t.state,
                updated_at=t.updated_at,
            )
            for t in items
        ]

    @app.get("/approvals/pending", response_model=list[ProposalOut])
    def pending(project: str = "") -> list[ProposalOut]:
        active = current_for(project or "default")
        items = active.gate.pending(project.strip())
        return [
            ProposalOut(
                id=p.id, action=p.action, target=p.target,
                project=p.project, created_at=p.created_at,
            )
            for p in items
        ]

    @app.post("/approvals/{proposal_id}/decision", response_model=DecisionOut)
    def decide(proposal_id: str, body: DecisionIn) -> DecisionOut:
        active = current_for(body.project)
        ok, message, _tag = active.decide(body.project, proposal_id, body.approve)
        return DecisionOut(ok=ok, message=message)

    @app.get("/audit", response_model=list[AuditOut])
    def audit(project: str = "", limit: int = 50) -> list[AuditOut]:
        active = current_for(project or "default")
        entries = active.audit.list_recent(project.strip() or None, limit=limit)
        return [
            AuditOut(
                id=e.id, created_at=e.created_at, actor=e.actor, project=e.project,
                action=e.action, target=e.target, decision=e.decision,
                rationale=e.rationale, result=e.result,
            )
            for e in entries
        ]

    @app.get("/status", response_model=StatusOut)
    def status(project: str = "default") -> StatusOut:
        try:
            active = current_for(project)
        except HTTPException as exc:
            return StatusOut(status="degraded", project=project, error=str(exc.detail))
        records = active.memory.list_project(project, limit=200)
        open_tasks = active.memory.task_list(project, state="open")
        doing_tasks = active.memory.task_list(project, state="doing")
        return StatusOut(
            status="ok",
            provider=active.provider.name,
            model=active.config.provider.model,
            project=project.strip() or "default",
            profile=profile_name(project),
            memory_count=len(records),
            standing_count=sum(1 for r in records if r.kind == "standing"),
            tasks_open=len(open_tasks) + len(doing_tasks),
            pending_count=len(active.gate.pending(project)),
            tools=[spec.name for spec in active.registry.describe()],
            shell_enabled=bool(active.policy.shell and active.policy.shell.enabled),
        )

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

    return app


app = build_app()


def main() -> int:
    """Run the API with host and port from config. Entry: python -m mitra.api."""
    import uvicorn

    try:
        config = load_config(None)
    except ConfigError as exc:
        print("Mitra API cannot start: %s" % exc)
        return 1
    print("Mitra API on http://%s:%d" % (config.api.host, config.api.port))
    print("Open the HUD at http://%s:%d in your browser." % (config.api.host, config.api.port))
    uvicorn.run(app, host=config.api.host, port=config.api.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
