from __future__ import annotations

import asyncio
import glob
import logging
import os
import shutil
import subprocess
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from .models import Job, JobCancelled, JobLane, ResourceUnreachable

logger = logging.getLogger(__name__)

# Previous sample for CPU% calculation (idle_ticks, total_ticks)
_prev_cpu_stat: tuple[int, int] | None = None


def _read_proc_stat() -> tuple[int, int]:
    with open("/proc/stat") as f:
        parts = f.readline().split()
    vals = [int(x) for x in parts[1:]]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
    total = sum(vals[:8])
    return idle, total


def _poll_cpu_pct() -> float | None:
    global _prev_cpu_stat
    try:
        curr = _read_proc_stat()
        if _prev_cpu_stat is None:
            _prev_cpu_stat = curr
            return None
        idle_diff  = curr[0] - _prev_cpu_stat[0]
        total_diff = curr[1] - _prev_cpu_stat[1]
        _prev_cpu_stat = curr
        if total_diff == 0:
            return 0.0
        return round((1.0 - idle_diff / total_diff) * 100.0, 1)
    except Exception:
        return None


def _poll_ram_stats() -> dict[str, float | None]:
    try:
        data: dict[str, int] = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    data[parts[0].rstrip(":")] = int(parts[1])  # kB
        total_gb = round(data["MemTotal"] / 1024**2, 1)
        used_gb  = round((data["MemTotal"] - data["MemAvailable"]) / 1024**2, 1)
        return {"ram_used_gb": used_gb, "ram_total_gb": total_gb}
    except Exception:
        return {"ram_used_gb": None, "ram_total_gb": None}


def _poll_cpu_temp() -> float | None:
    # First try hwmon drivers: coretemp / k10temp / zenpower
    try:
        for hwmon in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
            try:
                with open(f"{hwmon}/name") as f:
                    name = f.read().strip()
                if name in ("coretemp", "k10temp", "zenpower"):
                    with open(f"{hwmon}/temp1_input") as f:
                        return round(int(f.read().strip()) / 1000.0, 1)
            except (FileNotFoundError, ValueError):
                continue
    except Exception:
        pass
    # Fallback: thermal_zone (x86_pkg_temp / cpu-thermal, etc.)
    try:
        best: float | None = None
        for zone in glob.glob("/sys/class/thermal/thermal_zone*"):
            try:
                with open(f"{zone}/type") as f:
                    typ = f.read().strip().lower()
                if "x86" in typ or "cpu" in typ or "acpitz" in typ:
                    with open(f"{zone}/temp") as f:
                        t = int(f.read().strip()) / 1000.0
                    if best is None or t > best:
                        best = t
            except Exception:
                pass
        return round(best, 1) if best is not None else None
    except Exception:
        return None


def _poll_gpu_stats() -> dict[str, float | None]:
    """Retrieve GPU statistics via nvidia-ml-py (pynvml API). Returns all fields as None on failure."""
    try:
        import pynvml  # nvidia-ml-py package provides this module  # type: ignore
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        return {
            "gpu_util_pct": float(util.gpu),
            "temp_c": float(temp),
            "vram_used_gb": round(mem.used / 1024**3, 1),
            "vram_total_gb": round(mem.total / 1024**3, 1),
        }
    except Exception:
        pass

    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            parts = [p.strip() for p in result.stdout.strip().split(",")]
            if len(parts) == 4:
                return {
                    "gpu_util_pct": float(parts[0]),
                    "temp_c": float(parts[1]),
                    "vram_used_gb": round(float(parts[2]) / 1024, 1),
                    "vram_total_gb": round(float(parts[3]) / 1024, 1),
                }
    except Exception:
        pass

    return {"gpu_util_pct": None, "temp_c": None, "vram_used_gb": None, "vram_total_gb": None}


@dataclass
class Resource:
    name: str
    kind: Literal["local", "remote"]
    concurrency: int = 1
    endpoint: str | None = None
    health_path: str = "/"

    reachable: bool = False
    last_ok: float | None = None
    last_checked: float | None = None  # set on every probe attempt (success or failure)
    latency_ms: float | None = None
    version: str | None = None

    # local kind only: GPU / CPU / RAM metrics
    gpu_util_pct: float | None = None
    temp_c: float | None = None        # GPU temperature
    vram_used_gb: float | None = None
    vram_total_gb: float | None = None
    cpu_pct: float | None = None
    cpu_temp_c: float | None = None
    ram_used_gb: float | None = None
    ram_total_gb: float | None = None

    _sem: asyncio.Semaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._sem = asyncio.Semaphore(self.concurrency)

    @asynccontextmanager
    async def acquire(self):
        """Acquire the resource semaphore for one request/section.

        Fails fast with ResourceUnreachable for remote resources known to be down
        (only after the first health probe has run — permissive during startup).
        """
        if self.kind == "remote" and self.last_checked is not None and not self.reachable:
            raise ResourceUnreachable(f"Resource {self.name!r} is unreachable")
        async with self._sem:
            yield

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "kind": self.kind,
            "concurrency": self.concurrency,
            "endpoint": self.endpoint,
            "reachable": self.reachable,
            "last_ok": self.last_ok,
            "last_checked": self.last_checked,
            "latency_ms": self.latency_ms,
            "version": self.version,
        }
        if self.kind == "local":
            d.update({
                "gpu_util_pct": self.gpu_util_pct,
                "temp_c": self.temp_c,
                "vram_used_gb": self.vram_used_gb,
                "vram_total_gb": self.vram_total_gb,
                "cpu_pct": self.cpu_pct,
                "cpu_temp_c": self.cpu_temp_c,
                "ram_used_gb": self.ram_used_gb,
                "ram_total_gb": self.ram_total_gb,
            })
        return d


def _is_local_url(url: str) -> bool:
    """True if the URL points to the local host (same physical machine as the backend)."""
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1", "host.docker.internal")


# Default configuration.
# Ollama traffic is throttled INSIDE OllamaClient (per-request acquisition of
# remote-ollama), so no lane holds the Ollama semaphore across a whole job —
# this is what makes lane pause checkpoints deadlock-free.
# GENERATION is the only whole-job resource: one generation = one ComfyUI run.
DEFAULT_LANE_RESOURCE: dict[JobLane, str | None] = {
    JobLane.GENERATION: "local-gpu0",
    JobLane.EMBEDDING:  None,
    JobLane.EVALUATION: None,
    JobLane.SYNC:       None,
    JobLane.PROMPT:     None,
    JobLane.TAGGING:    None,
}

# Resources whose semaphore is acquired inside a client (per HTTP request).
# Mapping a LANE onto these would make a job acquire the same non-reentrant
# semaphore twice (whole-job + per-request) and self-deadlock.
_CLIENT_MANAGED_RESOURCES: frozenset[str] = frozenset({"remote-ollama"})


def build_resources(settings) -> tuple[dict[str, Resource], dict[JobLane, str | None], dict]:
    """Build the Resource dict and lane→resource mapping from settings."""
    resources: dict[str, Resource] = {
        "local-gpu0": Resource(
            name="local-gpu0",
            kind="local",
            concurrency=getattr(settings, "resource_local_gpu0_concurrency", 1),
        ),
    }

    # Register Ollama as a monitored resource (use the separate endpoint if configured, otherwise fall back to ollama_url)
    remote_ollama_endpoint = (
        getattr(settings, "resource_remote_ollama_endpoint", None)
        or getattr(settings, "ollama_url", None)
    )
    if remote_ollama_endpoint:
        resources["remote-ollama"] = Resource(
            name="remote-ollama",
            kind="remote",
            concurrency=getattr(settings, "resource_remote_ollama_concurrency", 1),
            endpoint=remote_ollama_endpoint,
            health_path=getattr(settings, "resource_remote_ollama_health_path", "/api/version"),
            reachable=False,
        )

    # Register Qdrant as a monitoring-only remote resource (not included in lane mapping)
    qdrant_url = getattr(settings, "qdrant_url", None)
    if qdrant_url:
        resources["remote-qdrant"] = Resource(
            name="remote-qdrant",
            kind="remote",
            concurrency=99,  # Not used in lane mapping, so semaphore is effectively a no-op
            endpoint=qdrant_url,
            health_path=getattr(settings, "resource_remote_qdrant_health_path", "/healthz"),
            reachable=False,
        )

    # Register ComfyUI as a remote resource — GENERATION maps onto it so that
    # generation jobs serialize per its concurrency and fail fast when it is down
    comfyui_url = getattr(settings, "comfyui_url", None)
    if comfyui_url:
        resources["remote-comfyui"] = Resource(
            name="remote-comfyui",
            kind="remote",
            concurrency=getattr(settings, "resource_remote_comfyui_concurrency", 1),
            endpoint=comfyui_url,
            health_path="/system_stats",
            reachable=False,
        )

    lane_resource = dict(DEFAULT_LANE_RESOURCE)

    if "remote-comfyui" in resources:
        lane_resource[JobLane.GENERATION] = "remote-comfyui"

    # Topology detection: determine whether Ollama and ComfyUI are on the same
    # physical host as the backend.  host.docker.internal / localhost / 127.0.0.1
    # all indicate "local machine".
    ollama_url_str = getattr(settings, "ollama_url", "") or ""
    comfyui_url_str = comfyui_url or ""
    ollama_local = _is_local_url(ollama_url_str)
    comfyui_local = _is_local_url(comfyui_url_str)

    # prompt_gen_mutex: None = auto (same-host → True), True/False = explicit override.
    # Both lanes are never pause targets, so holding local-gpu0 across the whole job
    # cannot meet a checkpoint pause (no circular wait / no deadlock).
    raw_mutex = getattr(settings, "prompt_gen_mutex", None)
    if raw_mutex is None:
        effective_mutex = ollama_local and comfyui_local
        logger.info(
            "[topology] auto prompt_gen_mutex=%s  ollama=%s (local=%s)  comfyui=%s (local=%s)",
            effective_mutex, ollama_url_str, ollama_local, comfyui_url_str, comfyui_local,
        )
    else:
        effective_mutex = bool(raw_mutex)
        logger.info("[topology] explicit prompt_gen_mutex=%s", effective_mutex)

    if effective_mutex:
        lane_resource[JobLane.GENERATION] = "local-gpu0"
        lane_resource[JobLane.PROMPT] = "local-gpu0"

    # Expert override: {"gen": "remote-comfyui", "prompt": null, ...}
    for lane_val, res_name in (getattr(settings, "resource_lane_map", None) or {}).items():
        if lane_val not in JobLane._value2member_map_:
            logger.warning("resource_lane_map: unknown lane %r ignored", lane_val)
            continue
        if res_name and res_name in _CLIENT_MANAGED_RESOURCES:
            logger.warning(
                "resource_lane_map: %r is client-managed (per-request acquisition) — "
                "mapping lane %r onto it would self-deadlock; ignored",
                res_name, lane_val,
            )
            continue
        if res_name and res_name not in resources:
            logger.warning("resource_lane_map: unknown resource %r for lane %r ignored", res_name, lane_val)
            continue
        lane_resource[JobLane(lane_val)] = res_name or None

    topology = {
        "ollama_local": ollama_local,
        "comfyui_local": comfyui_local,
        "tagging_local": True,  # WD14 always runs on the backend CPU
        "prompt_gen_mutex": effective_mutex,
    }
    return resources, lane_resource, topology


def disk_snapshot(paths: dict[str, str]) -> list[dict]:
    """Return one entry per unique filesystem device among the given named paths."""
    seen: set[int] = set()
    result = []
    for name, path in paths.items():
        try:
            dev = os.stat(path).st_dev
            if dev in seen:
                continue
            seen.add(dev)
            u = shutil.disk_usage(path)
            result.append({
                "name": name,
                "kind": "disk",
                "path": path,
                "total_gb": round(u.total / 1024**3, 1),
                "used_gb":  round(u.used  / 1024**3, 1),
                "free_gb":  round(u.free  / 1024**3, 1),
                "used_pct": round(u.used / u.total * 100, 1),
            })
        except Exception:
            pass
    return result

async def run_with_resource(
    job: Job,
    resources: dict[str, Resource],
    lane_resource: dict[JobLane, str | None],
    func: Callable,
    *args: Any,
) -> Any:
    res_name = lane_resource.get(job.lane)
    if res_name is None:
        return await func(*args)

    res = resources.get(res_name)
    if res is None:
        logger.warning("Resource %r not found, running without semaphore", res_name)
        return await func(*args)

    async with res.acquire():
        return await func(*args)


async def monitor_remote_resources(
    resources: dict[str, Resource],
    interval: int = 15,
) -> None:
    """Periodically poll the liveness of remote resources."""
    import httpx

    remote = {name: r for name, r in resources.items() if r.kind == "remote"}
    if not remote:
        return

    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            for res in remote.values():
                url = f"{res.endpoint.rstrip('/')}{res.health_path}"
                t0 = time.monotonic()
                try:
                    resp = await client.get(url)
                    ok = resp.status_code == 200
                except Exception:
                    ok = False
                elapsed_ms = (time.monotonic() - t0) * 1000

                now = time.time()
                res.last_checked = now
                if ok:
                    res.reachable = True
                    res.last_ok = now
                    res.latency_ms = round(elapsed_ms, 1)
                    try:
                        body = resp.json()
                        if isinstance(body, dict) and "version" in body:
                            res.version = body["version"]
                    except Exception:
                        pass
                else:
                    if res.reachable:
                        logger.warning("Resource %r became unreachable", res.name)
                    res.reachable = False

            await asyncio.sleep(interval)


async def monitor_local_resources(
    resources: dict[str, Resource],
    interval: int = 5,
) -> None:
    """Periodically poll statistics for local resources (GPU / CPU / RAM)."""
    local = {name: r for name, r in resources.items() if r.kind == "local"}
    if not local:
        return

    def _poll_all() -> dict:
        gpu  = _poll_gpu_stats()
        ram  = _poll_ram_stats()
        return {
            **gpu,
            "cpu_pct":    _poll_cpu_pct(),
            "cpu_temp_c": _poll_cpu_temp(),
            **ram,
        }

    while True:
        stats = await asyncio.to_thread(_poll_all)
        for res in local.values():
            res.gpu_util_pct  = stats["gpu_util_pct"]
            res.temp_c        = stats["temp_c"]
            res.vram_used_gb  = stats["vram_used_gb"]
            res.vram_total_gb = stats["vram_total_gb"]
            res.cpu_pct       = stats["cpu_pct"]
            res.cpu_temp_c    = stats["cpu_temp_c"]
            res.ram_used_gb   = stats["ram_used_gb"]
            res.ram_total_gb  = stats["ram_total_gb"]
        await asyncio.sleep(interval)


async def probe_resources_on_startup(resources: dict[str, Resource]) -> None:
    """Verify initial connectivity to remote resources on startup."""
    import httpx

    for res in resources.values():
        if res.kind != "remote":
            continue
        url = f"{res.endpoint.rstrip('/')}{res.health_path}"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                now = time.time()
                res.last_checked = now
                if resp.status_code == 200:
                    res.reachable = True
                    res.last_ok = now
                    try:
                        body = resp.json()
                        if isinstance(body, dict) and "version" in body:
                            res.version = body["version"]
                    except Exception:
                        pass
                    logger.info("Resource %r is reachable at startup", res.name)
                else:
                    res.reachable = False
                    logger.warning(
                        "Resource %r returned %d at startup, marking unreachable",
                        res.name, resp.status_code,
                    )
        except Exception as exc:
            res.last_checked = time.time()
            res.reachable = False
            logger.warning(
                "Resource %r unreachable at startup (%s), will retry in background",
                res.name, exc,
            )
