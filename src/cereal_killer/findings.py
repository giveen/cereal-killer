"""Automated findings extraction and tracking.

Extracts passwords, CVEs, ports, and other findings from terminal output
and LLM analysis. Stores findings for export in victory reports.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(slots=True)
class Finding:
    """Individual finding extracted from analysis."""
    type: str  # 'password', 'port', 'cve', 'user', 'service', 'config', 'other'
    value: str
    source: str  # 'terminal_output', 'llm_analysis', 'manual'
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    context: str = ""  # Brief context (e.g., service name, file path)
    
    def to_markdown(self) -> str:
        """Format finding for markdown export."""
        emoji_map = {
            'password': '🔑',
            'port': '🔌',
            'cve': '🐛',
            'user': '👤',
            'service': '⚙️',
            'config': '⚙️',
            'other': '📝',
        }
        emoji = emoji_map.get(self.type, '📌')
        
        line = f"- {emoji} **{self.type.upper()}**: `{self.value}`"
        if self.context:
            line += f" ({self.context})"
        return line


class FindingsExtractor:
    """Extract findings from terminal output and LLM responses."""
    
    # Regex patterns for common findings
    PATTERNS = {
        'password': [
            r'(?:password|passwd|pwd|secret)[\s:=]+([^\s\n]+)',
            r'admin:([^\s\n]+)',
            r'root:([^\s\n]+)',
        ],
        'port': [
            r'(?:port|PORT)[\s:]*(\d+)(?:/(?:tcp|udp))?',
            r'(?:listening on|open)[\s:].*?(\d{4,5})',
        ],
        'cve': [
            r'CVE-\d{4}-\d{4,}',
            r'(?:vulnerability|vuln|exploit).*?(CVE-\d{4}-\d{4,})',
        ],
        'user': [
            r'(?:user|username)[\s:=]+([a-zA-Z0-9_-]+)',
            r'(?:found user|user found)[\s:=]+([a-zA-Z0-9_-]+)',
        ],
    }
    
    def __init__(self) -> None:
        self.findings: list[Finding] = []
        self._seen_values: set[str] = set()  # Deduplication
    
    def extract_from_text(self, text: str, source: str, context: str = "") -> list[Finding]:
        """Extract findings from text using regex patterns."""
        found: list[Finding] = []
        
        for finding_type, patterns in self.PATTERNS.items():
            for pattern in patterns:
                try:
                    matches = re.finditer(pattern, text, re.IGNORECASE)
                    for match in matches:
                        value = match.group(1) if match.groups() else match.group(0)
                        value = value.strip().strip("\"'`")
                        
                        # Avoid duplicates
                        if value not in self._seen_values:
                            finding = Finding(
                                type=finding_type,
                                value=value,
                                source=source,
                                context=context,
                            )
                            found.append(finding)
                            self.findings.append(finding)
                            self._seen_values.add(value)
                except Exception:
                    continue
        
        return found
    
    def add_manual_finding(self, finding_type: str, value: str, context: str = "") -> Finding:
        """Add a manually-identified finding."""
        if value not in self._seen_values:
            finding = Finding(
                type=finding_type,
                value=value,
                source='manual',
                context=context,
            )
            self.findings.append(finding)
            self._seen_values.add(value)
            return finding
        return None
    
    def to_markdown(self) -> str:
        """Export all findings as markdown."""
        if not self.findings:
            return "## 🔍 Findings\n\nNo findings extracted yet.\n"
        
        # Group by type
        grouped: dict[str, list[Finding]] = {}
        for finding in self.findings:
            if finding.type not in grouped:
                grouped[finding.type] = []
            grouped[finding.type].append(finding)
        
        lines = ["## 🔍 Findings\n"]
        
        for finding_type in ['password', 'cve', 'port', 'user', 'service', 'config', 'other']:
            if finding_type not in grouped:
                continue
            
            type_emoji_map = {
                'password': '🔑 Credentials',
                'cve': '🐛 Vulnerabilities',
                'port': '🔌 Ports/Services',
                'user': '👤 Users',
                'service': '⚙️ Services',
                'config': '⚙️ Configuration',
                'other': '📝 Other',
            }
            
            lines.append(f"### {type_emoji_map.get(finding_type, finding_type)}\n")
            for finding in grouped[finding_type]:
                lines.append(finding.to_markdown())
            lines.append("")
        
        return "\n".join(lines)
