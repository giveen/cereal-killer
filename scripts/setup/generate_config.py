#!/usr/bin/env python3
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from check_env import check_nvidia


MODEL_REPO_ID = "HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive"
SUPPORTED_QUANTS = ("IQ2_M", "Q4_K_M", "Q8_K_P")


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def ask_int(prompt: str, default: int) -> int:
    while True:
        value = ask(prompt, str(default))
        try:
            return int(value)
        except ValueError:
            print(f"Invalid integer: {value}")


def ask_yes_no(prompt: str, default_yes: bool = True) -> bool:
    default = "y" if default_yes else "n"
    value = ask(prompt + " [y/n]", default).strip().lower()
    return value in {"y", "yes"}


def choose_value(prompt: str, default: str, accept_defaults: bool) -> str:
    if accept_defaults:
        return default
    return ask(prompt, default)


def choose_int(prompt: str, default: int, accept_defaults: bool) -> int:
    if accept_defaults:
        return default
    return ask_int(prompt, default)


@dataclass(slots=True)
class ModelEntry:
    alias: str
    display_name: str
    model_path: Path
    mmproj_path: Path


@dataclass(slots=True)
class DownloadSelection:
    quant: str
    model_file: str
    mmproj_file: str


def choose_match(
    matches: list[Path],
    label: str,
    accept_defaults: bool = False,
    preferred_name: str | None = None,
) -> str:
    if not matches:
        return ask(f"No {label} auto-detected. Enter {label} filename")

    if preferred_name:
        for path in matches:
            if path.name == preferred_name:
                return path.name

    if accept_defaults:
        return matches[0].name

    print(f"\nDetected {label} candidates:")
    for idx, path in enumerate(matches, start=1):
        print(f"  {idx}. {path.name}")

    selected = ask(f"Choose {label} number", "1")
    try:
        pick = int(selected)
        if 1 <= pick <= len(matches):
            return matches[pick - 1].name
    except ValueError:
        pass
    return matches[0].name


def _model_candidates(model_dir: Path) -> list[Path]:
    return sorted(
        [p for p in model_dir.glob("**/*.gguf") if p.is_file() and "mmproj" not in p.name.lower()]
    )


def _mmproj_candidates(model_dir: Path) -> list[Path]:
    return sorted([p for p in model_dir.glob("**/*mmproj*.gguf") if p.is_file()])


def _is_qwen_vl(name: str) -> bool:
    lowered = name.lower()
    return "qwen" in lowered and "vl" in lowered


def _qwen_vl_family(name: str) -> str:
    lowered = name.lower().replace("_", "-")
    matched = re.search(r"qwen[0-9.]*-vl-[0-9]+b", lowered)
    return matched.group(0) if matched else ""


def _mmproj_matches_qwen_vl(model_file: str, mmproj_file: str) -> bool:
    model_family = _qwen_vl_family(model_file)
    mmproj_family = _qwen_vl_family(mmproj_file)
    if model_family and mmproj_family:
        return model_family == mmproj_family
    return _is_qwen_vl(mmproj_file)


def _compose_service_port(repo_root: Path, service: str, fallback: int) -> int:
    compose_file = repo_root / "docker-compose.yml"
    if not compose_file.exists():
        return fallback

    text = compose_file.read_text(encoding="utf-8", errors="ignore")
    pattern = rf"(?ms)^\s{{2}}{re.escape(service)}:\n.*?^\s{{4}}ports:\n(?:\s{{6}}-\s+\"(\d+):\d+\"\n)+"
    match = re.search(pattern, text)
    if not match:
        return fallback

    first_port = re.search(rf"^\s{{6}}-\s+\"(\d+):\d+\"", match.group(0), flags=re.MULTILINE)
    if not first_port:
        return fallback

    try:
        return int(first_port.group(1))
    except ValueError:
        return fallback


def _detect_llama_server(repo_root: Path) -> Path | None:
    candidates = [
        repo_root / "llama.cpp" / "build" / "bin" / "llama-server",
        repo_root / "third_party" / "llama.cpp" / "build" / "bin" / "llama-server",
        Path.home() / "llama.cpp" / "build" / "bin" / "llama-server",
        Path("/usr/local/bin/llama-server"),
        Path("/usr/bin/llama-server"),
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def _prompt_llama_server(repo_root: Path, accept_defaults: bool) -> Path:
    detected = _detect_llama_server(repo_root)
    if detected is not None:
        if accept_defaults:
            return detected
        use_detected = ask_yes_no(f"Use detected llama-server binary at {detected}?", default_yes=True)
        if use_detected:
            return detected

    while True:
        raw = ask("Enter path to llama-server binary", str(detected) if detected else "")
        path = Path(raw).expanduser().resolve()
        if path.exists() and path.is_file():
            return path
        print(f"llama-server binary not found: {path}")


def _detect_total_vram_gb() -> float | None:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None

    if result.returncode != 0:
        return None

    values: list[float] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            values.append(float(line) / 1024.0)
        except ValueError:
            continue
    if not values:
        return None
    return max(values)


def _recommended_quant(vram_gb: float | None) -> tuple[str, str]:
    if vram_gb is None:
        return "Q4_K_M", "VRAM could not be detected; using balanced default"
    if vram_gb >= 48:
        return "Q8_K_P", f"detected about {vram_gb:.1f} GB VRAM"
    if vram_gb >= 24:
        return "Q4_K_M", f"detected about {vram_gb:.1f} GB VRAM"
    return "IQ2_M", f"detected about {vram_gb:.1f} GB VRAM"


def _choose_quant(default_quant: str, accept_defaults: bool) -> str:
    if accept_defaults:
        return default_quant

    while True:
        quant = ask("Quant to download (IQ2_M/Q4_K_M/Q8_K_P)", default_quant).upper()
        if quant in SUPPORTED_QUANTS:
            return quant
        print(f"Unsupported quant: {quant}")


def _ensure_hf_cli() -> str:
    hf_path = shutil.which("hf")
    if hf_path is not None:
        return hf_path

    print("hf CLI not found. Installing huggingface_hub[cli]...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "huggingface_hub[cli]>=0.31.0"],
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Failed to install huggingface_hub[cli]")

    hf_path = shutil.which("hf")
    if hf_path is None:
        candidate = Path(sys.executable).resolve().parent / "hf"
        if candidate.exists() and candidate.is_file():
            return str(candidate)
        raise RuntimeError("hf CLI still not found after install")
    return hf_path


def _download_hauhaucs_model(model_dir: Path, accept_defaults: bool) -> DownloadSelection:
    vram_gb = _detect_total_vram_gb()
    default_quant, reason = _recommended_quant(vram_gb)
    print(f"Recommended quant: {default_quant} ({reason})")
    quant = _choose_quant(default_quant, accept_defaults)

    hf_cli = _ensure_hf_cli()
    model_dir.mkdir(parents=True, exist_ok=True)
    mmproj_file = "mmproj-Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-f16.gguf"
    model_file = f"Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-{quant}.gguf"

    print(f"Downloading {MODEL_REPO_ID} [{quant}] to {model_dir}...")
    result = subprocess.run(
        [
            hf_cli,
            "download",
            MODEL_REPO_ID,
            model_file,
            mmproj_file,
            "README.md",
            "--local-dir",
            str(model_dir),
        ],
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("hf download failed")

    model_matches = [path for path in _model_candidates(model_dir) if path.name == model_file]
    mmproj_matches = [path for path in _mmproj_candidates(model_dir) if path.name == mmproj_file]
    if not model_matches:
        raise RuntimeError(f"Downloaded files did not include quant {quant}")
    if not mmproj_matches:
        raise RuntimeError("Downloaded files did not include an mmproj .gguf")

    return DownloadSelection(
        quant=quant,
        model_file=model_file,
        mmproj_file=mmproj_file,
    )


def _default_display_name(alias: str, model_file: str) -> str:
    stem = Path(model_file).stem.replace("_", "-")
    return f"{alias} ({stem})"


def _prompt_model_entry(
    model_dir: Path,
    default_alias: str,
    accept_defaults: bool,
    preferred_model_file: str | None = None,
    preferred_mmproj_file: str | None = None,
) -> ModelEntry:
    model_file = choose_match(
        _model_candidates(model_dir),
        "model .gguf",
        accept_defaults=accept_defaults,
        preferred_name=preferred_model_file,
    )
    mmproj_file = choose_match(
        _mmproj_candidates(model_dir),
        "mmproj .gguf",
        accept_defaults=accept_defaults,
        preferred_name=preferred_mmproj_file,
    )
    alias = choose_value("Model alias", default_alias, accept_defaults)

    if _is_qwen_vl(model_file) and not _mmproj_matches_qwen_vl(model_file, mmproj_file):
        print("\nERROR: Qwen-VL model selected but matching mmproj was not found.")
        print(f"Model:  {model_file}")
        print(f"mmproj: {mmproj_file}")
        print("Refusing this model entry because setup cannot be marked ready without a valid projector.")
        raise ValueError("invalid mmproj match")

    display_name = choose_value("Display name", _default_display_name(alias, model_file), accept_defaults)
    return ModelEntry(
        alias=alias,
        display_name=display_name,
        model_path=(model_dir / model_file).resolve(),
        mmproj_path=(model_dir / mmproj_file).resolve(),
    )


def write_llama_swap_config(
    target: Path,
    llama_server_path: Path,
    models: list[ModelEntry],
    gpu_layers: int,
    ctx_size: int,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["listen: 0.0.0.0:8000", "models:"]
    for model in models:
        lines.extend(
            [
                f"  {model.alias}:",
                f"    name: \"{model.display_name}\"",
                "    cmd: |",
                f"      {llama_server_path} \\",
                f"      --model {model.model_path} \\",
                f"      --mmproj {model.mmproj_path} \\",
                "      --port ${PORT} \\",
                f"      --gpu-layers {gpu_layers} \\",
                f"      --ctx-size {ctx_size} \\",
                "      --batch-size 2048 \\",
                "      --ubatch-size 1024 \\",
                "      --flash-attn on \\",
                "      --mlock",
                "    ttl: 3600",
            ]
        )
    content = "\n".join(lines) + "\n"
    target.write_text(content, encoding="utf-8")


def write_env(
    target: Path,
    llm_base_url: str,
    model_alias: str,
    screenshots_dir: Path,
    searxng_base_url: str,
    crawl4ai_base_url: str,
) -> None:
    content = (
        f"LLM_BASE_URL={llm_base_url}\n"
        f"LLM_MODEL={model_alias}\n"
        "LLM_API_KEY=not-needed\n"
        "REASONING_PARSER=qwen3\n"
        "MAX_MODEL_LEN=262144\n"
        "REDIS_URL=redis://localhost:6379\n"
        f"SEARXNG_BASE_URL={searxng_base_url}\n"
        f"CRAWL4AI_BASE_URL={crawl4ai_base_url}\n"
        f"SCREENSHOTS_DIR={screenshots_dir}\n"
    )
    target.write_text(content, encoding="utf-8")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    expected_markers = [repo_root / "pyproject.toml", repo_root / "src" / "cereal_killer"]
    if not all(path.exists() for path in expected_markers):
        print("ERROR: Could not verify cereal-killer repository structure.")
        print(f"Expected root markers under: {repo_root}")
        return 2

    if Path.cwd().resolve() != repo_root.resolve():
        print(f"WARN: Run this from repo root for predictable paths: cd {repo_root}")

    default_model_dir = str(Path.home() / "models")
    searxng_port = _compose_service_port(repo_root, "searxng", 18080)
    crawl4ai_port = _compose_service_port(repo_root, "crawl4ai", 11235)
    nvidia_check = check_nvidia()
    gpu_layers_default = 99 if nvidia_check.status == "PASS" else 0

    print("== Gibson Config Generator ==")
    accept_defaults = ask_yes_no("Use default settings?", default_yes=True)
    model_dir = Path(choose_value("Model directory", default_model_dir, accept_defaults)).expanduser().resolve()
    if not model_dir.exists():
        if ask_yes_no(f"Model directory missing: {model_dir}. Create it?", default_yes=True):
            model_dir.mkdir(parents=True, exist_ok=True)
        else:
            print("ERROR: Model directory must exist or be creatable.")
            return 2
    while not model_dir.is_dir():
        print(f"Directory not found: {model_dir}")
        model_dir = Path(ask("Model directory", default_model_dir)).expanduser().resolve()

    download_selection: DownloadSelection | None = None
    has_local_models = bool(_model_candidates(model_dir) and _mmproj_candidates(model_dir))
    download_now = ask_yes_no(
        "Download HauhauCS Qwen3.6 recommended model now?",
        default_yes=accept_defaults or not has_local_models,
    )
    if download_now:
        try:
            download_selection = _download_hauhaucs_model(model_dir, accept_defaults)
        except RuntimeError as exc:
            print(f"ERROR: {exc}")
            return 2

    llama_config_target = repo_root / "config" / "llama-swap" / "config.yaml"
    llama_server_path = _prompt_llama_server(repo_root, accept_defaults)
    gpu_layers = choose_int("GPU offload layers (gpu-layers)", gpu_layers_default, accept_defaults)
    if gpu_layers == 99 and nvidia_check.status != "PASS":
        print("ERROR: --gpu-layers 99 requires a passing NVIDIA check.")
        return 2
    ctx_size = choose_int("Context size (ctx_size)", 65536, accept_defaults)
    llm_base_url = choose_value("LLM_BASE_URL for containers", "http://host.docker.internal:8000/v1", accept_defaults)
    searxng_base_url = choose_value("SEARXNG_BASE_URL", f"http://localhost:{searxng_port}", accept_defaults)
    crawl4ai_base_url = choose_value("CRAWL4AI_BASE_URL", f"http://localhost:{crawl4ai_port}", accept_defaults)
    screenshots_dir = Path(choose_value("Screenshots directory", "/tmp/screenshots", accept_defaults)).expanduser().resolve()
    if not screenshots_dir.exists():
        if ask_yes_no(f"Screenshots dir missing: {screenshots_dir}. Create it?", default_yes=True):
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            print(f"Created screenshots directory: {screenshots_dir}")
        else:
            print("ERROR: Screenshots directory must exist or be creatable.")
            return 2

    env_target = repo_root / ".env"

    models: list[ModelEntry] = []
    model_count = 0
    while True:
        model_count += 1
        try:
            entry = _prompt_model_entry(
                model_dir,
                default_alias=(
                    "qwen3.6-35b"
                    if model_count == 1 and download_selection is not None
                    else ("qwen-vl" if model_count == 1 else f"model-{model_count}")
                ),
                accept_defaults=accept_defaults,
                preferred_model_file=download_selection.model_file if model_count == 1 and download_selection else None,
                preferred_mmproj_file=download_selection.mmproj_file if model_count == 1 and download_selection else None,
            )
        except ValueError:
            return 2
        models.append(entry)

        if accept_defaults:
            break
        if not ask_yes_no("Add another model to llama-swap config?", default_yes=False):
            break

    write_llama_swap_config(
        llama_config_target,
        llama_server_path,
        models,
        gpu_layers,
        ctx_size,
    )
    write_env(
        env_target,
        llm_base_url,
        models[0].alias,
        screenshots_dir,
        searxng_base_url,
        crawl4ai_base_url,
    )

    print("\nGenerated files:")
    print(f"- {llama_config_target}")
    print(f"- {env_target}")
    print("\n\033[96m[ COMPLETED: Gibson Neurons Calibrated ]\033[0m")
    print("\nNext steps:")
    print("1. Copy config/llama-swap/config.yaml to /etc/llama-swap/config.yaml")
    print("2. Restart llama-swap service")
    print("3. Run python scripts/setup/check_env.py --model-dir <your-model-dir>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
