"""FindingsWidget: scrollable checklist of extracted security findings."""
from __future__ import annotations

from textual.containers import Container
from textual.widgets import Static


class FindingsWidget(Static):
    """Displays extracted findings (passwords, CVEs, ports) in a scrollable list."""
    
    DEFAULT_CSS = """
    FindingsWidget {
        height: 1fr;
        overflow: auto;
        border: tall #4fd2ff;
        background: #08121d;
        color: #d6efff;
        padding: 1;
    }
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.findings: list[str] = []
    
    def add_finding(self, finding_type: str, value: str, context: str = "") -> None:
        """Add a finding to the list."""
        emoji_map = {
            'password': '🔑',
            'port': '🔌',
            'cve': '🐛',
            'user': '👤',
            'service': '⚙️',
            'config': '⚙️',
            'other': '📝',
        }
        emoji = emoji_map.get(finding_type, '📌')
        
        line = f"{emoji} {finding_type.upper()}: {value}"
        if context:
            line += f" ({context})"
        
        self.findings.append(line)
        self._update_display()
    
    def clear_findings(self) -> None:
        """Clear all findings."""
        self.findings = []
        self._update_display()
    
    def _update_display(self) -> None:
        """Update the widget display."""
        if not self.findings:
            self.update("📋 No findings yet.\n\nPasswords, CVEs, and ports\nfound will appear here.")
        else:
            content = "📋 Findings\n\n" + "\n".join(self.findings)
            self.update(content)
