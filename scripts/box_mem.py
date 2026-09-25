"""Graceful memory management for the shared ComfyUI box (10.0.10.30).

The box's 24GB 3090 and its host RAM are shared with a stack of other containers (game servers,
firecrawl, ...). Loading a big diffusion model (e.g. Qwen-Image-Edit fp8, ~20GB) can push host
RAM into a swap-death spiral. This module reclaims memory around a heavy job and always restores
state afterward.

Two layers:
  VRAM / loaded models -> comfy.free_memory()  (POST /free; evict before switching model family)
  Host RAM during load -> reclaim_ram()         (temporarily `docker stop` the RAM-hog containers)

`docker pause` is NOT used: a paused container keeps its RAM resident, so it frees nothing. Only
`docker stop` returns the memory. Everything stopped here is restarted in a finally block.

    from box_mem import reclaim_ram, comfy_restart, snapshot
    print(snapshot())
    with reclaim_ram(need_free_gb=24):     # frees host RAM only if we're short
        png = run_the_heavy_qwen_job()
    # stopped containers are back up here
"""

from __future__ import annotations

import contextlib
import subprocess
import time

BOX = "ben@10.0.10.30"

# RAM-hungry, safely-restartable containers, biggest-first. Game servers reserve large heaps;
# stopping them for a few minutes frees the most RAM with the least collateral. `comfyui` and
# infra (redis, firecrawl) are intentionally excluded.
HEAVY_CONTAINERS = ["7dtde", "7dtd", "7DtDBM", "minecraft-server", "terraria-modded"]


def _ssh(cmd: str, timeout: int = 30) -> str:
    out = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", BOX, cmd],
        capture_output=True, text=True, timeout=timeout)
    return (out.stdout or "").strip()


def gpu_mem() -> tuple[int, int]:
    """(used_mb, total_mb) for cuda:0."""
    try:
        line = _ssh("nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits")
        u, t = line.splitlines()[0].split(",")
        return int(u), int(t)
    except Exception:  # noqa: BLE001
        return -1, -1


def host_mem() -> dict:
    """{total,used,free,available,swap_used} in GB (int)."""
    try:
        mem = _ssh("free -m").splitlines()
        m = next(l for l in mem if l.lower().startswith("mem:")).split()
        s = next((l for l in mem if l.lower().startswith("swap:")), "swap: 0 0 0").split()
        g = lambda x: int(round(int(x) / 1024))
        return {"total": g(m[1]), "used": g(m[2]), "free": g(m[3]),
                "available": g(m[6]) if len(m) > 6 else g(m[3]), "swap_used": g(s[2])}
    except Exception:  # noqa: BLE001
        return {}


def running_heavy() -> list[str]:
    names = set(_ssh("docker ps --format '{{.Names}}'").split())
    return [c for c in HEAVY_CONTAINERS if c in names]


def snapshot() -> str:
    gu, gt = gpu_mem()
    h = host_mem()
    return (f"GPU {gu}/{gt} MB | host RAM avail {h.get('available','?')}/{h.get('total','?')} GB "
            f"(swap used {h.get('swap_used','?')} GB) | heavy up: {running_heavy() or 'none'}")


def comfy_restart(wait_s: int = 90) -> bool:
    """Restart the comfyui container for a clean slate (clears a wedged/half-loaded model), then
    wait until its HTTP answers again."""
    import urllib.request
    _ssh("docker restart comfyui", timeout=60)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        try:
            urllib.request.urlopen("http://10.0.10.30:8188/queue", timeout=5).read()
            return True
        except Exception:  # noqa: BLE001
            time.sleep(3)
    return False


@contextlib.contextmanager
def reclaim_ram(need_free_gb: int = 24, containers: list[str] | None = None):
    """Ensure ~need_free_gb of host RAM is available for a heavy load by temporarily stopping the
    RAM-hog containers, then restart whatever we stopped (even on error). No-op if we already have
    the headroom."""
    avail = host_mem().get("available", 0)
    stopped: list[str] = []
    try:
        if avail >= need_free_gb:
            print(f"[reclaim] {avail} GB available >= {need_free_gb} GB target — no stop needed")
        else:
            for c in (containers or running_heavy()):
                if host_mem().get("available", 0) >= need_free_gb:
                    break
                print(f"[reclaim] stopping {c} to free RAM (have "
                      f"{host_mem().get('available','?')} GB, want {need_free_gb})")
                _ssh(f"docker stop {c}", timeout=60)
                stopped.append(c)
            print(f"[reclaim] available now {host_mem().get('available','?')} GB; stopped {stopped or 'nothing'}")
        yield stopped
    finally:
        for c in reversed(stopped):
            print(f"[reclaim] restarting {c}")
            _ssh(f"docker start {c}", timeout=60)


if __name__ == "__main__":
    print(snapshot())
