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


# Eight bytes in the documented layout; a few dozen more once a metadata blob is
# in front of the image.
_PREVIEW_HEADER_MAX = 256


def _preview_image(payload: bytes) -> bytes | None:
    """Strip the binary header off a websocket preview frame.

    The frame is a 4-byte event type, a 4-byte image format and then the image,
    but newer builds can put a metadata blob in between. Looking for the magic
    survives both layouts; anything without one is not a preview.

    The search is bounded to the header: unbounded, a byte pair deep inside some
    other binary message would be read as the start of an image.
    """
    for magic in (b"\xff\xd8\xff", b"\x89PNG"):
        idx = payload.find(magic, 0, _PREVIEW_HEADER_MAX)
        if idx >= 0:
            return payload[idx:]
    return None


class ComfyUIClient:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=60.0)
        self.client_id = str(uuid.uuid4())

    async def is_available(self) -> bool:
        try:
            r = await self._http.get(f"{settings.comfyui_url}/system_stats", timeout=3.0)
            if r.status_code != 200:
                return False
            return "system" in r.json()
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
    # Turbo / Lightning graphs split sampling apart: the KSampler node has no
    # `steps` at all and the step count lives on a scheduler feeding it. Patching
    # only _KSAMPLER_TYPES silently did nothing on those workflows, so a caller
    # asking for a cheap 2-step draft got a full-price render and no warning.
    _SCHEDULER_TYPES = {
        "BasicScheduler", "KarrasScheduler", "ExponentialScheduler",
        "PolyexponentialScheduler", "VPScheduler", "BetaSamplingScheduler",
        "SDTurboScheduler", "AlignYourStepsScheduler", "LTXVScheduler",
        "LaplaceScheduler", "GITSScheduler",
    }
    _STEP_NODE_TYPES = _KSAMPLER_TYPES | _SCHEDULER_TYPES
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

    @classmethod
    def _resolve_latent_node(cls, wf: dict, start_id: str) -> str | None:
        """BFS from start_id through wire connections to find an EmptyLatent* node.

        Mirrors ``_resolve_clip_node``: follow upstream links from a KSampler's
        ``latent_image`` input until a known latent creator is found.
        """
        visited: set[str] = set()
        queue = [start_id]
        while queue:
            nid = queue.pop(0)
            if nid in visited:
                continue
            visited.add(nid)
            node = wf.get(nid, {})
            if node.get("class_type") in cls._LATENT_NODE_TYPES:
                return nid
            for v in node.get("inputs", {}).values():
                if isinstance(v, list) and len(v) >= 1:
                    queue.append(str(v[0]))
        return None

    @classmethod
    def _find_latent_nodes_via_ksampler(cls, wf: dict) -> list[str]:
        """Latent node ids reached from each KSampler's ``latent_image`` input."""
        found: list[str] = []
        seen: set[str] = set()
        for node in wf.values():
            if node.get("class_type") not in cls._KSAMPLER_TYPES:
                continue
            ref = (node.get("inputs") or {}).get("latent_image")
            if not isinstance(ref, list) or not ref:
                continue
            lid = cls._resolve_latent_node(wf, str(ref[0]))
            if lid and lid not in seen:
                seen.add(lid)
                found.append(lid)
        return found

    @classmethod
    def _patch_node_int_field(
        cls, wf: dict, node_id: str, key: str, value: int
    ) -> bool:
        """Set an int field on a node, or follow a wire to a Primitive and set it.

        Replacing a wire ``[upstream, idx]`` with a bare int is also valid in
        ComfyAPI graphs; we prefer patching the upstream Primitive when present
        so other consumers of that Primitive stay consistent.
        """
        node = wf.get(node_id)
        if not node:
            return False
        inputs = node.setdefault("inputs", {})
        cur = inputs.get(key)
        if isinstance(cur, bool):
            return False
        if isinstance(cur, (int, float)):
            inputs[key] = int(value)
            return True
        if isinstance(cur, list) and len(cur) >= 1:
            up_id = str(cur[0])
            up = wf.get(up_id)
            if not up:
                # Dangling wire — replace with scalar so the graph still runs.
                inputs[key] = int(value)
                return True
            up_inputs = up.setdefault("inputs", {})
            up_type = up.get("class_type") or ""
            if "value" in up_inputs and isinstance(up_inputs["value"], (int, float)):
                up_inputs["value"] = int(value)
                return True
            if up_type in (
                "PrimitiveNode", "PrimitiveInt", "PrimitiveFloat", "INT", "Float",
            ):
                up_inputs["value"] = int(value)
                return True
            # Unknown upstream — break the wire and set a scalar on the latent.
            inputs[key] = int(value)
            return True
        # Field missing (unusual for EmptyLatent) — set it.
        if key in ("width", "height", "batch_size"):
            inputs[key] = int(value)
            return True
        return False

    @classmethod
    def patchable_fields(cls, workflow: dict) -> dict[str, int]:
        """How many nodes ``patch_workflow`` could write each knob to.

        A zero means the corresponding argument will be accepted and then do
        nothing — which is worth telling the user about *before* they wait for a
        render that ignored their settings.
        """
        counts = {"steps": 0, "cfg": 0, "width": 0, "height": 0, "seed": 0}
        for node in workflow.values():
            class_type = node.get("class_type")
            inputs = node.get("inputs", {}) or {}
            if class_type in cls._STEP_NODE_TYPES and "steps" in inputs:
                counts["steps"] += 1
            if class_type in cls._KSAMPLER_TYPES and "cfg" in inputs:
                counts["cfg"] += 1
            if class_type in cls._LATENT_NODE_TYPES:
                for dim in ("width", "height"):
                    if dim in inputs:
                        counts[dim] += 1
            if "seed" in inputs or "noise_seed" in inputs:
                counts["seed"] += 1
        return counts

    def patch_workflow(
        self,
        workflow: dict,
        positive: str,
        negative: str,
        pos_node_id: str = "",
        neg_node_id: str = "",
        batch_count: int = 1,
        seed: int | None = None,
        width: int | None = None,
        height: int | None = None,
        steps: int | None = None,
        cfg: float | None = None,
        append_negative: bool = False,
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
            neg_target = None
            if neg_node_id and neg_node_id in wf:
                neg_target = neg_node_id
            elif auto_neg:
                neg_target = auto_neg
            elif len(fallback_clips) >= 2:
                neg_target = fallback_clips[1]
            if neg_target:
                # append_negative extends the workflow's baked negative instead of
                # replacing it, so caller-supplied tags add to (not wipe) the default.
                if append_negative:
                    raw = wf[neg_target]["inputs"].get("text")
                    # An API-format input is either a literal or a link
                    # ["<node_id>", <slot>]. str() on a link used to paste
                    # "['99', 0]" into the prompt, and the assignment below
                    # severs the link anyway — so treat a link as "nothing to
                    # keep" and say so rather than losing it silently.
                    if isinstance(raw, str):
                        existing = raw.strip()
                    else:
                        existing = ""
                        if raw is not None:
                            logger.info(
                                "[comfy] node %s negative text is wired from %s; "
                                "its content cannot be appended to",
                                neg_target, raw,
                            )
                    wf[neg_target]["inputs"]["text"] = (
                        f"{existing}, {negative}" if existing else negative
                    )
                else:
                    wf[neg_target]["inputs"]["text"] = negative

        # Size / batch: only touch EmptyLatent* nodes that are actually wired
        # into a KSampler.latent_image (same connection-tracing pattern as CLIP).
        # Fall back to every EmptyLatent* only when no connected latent is found.
        if batch_count > 1 or width is not None or height is not None:
            latent_ids = self._find_latent_nodes_via_ksampler(wf)
            if not latent_ids:
                latent_ids = [
                    k for k, v in wf.items()
                    if v.get("class_type") in self._LATENT_NODE_TYPES
                ]
            for lid in latent_ids:
                if batch_count > 1:
                    self._patch_node_int_field(wf, lid, "batch_size", batch_count)
                if width is not None:
                    self._patch_node_int_field(wf, lid, "width", int(width))
                if height is not None:
                    self._patch_node_int_field(wf, lid, "height", int(height))

        if steps is not None or cfg is not None:
            for node_id, node in wf.items():
                class_type = node.get("class_type")
                if class_type not in self._STEP_NODE_TYPES:
                    continue
                inputs = node.setdefault("inputs", {})
                if steps is not None and "steps" in inputs:
                    cur = inputs["steps"]
                    if isinstance(cur, list) and len(cur) >= 1:
                        # Follow Primitive wire when present; else replace link.
                        self._patch_node_int_field(wf, node_id, "steps", int(steps))
                    elif isinstance(cur, (int, float)):
                        inputs["steps"] = int(steps)
                # cfg belongs to the sampler, never to a scheduler.
                if cfg is not None and class_type in self._KSAMPLER_TYPES and "cfg" in inputs:
                    cur = inputs["cfg"]
                    if isinstance(cur, list) and len(cur) >= 1:
                        up = wf.get(str(cur[0]))
                        if up and "value" in up.get("inputs", {}):
                            up["inputs"]["value"] = float(cfg)
                        else:
                            inputs["cfg"] = float(cfg)
                    elif isinstance(cur, (int, float)):
                        inputs["cfg"] = float(cfg)

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

    _LOAD_IMAGE_TYPES = frozenset({
        "LoadImage",
        "LoadImageMask",
        "LoadImageOutput",
        "LoadImageFromUrl",
        "VHS_LoadImagePath",
        "Image Load",
        "LoadImageBatch",
    })

    def patch_load_image_nodes(self, workflow: dict, image_name: str) -> tuple[dict, int]:
        """Set ``inputs.image`` on every LoadImage-like node. Returns (wf, count)."""
        wf = copy.deepcopy(workflow)
        n = 0
        for node in wf.values():
            if node.get("class_type") in self._LOAD_IMAGE_TYPES:
                node.setdefault("inputs", {})["image"] = image_name
                n += 1
        return wf, n

    async def upload_image(
        self,
        data: bytes,
        filename: str,
        *,
        overwrite: bool = True,
        image_type: str = "input",
    ) -> str:
        """Upload bytes to Comfy ``/upload/image``.

        Returns the filename string suitable for ``LoadImage.inputs.image``
        (``subfolder/name`` when Comfy stores under a subfolder).
        """
        files = {"image": (filename, data, "application/octet-stream")}
        form = {
            "overwrite": "true" if overwrite else "false",
            "type": image_type,
        }
        r = await self._http.post(
            f"{settings.comfyui_url}/upload/image",
            files=files,
            data=form,
            timeout=120.0,
        )
        r.raise_for_status()
        body = r.json() if r.content else {}
        if not isinstance(body, dict):
            return filename
        name = str(body.get("name") or filename)
        sub = str(body.get("subfolder") or "").strip().strip("/")
        if sub:
            return f"{sub}/{name}"
        return name

    async def fetch_image(self, filename: str, subfolder: str = "", type_: str = "output") -> bytes:
        r = await self._http.get(
            f"{settings.comfyui_url}/view",
            params={"filename": filename, "subfolder": subfolder, "type": type_},
            timeout=120.0,
        )
        r.raise_for_status()
        return r.content

    async def queue_prompt(self, workflow: dict, *, preview: bool = False) -> str:
        """Queue a graph. ``preview`` turns on in-flight latent previews.

        ComfyUI takes the preview method **per prompt**, not per server — the web
        UI sends it on every queue call, which is why previews appear there while
        an API client that omits it sees none, however the server was started.
        It is opt-in because the frames are one JPEG per sampler step and only
        Muse, which lets you watch a draft form and abort it, has any use for them.
        """
        body: dict = {"prompt": workflow, "client_id": self.client_id}
        if preview:
            body["extra_data"] = {"preview_method": "auto"}
        r = await self._http.post(f"{settings.comfyui_url}/prompt", json=body)
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
            async with websockets.connect(ws_url, max_size=None) as ws:
                async for raw in ws:
                    if isinstance(raw, (bytes, bytearray)):
                        jpeg = _preview_image(bytes(raw))
                        if jpeg is not None:
                            # Preview frames carry no prompt_id. ComfyUI runs one
                            # graph at a time, so the frame belongs to whatever
                            # this client is currently waiting on.
                            yield {"type": "comfy_preview", "image": jpeg}
                        continue
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
