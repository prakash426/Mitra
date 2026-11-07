"""Shared fixtures. No network, no keys, everything under tmp_path."""

from __future__ import annotations

import pytest

from mitra.orchestrator import Orchestrator, build_test_orchestrator

SAMPLE_README = """# Sample Project

This project keeps a local first workflow.

## Notes

Keep it simple and safe.
"""


@pytest.fixture()
def workdir(tmp_path):
    (tmp_path / "README.md").write_text(SAMPLE_README, encoding="utf-8")
    (tmp_path / "notes").mkdir(exist_ok=True)
    return tmp_path


@pytest.fixture()
def orch(workdir) -> Orchestrator:
    instance = build_test_orchestrator(workdir)
    yield instance
    instance.close()
