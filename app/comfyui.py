from __future__ import annotations

import asyncio
import json
import mimetypes
import uuid
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx


class ComfyUIError(RuntimeError):
    pass


class ComfyUIClient:
    def __init__(self, base_url: str, timeout_seconds: int = 7200):
        self.base_url = self.normalize_url(base_url)
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def normalize_url(value: str) -> str:
        value = value.strip().rstrip("/")
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("ComfyUI URL 必须是完整的 http/https 地址")
        return value

    async def check(self, required_nodes: Iterable[str] = ()) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20) as client:
            stats_response = await client.get(f"{self.base_url}/system_stats")
            stats_response.raise_for_status()
            system_stats = stats_response.json()
            try:
                nodes_response = await client.get(
                    f"{self.base_url}/object_info", timeout=60
                )
                nodes_response.raise_for_status()
            except httpx.TimeoutException:
                return {
                    "available": True,
                    "node_check_complete": False,
                    "missing_nodes": [],
                    "system_stats": system_stats,
                    "node_count": None,
                    "warning": "ComfyUI 已连接，但节点清单响应超过 60 秒，暂未完成节点兼容性检查。",
                }
        object_info = nodes_response.json()
        missing = sorted(set(required_nodes) - set(object_info))
        return {
            "available": not missing,
            "node_check_complete": True,
            "missing_nodes": missing,
            "system_stats": system_stats,
            "node_count": len(object_info),
            "warning": None,
        }

    async def upload(self, path: Path, remote_name: str) -> str:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        async with httpx.AsyncClient(timeout=180) as client:
            with path.open("rb") as stream:
                response = await client.post(
                    f"{self.base_url}/upload/image",
                    files={"image": (remote_name, stream, mime)},
                    data={"type": "input", "overwrite": "true"},
                )
                response.raise_for_status()
        result = response.json()
        name = result.get("name", remote_name)
        subfolder = result.get("subfolder", "")
        return f"{subfolder}/{name}".lstrip("/") if subfolder else name

    async def submit(self, workflow: dict[str, Any]) -> str:
        client_id = uuid.uuid4().hex
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/prompt", json={"prompt": workflow, "client_id": client_id}
            )
            if response.is_error:
                raise ComfyUIError(f"ComfyUI 拒绝工作流：{response.text}")
            queued = response.json()
        prompt_id = queued.get("prompt_id")
        if not prompt_id:
            raise ComfyUIError(f"ComfyUI 没有返回 prompt_id：{queued}")
        return str(prompt_id)

    async def run(self, workflow: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        prompt_id = await self.submit(workflow)
        history = await self.wait(prompt_id)
        return prompt_id, history

    async def cancel(self, prompt_id: str | None = None) -> None:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{self.base_url}/interrupt")
            response.raise_for_status()
            if prompt_id:
                response = await client.post(
                    f"{self.base_url}/queue", json={"delete": [prompt_id]}
                )
                response.raise_for_status()

    async def wait(self, prompt_id: str) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_seconds
        async with httpx.AsyncClient(timeout=30) as client:
            while loop.time() < deadline:
                response = await client.get(f"{self.base_url}/history/{prompt_id}")
                response.raise_for_status()
                payload = response.json()
                if prompt_id in payload:
                    history = payload[prompt_id]
                    status = history.get("status", {})
                    if status.get("status_str") == "error" or status.get("completed") is False:
                        messages = status.get("messages", [])
                        detail = self._format_execution_messages(messages)
                        cleanup_note = await self.release_memory_after_failure()
                        raise ComfyUIError(
                            f"ComfyUI 执行失败：\n{detail}\n\n{cleanup_note}"
                        )
                    if history.get("outputs") is not None:
                        return history
                await asyncio.sleep(2)
        raise TimeoutError(f"ComfyUI 任务超时：{prompt_id}")

    @staticmethod
    def _format_execution_messages(messages: list[Any]) -> str:
        return json.dumps(messages, ensure_ascii=False, indent=2, default=str)

    async def release_memory_after_failure(self) -> str:
        """Ask ComfyUI to unload models and clear its execution cache.

        This endpoint sets queue flags inside ComfyUI, so reclamation can happen
        shortly after the failed prompt has fully left the execution worker.
        Cleanup failure must never replace the original generation error.
        """
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    f"{self.base_url}/free",
                    json={"unload_models": True, "free_memory": True},
                )
                response.raise_for_status()
            return "ComfyUI 清理：已请求卸载模型并释放执行缓存。"
        except Exception as exc:
            return (
                "ComfyUI 清理请求失败："
                f"{type(exc).__name__}: {exc}"
            )

    @staticmethod
    def artifacts(history: dict[str, Any]) -> list[dict[str, str]]:
        found: list[dict[str, str]] = []
        for output in history.get("outputs", {}).values():
            if not isinstance(output, dict):
                continue
            for value in output.values():
                if not isinstance(value, list):
                    continue
                for item in value:
                    if isinstance(item, dict) and item.get("filename"):
                        found.append(
                            {
                                "filename": item["filename"],
                                "subfolder": item.get("subfolder", ""),
                                "type": item.get("type", "output"),
                            }
                        )
        return found

    async def download(self, artifact: dict[str, str], destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=600) as client:
            response = await client.get(f"{self.base_url}/view", params=artifact)
            response.raise_for_status()
            destination.write_bytes(response.content)
        return destination
