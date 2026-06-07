from __future__ import annotations

import asyncio
import glob
import logging
import subprocess
import time
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

    reachable: bool = True
    last_ok: float | None = None
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

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "kind": self.kind,
            "concurrency": self.concurrency,
            "endpoint": self.endpoint,
            "reachable": self.reachable,
            "last_ok": self.last_ok,
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


# Default configuration: all GPU lanes are consolidated under local-gpu0 (concurrency=1)
# EVALUATION is controlled via lane-level pause (tier2 rule) and does not use the resource semaphore.
# Holding the remote-ollama semaphore while entering a checkpoint pause would deadlock other lanes.
DEFAULT_LANE_RESOURCE: dict[JobLane, str | None] = {
    JobLane.GENERATION: "local-gpu0",
    JobLane.EMBEDDING:  "local-gpu0",
    JobLane.EVALUATION: None,
    JobLane.SYNC:       None,
    JobLane.PROMPT:     None,
}


def build_resources(settings) -> tuple[dict[str, Resource], dict[JobLane, str | None]]:
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

    lane_resource = dict(DEFAULT_LANE_RESOURCE)

    # If remote-ollama exists, delegate EMBEDDING to it
    # Do not delegate EVALUATION — it is controlled via lane pause (deadlock prevention)
    if "remote-ollama" in resources:
        lane_resource[JobLane.EMBEDDING] = "remote-ollama"

    return resources, lane_resource


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

    if res.kind == "remote" and not res.reachable:
        raise ResourceUnreachable(f"Resource {res.name!r} is unreachable")

    async with res._sem:
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
                    ok = resp.status_code < 500
                except Exception:
                    ok = False
                elapsed_ms = (time.monotonic() - t0) * 1000

                if ok:
                    res.reachable = True
                    res.last_ok = time.time()
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
                if resp.status_code < 500:
                    res.reachable = True
                    res.last_ok = time.time()
                    try:
                        body = resp.json()
                        if isinstance(body, dict) and "version" in body:
                            res.version = body["version"]
                    except Exception:
                        pass
                    logger.info("Resource %r is reachable at startup", res.name)
                else:
                    logger.warning(
                        "Resource %r returned %d at startup, marking unreachable",
                        res.name, resp.status_code,
                    )
        except Exception as exc:
            logger.warning(
                "Resource %r unreachable at startup (%s), will retry in background",
                res.name, exc,
            )
