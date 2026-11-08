"""API shape over the shared core. Uses the mock provider only."""

from __future__ import annotations

from fastapi.testclient import TestClient

from mitra.api import build_app


def test_health_chat_memories_audit(orch):
    client = TestClient(build_app(orch))
    assert client.get("/health").json()["status"] == "ok"

    chat = client.post(
        "/chat", json={"project": "P", "message": "Remember that P uses tabs."}
    )
    assert chat.status_code == 200
    assert "Noted" in chat.json()["reply"]

    recall = client.post(
        "/chat", json={"project": "P", "message": "What did we decide about tabs?"}
    )
    assert "tabs" in recall.json()["reply"]
    assert recall.json()["evidence"][0]["source"] == "user"

    memories = client.get("/memories", params={"project": "P"})
    assert len(memories.json()) == 1

    audit = client.get("/audit", params={"project": "P"})
    assert any(e["action"] == "memory_add" for e in audit.json())


def test_chat_write_flow_through_api(orch, workdir):
    client = TestClient(build_app(orch))
    staged = client.post(
        "/chat", json={"project": "P", "message": "Save api data to api.txt"}
    )
    body = staged.json()
    assert body["approval"] is not None
    pid = body["approval"]["proposal_id"]
    assert not (workdir / "api.txt").exists()

    pending = client.get("/approvals/pending", params={"project": "P"})
    assert any(p["id"] == pid for p in pending.json())

    done = client.post("/chat", json={"project": "P", "message": "approve " + pid})
    assert "wrote" in done.json()["reply"].lower()
    assert (workdir / "api.txt").read_text(encoding="utf-8") == "api data"


def test_degraded_health_without_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = TestClient(build_app(None, config_path=str(tmp_path / "missing.toml")))
    body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert "Config file" in body["error"]
    chat = client.post("/chat", json={"project": "P", "message": "hi"})
    assert chat.status_code == 503


def test_status_endpoint(orch):
    client = TestClient(build_app(orch))
    orch.handle_message("P", "Remember that P uses tabs.")
    orch.handle_message("P", "todo: One thing.")
    orch.handle_message("P", "standing: Cite sources.")
    body = client.get("/status", params={"project": "P"}).json()
    assert body["status"] == "ok" and body["provider"] == "mock"
    assert body["memory_count"] == 2 and body["standing_count"] == 1
    assert body["tasks_open"] == 1 and body["pending_count"] == 0
    assert "run_shell" in body["tools"]
    assert body["shell_enabled"] is False


def test_tasks_endpoint(orch):
    client = TestClient(build_app(orch))
    orch.handle_message("P", "todo: Via chat.")
    body = client.get("/tasks", params={"project": "P"}).json()
    assert len(body) == 1 and body[0]["title"] == "Via chat."
    assert client.get("/tasks", params={"project": "P", "state": "done"}).json() == []
    bad = client.get("/tasks", params={"project": "P", "state": "bogus"})
    assert bad.status_code == 400


def test_decision_endpoint(orch, workdir):
    client = TestClient(build_app(orch))
    staged = client.post(
        "/chat", json={"project": "P", "message": "Save api decide to decide2.txt"}
    )
    pid = staged.json()["approval"]["proposal_id"]
    decided = client.post(
        f"/approvals/{pid}/decision", json={"project": "P", "approve": True}
    )
    assert decided.json()["ok"] is True
    assert (workdir / "decide2.txt").exists()
    again = client.post(
        f"/approvals/{pid}/decision", json={"project": "P", "approve": False}
    )
    assert again.json()["ok"] is False


def test_per_project_profiles(tmp_path):
    alpha = tmp_path / "alpha"
    alpha.mkdir()
    (alpha / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "beta").mkdir()
    config_text = """
[provider]
name = "mock"
[memory]
db_path = "m.db"
[tools]
allowed_dirs = ["."]
[profiles.Alpha]
allowed_dirs = ["alpha"]
[profiles.Beta]
allowed_dirs = ["beta"]
"""
    cfg = tmp_path / "config.toml"
    cfg.write_text(config_text, encoding="utf-8")
    client = TestClient(build_app(None, config_path=str(cfg)))
    ok = client.post("/chat", json={"project": "Alpha", "message": "Read a.txt"})
    assert "a.txt" in ok.json()["reply"]
    denied = client.post("/chat", json={"project": "Beta", "message": "Read a.txt"})
    assert "could not read" in denied.json()["reply"]
    status = client.get("/status", params={"project": "Alpha"}).json()
    assert status["profile"] == "Alpha"


def test_audit_verify_endpoint(orch):
    client = TestClient(build_app(orch))
    orch.handle_message("P", "Remember that P uses tabs.")
    body = client.get("/audit/verify", params={"project": "P"}).json()
    assert body["ok"] is True
    assert body["checked"] >= 1
    assert body["bad_id"] == 0


def test_web_hud_served(orch):
    client = TestClient(build_app(orch))
    page = client.get("/")
    assert page.status_code == 200
    assert "MITRA" in page.text
    assert "reactor" in page.text
    assert "transcript" in page.text
    js = client.get("/app.js")
    assert js.status_code == 200
    assert "speechSynthesis" in js.text
    css = client.get("/styles.css")
    assert css.status_code == 200
    assert "reactor-wrap" in css.text


def test_briefing_connectors_voices(orch):
    client = TestClient(build_app(orch))
    orch.handle_message("P", "Remember that P uses tabs.")
    orch.handle_message("P", "todo: Ship it.")
    briefing = client.get("/briefing", params={"project": "P"}).json()
    assert "tabs" in briefing["recap"] or "Ship" in briefing["recap"]
    assert briefing["tasks_open"] == 1
    assert briefing["suggestions"]
    connectors = client.get("/connectors", params={"project": "P"}).json()
    ids = {card["id"] for card in connectors}
    assert {"files", "github", "macos"} <= ids
    voices = client.get("/voices").json()
    assert {item["id"] for item in voices} == {"auto", "friday", "jarvis"}


def test_autostart_status_endpoint(orch, tmp_path, monkeypatch):
    monkeypatch.setenv("MITRA_AGENTS_DIR", str(tmp_path))
    client = TestClient(build_app(orch))
    body = client.get("/autostart").json()
    assert body["supported"] is True
    assert body["enabled"] is False


def test_morning_endpoint(orch, monkeypatch):
    monkeypatch.setattr("mitra.tools.macos_tools.shutil.which", lambda n: None)
    client = TestClient(build_app(orch))
    orch.handle_message("P", "todo: Ship it.")
    body = client.get("/morning", params={"project": "P"}).json()
    assert "Ship it" in body["text"]
    assert body["tasks_open"] == 1
