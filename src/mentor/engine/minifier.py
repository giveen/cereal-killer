from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET


NMAP_OPEN_PORT_RE = re.compile(
    r"^(?P<port>\d+)/(?:tcp|udp)\s+open\s+(?P<service>[a-zA-Z0-9_.-]+)(?:\s+(?P<version>.*))?$",
    re.IGNORECASE,
)
GOBUSTER_PATH_RE = re.compile(
    r"^(?P<path>/\S+)\s+\(Status:\s*(?P<status>\d{3})(?:\)\s*\[Size:\s*(?P<size>\d+)\])?",
    re.IGNORECASE,
)
BLOODHOUND_EDGE_RE = re.compile(
    r"(?P<src>[A-Za-z0-9_.$-]+)\s*[-=]>\s*(?P<dst>[A-Za-z0-9_.$-]+)(?:\s*\((?P<rel>[^)]+)\))?",
    re.IGNORECASE,
)
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def minify_terminal_output(output: str, command: str | None = None, max_items: int = 25) -> str:
    if not output.strip():
        return ""

    command_name = _first_command_word(command or "")
    lowered_command = (command or "").lower()

    if command_name == "nmap":
        if "-oj" in lowered_command or "--json" in lowered_command or _looks_like_json(output):
            return _minify_nmap_json(output, max_items=max_items)
        if "-ox" in lowered_command or "-ox-" in lowered_command or _looks_like_xml(output):
            return _minify_nmap_xml(output, max_items=max_items)
        return _minify_nmap_text(output, max_items=max_items)

    if command_name in {"gobuster", "feroxbuster"}:
        return _minify_web_bruteforce(output, command_name=command_name, max_items=max_items)

    if command_name in {"bloodhound", "bloodhound-python", "bloodhound-python.py"}:
        return _minify_bloodhound(output, max_items=max_items)

    if "linpeas" in command_name or "linpeas" in lowered_command:
        return _minify_linpeas(output, max_items=max_items)

    if command_name == "sqlmap":
        return _minify_sqlmap(output, max_items=max_items)

    return _generic_trim(output, max_items=max_items)


def _first_command_word(command: str) -> str:
    parts = command.strip().split()
    if not parts:
        return ""
    if parts[0] in {"sudo", "doas"} and len(parts) > 1:
        return parts[1].lower()
    return parts[0].lower()


def _looks_like_json(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


def _looks_like_xml(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("<") and ("<nmaprun" in stripped or "<?xml" in stripped)


def _minify_nmap_json(output: str, max_items: int) -> str:
    try:
        payload = json.loads(output)
    except Exception:
        return _minify_nmap_text(output, max_items=max_items)

    hosts = payload if isinstance(payload, list) else [payload]
    rows: list[dict[str, str]] = []

    for host in hosts:
        host_addr = str(host.get("ip", host.get("host", "unknown"))) if isinstance(host, dict) else "unknown"
        ports = host.get("ports", []) if isinstance(host, dict) else []
        for port in ports[:max_items]:
            if not isinstance(port, dict):
                continue
            state = str(port.get("state", ""))
            if state and state != "open":
                continue
            rows.append(
                {
                    "host": host_addr,
                    "port": str(port.get("port", "")),
                    "protocol": str(port.get("protocol", "tcp")),
                    "service": str(port.get("service", "")),
                    "version": str(port.get("version", "")),
                }
            )

    return json.dumps({"nmap_summary": rows[:max_items]}, indent=2)


def _minify_nmap_xml(output: str, max_items: int) -> str:
    try:
        root = ET.fromstring(output)
    except Exception:
        return _minify_nmap_text(output, max_items=max_items)

    rows: list[dict[str, str]] = []
    for host in root.findall("host"):
        addr = host.find("address")
        host_addr = addr.get("addr", "unknown") if addr is not None else "unknown"
        for port in host.findall("./ports/port"):
            state = port.find("state")
            if state is not None and state.get("state") != "open":
                continue
            svc = port.find("service")
            rows.append(
                {
                    "host": host_addr,
                    "port": str(port.get("portid", "")),
                    "protocol": str(port.get("protocol", "tcp")),
                    "service": svc.get("name", "") if svc is not None else "",
                    "version": svc.get("version", "") if svc is not None else "",
                }
            )

    return json.dumps({"nmap_summary": rows[:max_items]}, indent=2)


def _minify_nmap_text(output: str, max_items: int) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    rows: list[str] = []

    for line in lines:
        match = NMAP_OPEN_PORT_RE.search(line)
        if not match:
            continue
        port = match.group("port")
        service = match.group("service")
        version = (match.group("version") or "").strip()
        rows.append(f"{port} {service} {version}".strip())

    if rows:
        return _as_signal_summary("Nmap Open Services", rows[:max_items])

    keep: list[str] = []
    for line in lines:
        lowered = line.lower()
        if "closed" in lowered and ("/tcp" in lowered or "/udp" in lowered):
            continue
        if lowered.startswith("starting nmap") or lowered.startswith("nmap done"):
            continue
        if "host is up" in lowered or "/tcp" in lowered or "/udp" in lowered or "service info" in lowered:
            keep.append(line)
    if not keep:
        keep = lines
    return _as_signal_summary("Nmap Summary", keep[:max_items])


def _minify_web_bruteforce(output: str, command_name: str, max_items: int) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    discovered: list[str] = []

    for line in lines:
        match = GOBUSTER_PATH_RE.search(line)
        if not match:
            continue
        path = match.group("path")
        status = match.group("status")
        size = match.group("size") or "?"
        discovered.append(f"{path} status={status} size={size}")

    if discovered:
        return _as_signal_summary(f"{command_name.title()} Paths", discovered[:max_items])

    for line in lines:
        lowered = line.lower()
        if "status:" in lowered and any(code in lowered for code in ("200", "301", "302", "403", "500")):
            discovered.append(line)
        elif lowered.startswith("/"):
            discovered.append(line)

    return _as_signal_summary(f"{command_name.title()} Paths", discovered[:max_items] or lines[:max_items])


def _minify_sqlmap(output: str, max_items: int) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    keep: list[str] = []
    keywords = (
        "back-end dbms",
        "current user",
        "current database",
        "available databases",
        "parameter",
        "is vulnerable",
        "payload",
        "os-shell",
        "file system",
    )
    for line in lines:
        lowered = line.lower()
        if any(keyword in lowered for keyword in keywords):
            keep.append(line)

    if not keep:
        keep = [line for line in lines if "[info]" in line.lower() or "[critical]" in line.lower()]
    return _as_signal_summary("SQLMap Findings", keep[:max_items] or lines[:max_items])


def _minify_bloodhound(output: str, max_items: int) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    edges: list[str] = []
    principals: list[str] = []
    for line in lines:
        match = BLOODHOUND_EDGE_RE.search(line)
        if match:
            src = match.group("src")
            dst = match.group("dst")
            rel = (match.group("rel") or "linked").strip()
            edges.append(f"{src} -> {dst} ({rel})")
            continue

        lowered = line.lower()
        if "domain" in lowered or "user" in lowered or "group" in lowered or "computer" in lowered:
            principals.append(line)

    items = edges[:max_items] or principals[:max_items] or lines[:max_items]
    return _as_signal_summary("BloodHound Signals", items)


def _minify_linpeas(output: str, max_items: int) -> str:
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    findings: list[str] = []
    for line in lines:
        clean = ANSI_ESCAPE_RE.sub("", line).strip()
        lowered = clean.lower()
        if "red" in lowered or "yellow" in lowered:
            findings.append(clean)
            continue
        if any(keyword in lowered for keyword in ("cve-", "sudo -l", "capabilities", "writable", "nfs", "suid")):
            findings.append(clean)

    deduped: list[str] = []
    seen: set[str] = set()
    for finding in findings:
        if finding in seen:
            continue
        seen.add(finding)
        deduped.append(finding)

    return _as_signal_summary("LinPEAS High-Priority Findings", deduped[:max_items] or lines[:max_items])


def _generic_trim(output: str, max_items: int) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return "\n".join(lines[:max_items])


def _as_markdown_section(title: str, lines: list[str]) -> str:
    body = "\n".join(f"- {line}" for line in lines)
    return f"### {title}\n{body}" if body else f"### {title}\n- no high-signal data"


def _as_signal_summary(title: str, lines: list[str]) -> str:
    return _as_markdown_section(f"Signal Summary: {title}", lines)
