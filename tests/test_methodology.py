"""Tests for mentor.engine.methodology – command-order auditor."""
from __future__ import annotations

import unittest

from mentor.engine.methodology import audit_command, _WARNING


class TestAuditCommandNonExploit(unittest.TestCase):
    def test_regular_command_returns_none(self):
        self.assertIsNone(audit_command("ls -la /etc", []))

    def test_nmap_returns_none(self):
        self.assertIsNone(audit_command("nmap -sV 10.10.10.1", []))

    def test_gobuster_returns_none(self):
        self.assertIsNone(audit_command("gobuster dir -u http://10.10.10.1", []))


class TestAuditCommandExploitWithoutRecon(unittest.TestCase):
    def test_searchsploit_no_history_warns(self):
        self.assertEqual(audit_command("searchsploit Apache 2.4", []), _WARNING)

    def test_msfconsole_no_history_warns(self):
        self.assertEqual(audit_command("msfconsole", []), _WARNING)

    def test_sqlmap_no_history_warns(self):
        self.assertEqual(audit_command("sqlmap -u http://10.10.10.1/login", []), _WARNING)

    def test_python_exploit_no_history_warns(self):
        self.assertEqual(audit_command("python3 exploit.py", []), _WARNING)


class TestAuditCommandExploitWithSufficientRecon(unittest.TestCase):
    def test_searchsploit_after_nmap_ok(self):
        history = ["nmap -sV -sC 10.10.10.1"]
        self.assertIsNone(audit_command("searchsploit Apache 2.4", history))

    def test_msfconsole_after_gobuster_ok(self):
        history = ["gobuster dir -u http://10.10.10.1 -w /usr/share/wordlists/dirb/common.txt"]
        self.assertIsNone(audit_command("msfconsole", history))

    def test_sqlmap_after_enum4linux_ok(self):
        history = ["enum4linux -a 10.10.10.1"]
        self.assertIsNone(audit_command("sqlmap -u http://10.10.10.1/login", history))

    def test_exploit_after_smbclient_ok(self):
        history = ["smbclient -L //10.10.10.1"]
        self.assertIsNone(audit_command("searchsploit samba 3.0", history))
