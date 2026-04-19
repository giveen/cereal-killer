import unittest

from mentor.ui.phase import detect_phase


class PhaseAnalyzerTests(unittest.TestCase):
    def test_detect_recon_phase(self) -> None:
        self.assertEqual(detect_phase(["nmap -sC -sV 10.10.10.10"]), "[RECON]")

    def test_detect_recon_masscan_phase(self) -> None:
        self.assertEqual(detect_phase(["masscan 10.10.10.10 -p1-65535"]), "[RECON]")

    def test_detect_enumeration_phase(self) -> None:
        self.assertEqual(detect_phase(["gobuster dir -u http://target -w words.txt"]), "[ENUMERATION]")

    def test_detect_nikto_enumeration_phase(self) -> None:
        self.assertEqual(detect_phase(["nikto -h http://target"]), "[ENUMERATION]")

    def test_detect_dirbuster_enumeration_phase(self) -> None:
        self.assertEqual(detect_phase(["dirbuster -u http://target -w words.txt"]), "[ENUMERATION]")

    def test_detect_exploitation_phase(self) -> None:
        history = ["nmap -sV 10.10.10.10", "python3 -c 'import pty; pty.spawn(\"/bin/bash\")'"]
        self.assertEqual(detect_phase(history), "[EXPLOITATION]")

    def test_detect_python_exploit_string_phase(self) -> None:
        history = ["python3 exploit.py --target 10.10.10.10 --payload revshell"]
        self.assertEqual(detect_phase(history), "[EXPLOITATION]")

    def test_detect_post_exploitation_phase(self) -> None:
        history = ["msfconsole", "whoami", "id"]
        self.assertEqual(detect_phase(history), "[POST-EXPLOITATION]")

    def test_detect_idle_when_no_match(self) -> None:
        self.assertEqual(detect_phase(["ls -la", "pwd"]), "[IDLE]")


if __name__ == "__main__":
    unittest.main()
