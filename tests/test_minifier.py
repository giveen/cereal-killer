import unittest

from mentor.engine.minifier import minify_terminal_output


class MinifierTests(unittest.TestCase):
    def test_minify_nmap_text_removes_closed_noise(self) -> None:
        raw = """
        Starting Nmap
        22/tcp open ssh OpenSSH 9.2
        80/tcp closed http
        443/tcp open https nginx 1.22
        Nmap done
        """
        result = minify_terminal_output(raw, command="nmap -sV 10.10.10.10")
        self.assertIn("22 ssh openssh 9.2", result.lower())
        self.assertIn("443 https nginx 1.22", result.lower())
        self.assertNotIn("closed", result.lower())

    def test_minify_gobuster_paths(self) -> None:
        raw = """
        /admin (Status: 301)
        /login (Status: 200)
        random banner line
        """
        result = minify_terminal_output(raw, command="gobuster dir -u http://target")
        self.assertIn("/admin", result)
        self.assertIn("/login", result)

    def test_minify_sqlmap_findings(self) -> None:
        raw = """
        [INFO] back-end DBMS: MySQL >= 5.0
        [INFO] current user: 'root@localhost'
        [CRITICAL] connection reset
        """
        result = minify_terminal_output(raw, command="sqlmap -u http://target")
        self.assertIn("back-end dbms", result.lower())
        self.assertIn("current user", result.lower())

    def test_minify_bloodhound_signals(self) -> None:
        raw = """
        USER1 -> DOMAIN ADMINS (MemberOf)
        USER2 -> SERVER01$ (AdminTo)
        banner noise line
        """
        result = minify_terminal_output(raw, command="bloodhound-python -u user")
        self.assertIn("bloodhound signals", result.lower())
        self.assertIn("user1 -> domain", result.lower())

    def test_minify_linpeas_high_priority_findings(self) -> None:
        raw = """
        [*] Random banner section
        RED: /etc/shadow is readable by current user
        YELLOW: sudo -l shows NOPASSWD for /usr/bin/vim
        harmless info line
        """
        result = minify_terminal_output(raw, command="./linpeas.sh")
        self.assertIn("linpeas high-priority", result.lower())
        self.assertIn("red: /etc/shadow", result.lower())
        self.assertIn("yellow: sudo -l", result.lower())


if __name__ == "__main__":
    unittest.main()
