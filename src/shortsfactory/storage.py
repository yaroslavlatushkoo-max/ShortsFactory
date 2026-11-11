from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from .config import Config
from .models import Project, StageRecord, STATUS_INTERRUPTED, STATUS_RUNNING
from .util import atomic_write_json, now_iso, read_json


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    profile TEXT NOT NULL,
    languages_json TEXT NOT NULL,
    duration REAL NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    workspace TEXT NOT NULL,
    output TEXT NOT NULL,
    command TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    params_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS stages (
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, name),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'INFO',
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_created ON projects(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id, id);
"""


class Storage:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.path = config.database_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), timeout=60, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ projects
    def create_project(self, project: Project) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO projects
                (id, topic, profile, languages_json, duration, status, stage, workspace,
                 output, command, created_at, updated_at, error, params_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project.id, project.topic, project.profile,
                    json.dumps(project.languages, ensure_ascii=False),
                    project.duration, project.status, project.stage, project.workspace,
                    project.output, project.command, project.created_at, project.updated_at,
                    project.error, json.dumps(project.params, ensure_ascii=False),
                ),
            )
            self._conn.commit()
        self._write_project_file(project)

    def update_project(self, project: Project) -> None:
        project.updated_at = now_iso()
        with self._lock:
            self._conn.execute(
                """
                UPDATE projects SET topic=?, profile=?, languages_json=?, duration=?,
                    status=?, stage=?, workspace=?, output=?, command=?, updated_at=?,
                    error=?, params_json=? WHERE id=?
                """,
                (
                    project.topic, project.profile,
                    json.dumps(project.languages, ensure_ascii=False),
                    project.duration, project.status, project.stage, project.workspace,
                    project.output, project.command, project.updated_at, project.error,
                    json.dumps(project.params, ensure_ascii=False), project.id,
                ),
            )
            self._conn.commit()
        self._write_project_file(project)

    def set_status(self, project: Project, status: str, stage: str | None = None, error: str = "") -> None:
        project.status = status
        if stage is not None:
            project.stage = stage
        project.error = error
        self.update_project(project)

    def get_project(self, project_id: str) -> Project | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return self._row_to_project(row) if row else None

    def latest_project(self, statuses: Iterable[str] | None = None) -> Project | None:
        rows = self.list_projects(limit=1, statuses=statuses)
        return rows[0] if rows else None

    def list_projects(self, limit: int = 50, statuses: Iterable[str] | None = None) -> list[Project]:
        sql = "SELECT * FROM projects"
        params: list[Any] = []
        if statuses:
            values = list(statuses)
            sql += f" WHERE status IN ({','.join('?' for _ in values)})"
            params.extend(values)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_project(row) for row in rows]

    def recent_topics(self, limit: int = 30) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT topic FROM projects ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [row["topic"] for row in rows]

    def _row_to_project(self, row: sqlite3.Row) -> Project:
        return Project(
            id=row["id"], topic=row["topic"], profile=row["profile"],
            languages=json.loads(row["languages_json"] or "[]"),
            duration=float(row["duration"]), status=row["status"], stage=row["stage"],
            workspace=row["workspace"], output=row["output"], command=row["command"],
            created_at=row["created_at"], updated_at=row["updated_at"],
            error=row["error"] or "", params=json.loads(row["params_json"] or "{}"),
        )

    def _write_project_file(self, project: Project) -> None:
        workspace = Path(project.workspace)
        if workspace:
            workspace.mkdir(parents=True, exist_ok=True)
            atomic_write_json(workspace / "project.json", project.to_json())

    # -------------------------------------------------------------------- stages
    def start_stage(self, project: Project, stage: str) -> None:
        stamp = now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO stages(project_id, name, status, started_at, finished_at, detail)
                VALUES(?, ?, 'running', ?, '', '')
                ON CONFLICT(project_id, name) DO UPDATE SET
                    status='running', started_at=excluded.started_at,
                    finished_at='', detail=''
                """,
                (project.id, stage, stamp),
            )
            self._conn.commit()
        self.set_status(project, STATUS_RUNNING, stage, "")

    def finish_stage(self, project: Project, stage: str, detail: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE stages SET status='done', finished_at=?, detail=? WHERE project_id=? AND name=?",
                (now_iso(), detail, project.id, stage),
            )
            self._conn.commit()

    def fail_stage(self, project: Project, stage: str, detail: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE stages SET status='failed', finished_at=?, detail=? WHERE project_id=? AND name=?",
                (now_iso(), detail, project.id, stage),
            )
            self._conn.commit()

    def mark_stage(self, project: Project, stage: str, status: str, detail: str = "") -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO stages(project_id, name, status, started_at, finished_at, detail)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, name) DO UPDATE SET
                    status=excluded.status, finished_at=excluded.finished_at, detail=excluded.detail
                """,
                (project.id, stage, status, "", now_iso(), detail),
            )
            self._conn.commit()

    def reset_stages(self, project_id: str, stages: list[str]) -> None:
        """Reopen stages for a retry without touching successful earlier work."""
        if not stages:
            return
        placeholders = ",".join("?" for _ in stages)
        with self._lock:
            self._conn.execute(
                f"UPDATE stages SET status='pending', finished_at='', detail='' "
                f"WHERE project_id=? AND name IN ({placeholders})",
                (project_id, *stages),
            )
            self._conn.commit()

    def stage_status(self, project_id: str, stage: str) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM stages WHERE project_id=? AND name=?", (project_id, stage)
            ).fetchone()
        return row["status"] if row else "pending"

    def stage_records(self, project_id: str) -> list[StageRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM stages WHERE project_id=? ORDER BY rowid", (project_id,)
            ).fetchall()
        return [
            StageRecord(
                project_id=row["project_id"], name=row["name"], status=row["status"],
                started_at=row["started_at"], finished_at=row["finished_at"], detail=row["detail"],
            )
            for row in rows
        ]

    def recover_interrupted(self) -> int:
        """Mark projects/stages left running by a previous process as interrupted."""
        stamp = now_iso()
        count = 0
        with self._lock:
            rows = self._conn.execute("SELECT id FROM projects WHERE status=?", (STATUS_RUNNING,)).fetchall()
            for row in rows:
                self._conn.execute(
                    "UPDATE projects SET status=?, error=?, updated_at=? WHERE id=?",
                    (STATUS_INTERRUPTED, "Производство было прервано. Можно продолжить командой retry.", stamp, row["id"]),
                )
                self._conn.execute(
                    "UPDATE stages SET status='interrupted', finished_at=?, detail=? WHERE project_id=? AND status='running'",
                    (stamp, "Прервано завершением процесса", row["id"]),
                )
                count += 1
            self._conn.commit()
        return count

    def import_workspace_projects(self) -> int:
        """Recover project metadata from workspace/project.json if the DB was reset."""
        imported = 0
        if not self.config.workspace_dir.exists():
            return 0
        for path in sorted(self.config.workspace_dir.glob("*/project.json")):
            data = read_json(path)
            if not isinstance(data, dict) or not data.get("id"):
                continue
            if self.get_project(data["id"]):
                continue
            try:
                project = Project.from_dict(data)
            except (TypeError, ValueError):
                continue
            if project.status == STATUS_RUNNING:
                project.status = STATUS_INTERRUPTED
                project.error = "Восстановлено после перезапуска; производство можно продолжить."
            self.create_project(project)
            imported += 1
        return imported

    # -------------------------------------------------------------------- events
    def record_event(self, project_id: str, stage: str, level: str, message: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events(project_id, ts, stage, level, message) VALUES(?, ?, ?, ?, ?)",
                (project_id, now_iso(), stage, level, message[:4000]),
            )
            self._conn.commit()

    def events(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE project_id=? ORDER BY id DESC LIMIT ?", (project_id, limit)
            ).fetchall()
        return [dict(row) for row in rows]
