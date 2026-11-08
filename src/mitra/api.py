"""Local HTTP API for Mitra. Same core as the CLI, no extra logic.

Project profiles resolve per request, so each project gets its own provider,
scope, and shell settings over one shared database.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .autostart import set_enabled as autostart_set
from .autostart import status as autostart_status
from .config import ConfigError, MitraConfig, load_config, resolve_profile
from .orchestrator import Orchestrator, build_orchestrator
from .providers.base import ProviderError
from .speech import VOICE_PERSONAS, describe_persona
from .stt import ALLOWED_SUFFIXES, TranscribeError, build_engine

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


class SttOut(BaseModel):
    text: str
    engine: str = ""


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


class BriefingOut(BaseModel):
    greeting: str = ""
    recap: str = ""
    last_seen: str = ""
    tasks_open: int = 0
    pending_count: int = 0
    memory_count: int = 0
    suggestions: list[str] = []


class ConnectorOut(BaseModel):
    id: str
    label: str
    tools: list[str] = []
    enabled: bool = True
    hint: str = ""


class VoiceOut(BaseModel):
    id: str
    label: str
    voices: list[str] = []
    rate: int = 190
    pitch: float = 1.0
    line: str = ""


class AutostartOut(BaseModel):
    supported: bool = True
    enabled: bool = False
    server_plist: str = ""
    opener_plist: str = ""
    ok: bool = True
    message: str = ""


class AutostartIn(BaseModel):
    enabled: bool = True


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

    @app.post("/stt", response_model=SttOut)
    async def transcribe(request: Request, project: str = "default", suffix: str = "webm") -> SttOut:
        active = current_for(project)
        cleaned = "." + suffix.strip().lower().lstrip(".")
        if cleaned not in ALLOWED_SUFFIXES:
            raise HTTPException(status_code=400, detail="Unsupported audio suffix.")
        body = await request.body()
        cap = active.config.speech_to_text.max_mb * 1024 * 1024
        if len(body) > cap:
            raise HTTPException(status_code=413, detail="Audio is over the size limit.")
        if not body:
            raise HTTPException(status_code=400, detail="No audio received.")
        engine = build_engine(
            active.config.speech_to_text.engine, active.config.speech_to_text.model
        )
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=cleaned, delete=False) as handle:
                handle.write(body)
                tmp_path = Path(handle.name)
            try:
                text = engine.transcribe(tmp_path)
            except TranscribeError as exc:
                active.audit.log(
                    actor="user",
                    project=project.strip() or "default",
                    action="speech_to_text",
                    target=engine.name,
                    decision="error",
                    rationale=str(exc),
                )
                raise HTTPException(status_code=503, detail=str(exc))
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
        active.audit.log(
            actor="user",
            project=project.strip() or "default",
            action="speech_to_text",
            target=engine.name,
            decision="allowed",
            rationale="Server transcription.",
            result="%d chars" % len(text),
        )
        return SttOut(text=text, engine=engine.name)

    @app.get("/audit/verify")
    def audit_verify(project: str = "") -> dict:
        active = current_for(project or "default")
        ok, count_or_id = active.audit.verify_chain()
        if ok:
            return {"ok": True, "checked": count_or_id, "bad_id": 0}
        return {"ok": False, "checked": 0, "bad_id": count_or_id}

    @app.get("/briefing", response_model=BriefingOut)
    def briefing(project: str = "default") -> BriefingOut:
        active = current_for(project)
        data = active.briefing(project)
        return BriefingOut(
            greeting=str(data.get("greeting", "")),
            recap=str(data.get("recap", "")),
            last_seen=str(data.get("last_seen", "")),
            tasks_open=int(data.get("tasks_open", 0)),  # type: ignore[arg-type]
            pending_count=int(data.get("pending_count", 0)),  # type: ignore[arg-type]
            memory_count=int(data.get("memory_count", 0)),  # type: ignore[arg-type]
            suggestions=[str(item) for item in data.get("suggestions", [])],  # type: ignore[union-attr]
        )

    @app.get("/connectors", response_model=list[ConnectorOut])
    def connectors(project: str = "default") -> list[ConnectorOut]:
        active = current_for(project)
        return [
            ConnectorOut(
                id=str(card.get("id", "")),
                label=str(card.get("label", "")),
                tools=[str(name) for name in card.get("tools", [])],  # type: ignore[union-attr]
                enabled=bool(card.get("enabled", True)),
                hint=str(card.get("hint", "")),
            )
            for card in active.connectors(project)
        ]

    @app.get("/voices", response_model=list[VoiceOut])
    def voices() -> list[VoiceOut]:
        items = [describe_persona("auto")]
        for key in sorted(VOICE_PERSONAS):
            items.append(describe_persona(key))
        out: list[VoiceOut] = []
        for item in items:
            out.append(
                VoiceOut(
                    id=str(item.get("id", "auto")),
                    label=str(item.get("label", "")),
                    voices=[str(v) for v in item.get("voices", [])],  # type: ignore[union-attr]
                    rate=int(item.get("rate", 190)),  # type: ignore[arg-type]
                    pitch=float(item.get("pitch", 1.0)),  # type: ignore[arg-type]
                    line=str(item.get("line", "")),
                )
            )
        return out

    @app.get("/autostart", response_model=AutostartOut)
    def autostart_get() -> AutostartOut:
        data = autostart_status()
        return AutostartOut(
            supported=bool(data.get("supported", True)),
            enabled=bool(data.get("enabled", False)),
            server_plist=str(data.get("server_plist", "")),
            opener_plist=str(data.get("opener_plist", "")),
        )

    @app.post("/autostart", response_model=AutostartOut)
    def autostart_update(body: AutostartIn) -> AutostartOut:
        data = autostart_set(body.enabled)
        if not data.get("ok", False):
            raise HTTPException(status_code=500, detail=str(data.get("message", "")))
        return AutostartOut(
            supported=True,
            enabled=bool(data.get("enabled", body.enabled)),
            server_plist=str(data.get("server_plist", "")),
            opener_plist=str(data.get("opener_plist", "")),
            ok=True,
            message=str(data.get("message", "")),
        )

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
