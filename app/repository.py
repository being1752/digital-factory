from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectRepository:
    def __init__(self, database: Path):
        database.parent.mkdir(parents=True, exist_ok=True)
        self.database = database
        self.lock = threading.RLock()
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS production_tasks (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    priority INTEGER NOT NULL DEFAULT 0,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    error TEXT,
                    payload TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_production_tasks_queue
                ON production_tasks(status, priority DESC, created_at ASC)
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, project: dict[str, Any]) -> dict[str, Any]:
        timestamp = now_iso()
        project = {**project, "created_at": timestamp, "updated_at": timestamp}
        with self.lock, self._connect() as db:
            db.execute(
                "INSERT INTO projects(id, created_at, updated_at, payload) VALUES (?, ?, ?, ?)",
                (project["id"], timestamp, timestamp, json.dumps(project, ensure_ascii=False)),
            )
        return project

    def get(self, project_id: str) -> dict[str, Any] | None:
        with self.lock, self._connect() as db:
            row = db.execute("SELECT payload FROM projects WHERE id = ?", (project_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.lock, self._connect() as db:
            rows = db.execute(
                "SELECT payload FROM projects ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def update(self, project_id: str, **changes: Any) -> dict[str, Any]:
        with self.lock:
            project = self.get(project_id)
            if not project:
                raise KeyError(project_id)
            project.update(changes)
            project["updated_at"] = now_iso()
            with self._connect() as db:
                db.execute(
                    "UPDATE projects SET updated_at = ?, payload = ? WHERE id = ?",
                    (project["updated_at"], json.dumps(project, ensure_ascii=False), project_id),
                )
        return project

    def delete(self, project_id: str) -> bool:
        with self.lock, self._connect() as db:
            cursor = db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return cursor.rowcount > 0

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> dict[str, Any]:
        task = json.loads(row["payload"])
        for key in (
            "id",
            "project_id",
            "status",
            "stage",
            "progress",
            "priority",
            "attempt",
            "created_at",
            "updated_at",
            "started_at",
            "completed_at",
        ):
            task[key] = row[key]
        task["error"] = json.loads(row["error"]) if row["error"] else None
        return task

    def enqueue_task(
        self, project_id: str, snapshot: dict[str, Any], priority: int = 0
    ) -> dict[str, Any]:
        timestamp = now_iso()
        task_id = uuid.uuid4().hex[:12]
        payload = {
            "id": task_id,
            "project_id": project_id,
            "project_title": snapshot.get("title", project_id),
            "comfy_url": snapshot.get("comfy_url", ""),
            "snapshot_updated_at": snapshot.get("updated_at"),
            "snapshot": {
                key: snapshot.get(key)
                for key in (
                    "title",
                    "comfy_url",
                    "original_script",
                    "purpose",
                    "audience",
                    "requested_style",
                    "tts_engine",
                    "auto_run",
                    "image_path",
                    "voice_path",
                    "emotion_voice_path",
                )
            },
        }
        with self.lock, self._connect() as db:
            existing = db.execute(
                """
                SELECT id FROM production_tasks
                WHERE project_id = ? AND status IN ('QUEUED', 'RUNNING')
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
            if existing:
                raise ValueError("该项目已经在队列中或正在执行")
            db.execute(
                """
                INSERT INTO production_tasks(
                    id, project_id, status, stage, progress, priority, attempt,
                    created_at, updated_at, payload
                ) VALUES (?, ?, 'QUEUED', 'WAITING', 0, ?, 0, ?, ?, ?)
                """,
                (
                    task_id,
                    project_id,
                    int(priority),
                    timestamp,
                    timestamp,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
        return self.get_task(task_id) or payload

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM production_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return self._task_from_row(row) if row else None

    def list_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock, self._connect() as db:
            rows = db.execute(
                """
                SELECT * FROM production_tasks
                ORDER BY
                    CASE status
                        WHEN 'RUNNING' THEN 0
                        WHEN 'QUEUED' THEN 1
                        WHEN 'FAILED' THEN 2
                        WHEN 'COMPLETED' THEN 3
                        ELSE 4
                    END,
                    priority DESC,
                    created_at ASC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def claim_next_task(self) -> dict[str, Any] | None:
        timestamp = now_iso()
        with self.lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT id FROM production_tasks
                WHERE status = 'QUEUED'
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            cursor = db.execute(
                """
                UPDATE production_tasks
                SET status = 'RUNNING', stage = 'STARTING', progress = 1,
                    started_at = COALESCE(started_at, ?), updated_at = ?,
                    attempt = attempt + 1, error = NULL
                WHERE id = ? AND status = 'QUEUED'
                """,
                (timestamp, timestamp, row["id"]),
            )
            if cursor.rowcount != 1:
                return None
        return self.get_task(row["id"])

    def update_task(self, task_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {
            "status",
            "stage",
            "progress",
            "priority",
            "started_at",
            "completed_at",
            "error",
        }
        values = {key: value for key, value in changes.items() if key in allowed}
        values["updated_at"] = now_iso()
        if "error" in values:
            values["error"] = (
                json.dumps(values["error"], ensure_ascii=False)
                if values["error"] is not None
                else None
            )
        assignments = ", ".join(f"{key} = ?" for key in values)
        parameters = [*values.values(), task_id]
        with self.lock, self._connect() as db:
            cursor = db.execute(
                f"UPDATE production_tasks SET {assignments} WHERE id = ?", parameters
            )
            if cursor.rowcount != 1:
                raise KeyError(task_id)
        task = self.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        return task

    def recover_running_tasks(self) -> int:
        timestamp = now_iso()
        with self.lock, self._connect() as db:
            cursor = db.execute(
                """
                UPDATE production_tasks
                SET status = 'QUEUED', stage = 'RECOVERING',
                    updated_at = ?, error = NULL
                WHERE status = 'RUNNING'
                """,
                (timestamp,),
            )
        return cursor.rowcount

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        cancellable = task["status"] == "QUEUED" or (
            task["status"] == "RUNNING"
            and task["stage"] in {"ANALYZING", "ANALYSIS_RETRYING"}
        )
        if not cancellable:
            raise ValueError("只能取消等待中的任务或正在重试的 AI 导演任务")
        return self.update_task(
            task_id,
            status="CANCELLED",
            stage="CANCELLED",
            progress=0,
            completed_at=now_iso(),
        )

    def retry_task(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        if task["status"] not in {"FAILED", "CANCELLED"}:
            raise ValueError("只有失败或已取消的任务可以重新排队")
        return self.update_task(
            task_id,
            status="QUEUED",
            stage="WAITING",
            progress=0,
            completed_at=None,
            error=None,
        )

    def delete_task(self, task_id: str) -> dict[str, Any]:
        with self.lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM production_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not row:
                raise KeyError(task_id)
            task = self._task_from_row(row)
            if task["status"] not in {"FAILED", "CANCELLED", "COMPLETED"}:
                raise ValueError("只能删除失败、已取消或已完成的任务记录")
            cursor = db.execute(
                """
                DELETE FROM production_tasks
                WHERE id = ? AND status IN ('FAILED', 'CANCELLED', 'COMPLETED')
                """,
                (task_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("任务状态已变化，请刷新后重试")
        return task

    def active_tasks_for_project(self, project_id: str) -> list[dict[str, Any]]:
        with self.lock, self._connect() as db:
            rows = db.execute(
                """
                SELECT * FROM production_tasks
                WHERE project_id = ? AND status IN ('QUEUED', 'RUNNING')
                """,
                (project_id,),
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def delete_tasks_for_project(self, project_id: str) -> int:
        with self.lock, self._connect() as db:
            cursor = db.execute(
                "DELETE FROM production_tasks WHERE project_id = ?", (project_id,)
            )
        return cursor.rowcount
