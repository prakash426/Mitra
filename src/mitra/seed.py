"""Seed project memory from local knowledge files.

Reads markdown under knowledge/ plus, on request, the repo docs, splits
each file into headed sections, and stores one record per section. Reruns
are idempotent: texts already present in the project are skipped, so
seeding never duplicates records.

Usage:
    python -m mitra.seed -p Mitra
    python -m mitra.seed -p Mitra --docs
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .memory.store import MemoryStore

MAX_RECORDS = 200
MAX_SECTION_CHARS = 1500


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def split_sections(path: Path) -> list[tuple[str, str]]:
    """One (kind, text) pair per section. Standing files split per bullet."""
    raw = path.read_text(encoding="utf-8")
    if path.name == "standing-rules.md":
        records = []
        for line in raw.splitlines():
            cleaned = line.strip()
            if cleaned.startswith(("* ", "- ")):
                text = cleaned[2:].strip()
                if text:
                    records.append(("standing", text))
        return records
    title = ""
    first = re.search(r"(?m)^#\s+(.+)$", raw)
    if first:
        title = first.group(1).strip()
    parts = re.split(r"(?m)^##\s+(.+)$", raw)
    records = []
    for pos in range(1, len(parts), 2):
        heading = parts[pos].strip()
        body = parts[pos + 1].strip() if pos + 1 < len(parts) else ""
        body = re.sub(r"\s+", " ", body).strip()
        if not body:
            continue
        label = ("%s, %s" % (title, heading)) if title else heading
        text = "%s: %s" % (label, body[:MAX_SECTION_CHARS])
        records.append(("note", text))
    return records


def seed_project(
    store: MemoryStore,
    project: str,
    knowledge_dir: Path,
    docs_dir: Path | None = None,
    cap: int = MAX_RECORDS,
) -> dict[str, int]:
    """Import knowledge files into one project. Returns added/skipped."""
    key = project.strip() or "default"
    existing = {record.text for record in store.list_project(key, limit=2000)}
    files: list[Path] = []
    if knowledge_dir.is_dir():
        files.extend(sorted(knowledge_dir.glob("*.md")))
    if docs_dir is not None and docs_dir.is_dir():
        files.extend(sorted(docs_dir.glob("*.md")))
    added = 0
    skipped = 0
    for path in files:
        try:
            sections = split_sections(path)
        except OSError:
            continue
        for kind, text in sections:
            if added >= cap:
                return {"added": added, "skipped": skipped}
            if not text or text in existing:
                skipped += 1
                continue
            try:
                store.add(key, kind, text, source="seed:" + path.name)
            except ValueError:
                skipped += 1
                continue
            existing.add(text)
            added += 1
    return {"added": added, "skipped": skipped}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed Mitra project memory.")
    parser.add_argument("-p", dest="project", default="Mitra", help="project name")
    parser.add_argument("--db", dest="db", default="mitra.db", help="database path")
    parser.add_argument(
        "--docs", dest="docs", action="store_true", help="also seed docs/*.md"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = repo_root()
    store = MemoryStore(args.db)
    try:
        result = seed_project(
            store,
            args.project,
            root / "knowledge",
            root / "docs" if args.docs else None,
        )
    finally:
        store.close()
    print(
        "Seeded %d records into %s (%d already present)."
        % (result["added"], args.project, result["skipped"])
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
