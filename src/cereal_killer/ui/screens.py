from __future__ import annotations
import re

CODE_BLOCK_RE = re.compile(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", re.DOTALL)
PROBABLE_COMMAND_RE = re.compile(
    r"^(?:\$\s*)?(?:sudo\s+)?(?:"
    r"nmap|curl|wget|ffuf|gobuster|feroxbuster|dirb|dirsearch|nikto|sqlmap|"
    r"nc|netcat|python|python3|bash|sh|ssh|ftp|smbclient|rpcclient|redis-cli|"
    r"docker(?:\s+compose)?|git|ls|cat|grep|find|chmod|chown|echo|export|cd|"
    r"cp|mv|awk|sed"
    r")\b"
)
