#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


CYAN = "\033[96m"
AMBER = "\033[33m"
RED = "\033[91m"
RESET = "\033[0m"


def _run(cmd: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        output = (result.stdout or "") + (result.stderr or "")
        return result.returncode == 0, output.strip()
    except Exception as exc:
        return False, str(exc)


@dataclass(slots=True)
class CheckItem:
    name: str
    status: str
    message: str


def _pass(name: str, message: str) -> CheckItem:
    return CheckItem(name=name, status="PASS", message=message)


def _fail(name: str, message: str) -> CheckItem:
    return CheckItem(name=name, status="FAIL", message=message)


def _warn(name: str, message: str) -> CheckItem:
    return CheckItem(name=name, status="WARN", message=message)


def check_nvidia() -> CheckItem:
    if shutil.which("nvidia-smi") is None:
        return _fail("NVIDIA", "nvidia-smi not found")
    ok, out = _run(["nvidia-smi"])
    if ok:
        return _pass("NVIDIA", "nvidia-smi detected")
    return _fail("NVIDIA", f"nvidia-smi failed: {out}")


def check_cuda() -> CheckItem:
    if shutil.which("nvcc") is None:
        return _fail("CUDA", "nvcc not found")
    ok, out = _run(["nvcc", "--version"])
    if ok:
        return _pass("CUDA", "CUDA toolkit detected")
    return _fail("CUDA", f"nvcc failed: {out}")


def _max_compute_capability() -> float | None:
    if shutil.which("nvidia-smi") is None:
        return None
    ok, out = _run(
        [
            "nvidia-smi",
            "--query-gpu=compute_cap",
            "--format=csv,noheader",
        ]
    )
    if not ok:
        return None

    caps: list[float] = []
    for raw in out.splitlines():
        value = raw.strip().split()[0] if raw.strip() else ""
        if not value:
            continue
        try:
            caps.append(float(value))
        except ValueError:
            continue
    if not caps:
        return None
    return max(caps)


def check_llama_server_flags() -> CheckItem:
    llama_server = shutil.which("llama-server")
    if llama_server is None:
        return _warn("llama-server", "llama-server not found in PATH; skipped cache-flag validation")

    ok, out = _run([llama_server, "--help"])
    if not ok:
        return _warn("llama-server", f"failed to read --help output: {out}")

    help_text = out or ""
    has_slots = "--slots" in help_text
    has_cache_reuse = "--cache-reuse" in help_text
    has_legacy_prompt_cache = "--prompt-cache" in help_text or "--prompt-cache-all" in help_text
    cc = _max_compute_capability()
    is_cc_12 = bool(cc is not None and cc >= 12.0)

    if not has_slots or not has_cache_reuse:
        return _fail(
            "llama-server",
            "missing --slots/--cache-reuse support; install a modern llama.cpp build",
        )

    if has_legacy_prompt_cache and is_cc_12:
        return _warn(
            "llama-server",
            "legacy --prompt-cache flags detected, but CC 12.0 GPU found; defaulting to slot-based reuse (--slots 1 + --cache-reuse)",
        )

    if has_legacy_prompt_cache:
        return _warn(
            "llama-server",
            "legacy --prompt-cache flags detected; supported, but slot-based reuse is the default path",
        )

    return _pass("llama-server", "modern slot-based cache flags detected (no legacy prompt-cache flags)")


def _llama_vram_warning() -> CheckItem | None:
    if shutil.which("nvidia-smi") is None:
        return None

    ok, out = _run(
        [
            "nvidia-smi",
            "--query-compute-apps=process_name,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ]
    )
    if not ok:
        return None

    lines = [line.strip() for line in out.splitlines() if line.strip()]
    llama_mem_mb = 0
    for line in lines:
        parts = [part.strip() for part in line.split(",", 1)]
        if len(parts) != 2:
            continue
        process_name, used_memory = parts
        if "llama" not in process_name.lower():
            continue
        try:
            llama_mem_mb = max(llama_mem_mb, int(float(used_memory)))
        except ValueError:
            continue

    if llama_mem_mb <= 0:
        return _warn(
            "llama-swap",
            "Service active but model not detected in VRAM.",
        )
    return None


def check_llama_swap() -> list[CheckItem]:
    results: list[CheckItem] = []
    if shutil.which("systemctl") is None:
        results.append(_fail("llama-swap", "systemctl not found"))
        return results

    ok, out = _run(["systemctl", "is-active", "llama-swap"])
    active = out.strip() == "active"
    if ok and active:
        results.append(_pass("llama-swap", "llama-swap is active"))
        warn_item = _llama_vram_warning()
        if warn_item is not None:
            results.append(warn_item)
        return results

    results.append(_fail("llama-swap", f"llama-swap state: {out or 'unknown'}"))
    return results


def check_redis(host: str, port: int, timeout: float) -> CheckItem:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return _pass("Redis", f"redis reachable at {host}:{port}")
    except Exception as exc:
        return _fail("Redis", f"redis unavailable at {host}:{port} ({exc})")
    finally:
        sock.close()


def check_model_dir(model_dir_value: str) -> CheckItem:
    normalized = os.path.expandvars(os.path.expanduser(model_dir_value))
    model_dir = Path(normalized)

    if not model_dir.exists() or not model_dir.is_dir():
        return _fail("Models", f"model directory not found: {model_dir}")

    gguf_files = [p for p in model_dir.glob("**/*.gguf") if p.is_file()]
    mmproj_files = [p for p in gguf_files if "mmproj" in p.name.lower()]

    if not gguf_files:
        return _fail("Models", "no .gguf files found")
    if not mmproj_files:
        return _fail("Models", "no mmproj .gguf files found")

    largest = max(gguf_files, key=lambda path: path.stat().st_size)
    largest_mb = largest.stat().st_size / (1024 * 1024)
    return _pass(
        "Models",
        (
            f"found {len(gguf_files)} gguf and {len(mmproj_files)} mmproj files; "
            f"largest model: {largest.name} ({largest_mb:.1f} MB)"
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Gibson setup validator")
    parser.add_argument(
        "--model-dir",
        default=str(Path.home() / "models"),
        help="Path to model directory containing .gguf and mmproj files",
    )
    parser.add_argument("--redis-host", default="127.0.0.1")
    parser.add_argument("--redis-port", type=int, default=6379)
    parser.add_argument("--redis-timeout", type=float, default=1.5)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    checks: list[CheckItem] = [
        check_nvidia(),
        check_cuda(),
        check_llama_server_flags(),
        *check_llama_swap(),
        check_redis(args.redis_host, args.redis_port, args.redis_timeout),
        check_model_dir(args.model_dir),
    ]

    failures = sum(1 for item in checks if item.status == "FAIL")
    status = "READY" if failures == 0 else "INCOMPLETE"

    if args.as_json:
        payload = {
            "status": status,
            "failures": failures,
            "details": [
                {"name": item.name, "status": item.status, "message": item.message}
                for item in checks
            ],
        }
        print(json.dumps(payload))
        return 0 if failures == 0 else 1

    print(f"{CYAN}== Gibson Check =={RESET}")
    for item in checks:
        if item.status == "PASS":
            prefix = f"{CYAN}[PASS]{RESET}"
        elif item.status == "WARN":
            prefix = f"{AMBER}[WARN]{RESET}"
        else:
            prefix = f"{RED}[FAIL]{RESET}"
        print(f"{prefix} {item.name}: {item.message}")

    if failures:
        print(f"\n{RED}Setup incomplete: {failures} check(s) failed{RESET}")
        return 1

    print(f"\n{CYAN}Setup ready: all checks passed{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
