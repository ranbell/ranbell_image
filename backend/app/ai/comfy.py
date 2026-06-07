import asyncio
import copy
import json
import logging
import uuid
from pathlib import Path
from typing import AsyncGenerator

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


class ComfyUIClient:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=60.0)
        self.client_id = str(uuid.uuid4())

    async def is_available(self) -> bool:
        try:
            r = await self._http.get(f"{settings.comfyui_url}/system_stats", timeout=3.0)
            return r.status_code == 200
        except Exception:
            return False

    def list_workflows(self) -> list[str]:
        wf_dir = Path(settings.comfyui_workflows_dir)
        if not wf_dir.exists():
            return []
        return sorted(p.name for p in wf_dir.glob("*.json"))

    def load_workflow(self, name: str) -> dict:
        wf_dir = Path(settings.comfyui_workflows_dir)
        path = (wf_dir / name).resolve()
        if not path.is_relative_to(wf_dir.resolve()):
            raise ValueError("Invalid workflow path")
        if not path.exists():
            raise FileNotFoundError(f"Workflow not found: {name}")
        return json.loads(path.read_text())

    _LATENT_NODE_TYPES = {
        "EmptyLatentImage",
        "EmptySD3LatentImage",
        "EmptyLatentImageLarge",
        "EmptyHunyuanLatentVideo",
        "EmptyMochiLatentVideo",
        "EmptyLTXVLatentVideo",
        "EmptyCogVideoLatentVideo",
    }

    _KSAMPLER_TYPES = {
        "KSampler", "KSamplerAdvanced", "KSamplerSelect", "KSamplerCustom",
        "KSamplerCustomAdvanced",
    }
    _CLIP_ENCODE_TYPES = {
        "CLIPTextEncode", "CLIPTextEncodeSDXL", "CLIPTextEncodeSDXLRefiner",
        "BNK_CLIPTextEncodeAdvanced", "smZ CLIPTextEncode",
    }

    @classmethod
    def _resolve_clip_node(cls, wf: dict, start_id: str) -> str | None:
        """BFS from start_id through wire connections to find a CLIPTextEncode node."""
        visited: set[str] = set()
        queue = [start_id]
        while queue:
            nid = queue.pop(0)
            if nid in visited:
                continue
            visited.add(nid)
            node = wf.get(nid, {})
            if node.get("class_type") in cls._CLIP_ENCODE_TYPES:
                return nid
            for v in node.get("inputs", {}).values():
                if isinstance(v, list) and len(v) >= 1:
                    queue.append(str(v[0]))
        return None

    @classmethod
    def _find_clip_nodes_via_ksampler(cls, wf: dict) -> tuple[str | None, str | None]:
        """Return (pos_node_id, neg_node_id) by tracing from KSampler positive/negative inputs."""
        for node in wf.values():
            if node.get("class_type") not in cls._KSAMPLER_TYPES:
                continue
            inputs = node.get("inputs", {})
            pos_ref = inputs.get("positive")
            neg_ref = inputs.get("negative")
            pos_id = cls._resolve_clip_node(wf, str(pos_ref[0])) if isinstance(pos_ref, list) else None
            neg_id = cls._resolve_clip_node(wf, str(neg_ref[0])) if isinstance(neg_ref, list) else None
            if pos_id or neg_id:
                return pos_id, neg_id
        return None, None

    def patch_workflow(
        self,
        workflow: dict,
        positive: str,
        negative: str,
        pos_node_id: str = "",
        neg_node_id: str = "",
        batch_count: int = 1,
        seed: int | None = None,
    ) -> dict:
        wf = copy.deepcopy(workflow)

        auto_pos, auto_neg = self._find_clip_nodes_via_ksampler(wf)
        fallback_clips = (
            [k for k, v in wf.items() if v.get("class_type") in self._CLIP_ENCODE_TYPES]
            if not (auto_pos or auto_neg) else []
        )

        if pos_node_id and pos_node_id in wf:
            wf[pos_node_id]["inputs"]["text"] = positive
        elif auto_pos:
            wf[auto_pos]["inputs"]["text"] = positive
        elif fallback_clips:
            wf[fallback_clips[0]]["inputs"]["text"] = positive

        if negative:
            if neg_node_id and neg_node_id in wf:
                wf[neg_node_id]["inputs"]["text"] = negative
            elif auto_neg:
                wf[auto_neg]["inputs"]["text"] = negative
            elif len(fallback_clips) >= 2:
                wf[fallback_clips[1]]["inputs"]["text"] = negative

        if batch_count > 1:
            for node in wf.values():
                if node.get("class_type") in self._LATENT_NODE_TYPES:
                    node["inputs"]["batch_size"] = batch_count

        if seed is not None:
            patched: set[str] = set()

            def _patch_seed_node(node_id: str) -> None:
                """Patch the scalar seed in node_id if not already patched."""
                if node_id in patched:
                    return
                n = wf.get(node_id)
                if not n:
                    return
                inp = n.get("inputs", {})
                for key in ("seed", "noise_seed"):
                    if isinstance(inp.get(key), int):
                        inp[key] = seed
                        # Only set control_after_generate when already present in the node
                        if "control_after_generate" in inp:
                            inp["control_after_generate"] = "fixed"
                        patched.add(node_id)
                        return

            for node_id, node in wf.items():
                inputs = node.get("inputs", {})
                for seed_key in ("seed", "noise_seed"):
                    val = inputs.get(seed_key)
                    if val is None:
                        continue
                    if isinstance(val, int):
                        # Scalar seed on this node — patch directly
                        _patch_seed_node(node_id)
                    elif isinstance(val, list) and len(val) == 2:
                        # Wire reference [upstream_id, output_idx] — follow one level
                        _patch_seed_node(str(val[0]))
                    break  # handle only the first seed key per node

        return wf

    async def fetch_image(self, filename: str, subfolder: str = "", type_: str = "output") -> bytes:
        r = await self._http.get(
            f"{settings.comfyui_url}/view",
            params={"filename": filename, "subfolder": subfolder, "type": type_},
            timeout=120.0,
        )
        r.raise_for_status()
        return r.content

    async def queue_prompt(self, workflow: dict) -> str:
        r = await self._http.post(
            f"{settings.comfyui_url}/prompt",
            json={"prompt": workflow, "client_id": self.client_id},
        )
        r.raise_for_status()
        return r.json()["prompt_id"]

    async def stream_progress(self, prompt_id: str) -> AsyncGenerator[dict, None]:
        try:
            import websockets  # type: ignore
        except ImportError:
            yield {"type": "error", "message": "websockets library not installed"}
            return

        ws_url = (
            settings.comfyui_url
            .replace("http://", "ws://")
            .replace("https://", "wss://")
        )
        ws_url = f"{ws_url}/ws?clientId={self.client_id}"

        import time
        last_progress_time = 0.0

        try:
            async with websockets.connect(ws_url) as ws:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue

                    mtype = msg.get("type")
                    data = msg.get("data", {})

                    pid = data.get("prompt_id")
                    if pid and pid != prompt_id:
                        continue

                    if mtype == "progress":
                        val = data.get("value", 0)
                        max_val = data.get("max", 0)
                        now = time.monotonic()
                        
                        if val == max_val or (now - last_progress_time) > 0.1:
                            last_progress_time = now
                            yield {
                                "type": "comfy_progress",
                                "value": val,
                                "max": max_val,
                                "node": data.get("node", ""),
                            }

                    elif mtype == "executing":
                        node = data.get("node")
                        if node is None:
                            yield {"type": "comfy_done"}
                            return
                        yield {"type": "comfy_executing", "node": node}

                    elif mtype == "executed":
                        output = data.get("output", {})
                        images = output.get("images", [])
                        if images:
                            yield {"type": "comfy_output", "images": images}

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("ComfyUI WebSocket error: %s", exc)
            yield {"type": "error", "message": str(exc)}

    async def interrupt(self) -> None:
        """Send an interrupt signal to the currently running job (assumes concurrency=1)."""
        try:
            r = await self._http.post(f"{settings.comfyui_url}/interrupt", timeout=5.0)
            r.raise_for_status()
        except Exception as exc:
            logger.warning("ComfyUI interrupt error: %s", exc)

    async def delete_from_queue(self, prompt_id: str) -> None:
        """Remove a queued job that has not yet started execution."""
        try:
            r = await self._http.post(
                f"{settings.comfyui_url}/queue",
                json={"delete": [prompt_id]},
                timeout=5.0,
            )
            r.raise_for_status()
        except Exception as exc:
            logger.warning("ComfyUI queue delete error: %s", exc)

    async def fetch_history(self, prompt_id: str) -> list[dict]:
        """Return all output image refs from /history/{prompt_id} as fallback."""
        try:
            r = await self._http.get(
                f"{settings.comfyui_url}/history/{prompt_id}", timeout=10.0
            )
            r.raise_for_status()
            data = r.json()
            outputs = data.get(prompt_id, {}).get("outputs", {})
            images: list[dict] = []
            for node_output in outputs.values():
                for img in node_output.get("images", []):
                    images.append(img)
            return images
        except Exception as exc:
            logger.error("fetch_history error: %s", exc)
            return []

    async def close(self) -> None:
        await self._http.aclose()
