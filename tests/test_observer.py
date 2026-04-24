import unittest

from cereal_killer.config import Settings
from cereal_killer.observer import (
    detect_feedback_signal,
    filter_context_commands,
    is_technical_command,
    needs_structured_output_hint,
    parse_history_lines,
)

# Default settings instance for is_technical_command tests
_default_settings = Settings()


class ObserverTests(unittest.TestCase):
    def test_parse_zsh_extended_history(self) -> None:
        commands = parse_history_lines(": 1700000000:0;cd /tmp\n: 1700000001:0;ls -la")
        self.assertEqual(commands, ["cd /tmp", "ls -la"])

    def test_filter_limits_to_50(self) -> None:
        commands = [f"cd /tmp/project && cmd {i}" for i in range(80)]
        filtered = filter_context_commands(commands, "/tmp/project")
        self.assertEqual(len(filtered), 50)
        self.assertEqual(filtered[0], "cd /tmp/project && cmd 30")
        self.assertEqual(filtered[-1], "cd /tmp/project && cmd 79")

    def test_filter_handles_empty_history(self) -> None:
        self.assertEqual(filter_context_commands([], "/tmp/project"), [])

    def test_filter_returns_all_when_under_limit(self) -> None:
        commands = ["pwd", "ls", "cat notes.txt"]
        self.assertEqual(filter_context_commands(commands, "/tmp/project"), commands)

    def test_detects_technical_tool_commands(self) -> None:
        self.assertTrue(is_technical_command("nmap -sV 10.10.10.10", settings=_default_settings))
        self.assertTrue(is_technical_command("sudo gobuster dir -u http://target", settings=_default_settings))
        self.assertFalse(is_technical_command("echo hello", settings=_default_settings))

    def test_detects_failure_feedback_signal(self) -> None:
        self.assertEqual(detect_feedback_signal("[-] Access denied for share"), "failure")

    def test_bare_failed_word_does_not_trigger(self) -> None:
        # "failed" alone must NOT fire — it matches AI explanations like
        # "The authentication failed because...".  Only specific compound forms
        # (e.g. "authentication failed") are in the failure markers list.
        self.assertIsNone(detect_feedback_signal("The exploit failed."))
        self.assertIsNone(detect_feedback_signal("Connection failed"))
        self.assertIsNone(detect_feedback_signal("failed"))

    def test_prose_line_does_not_trigger(self) -> None:
        # Lines that look like AI prose must be ignored even if they contain
        # failure keywords — prevents the Sarcastic Singularity.
        self.assertIsNone(detect_feedback_signal("It looks like the connection was refused by the target."))
        self.assertIsNone(detect_feedback_signal("[red]Zero Cool>[/red] access denied on that share"))
        self.assertIsNone(detect_feedback_signal("You should try running the exploit again with a different payload."))

    def test_long_line_does_not_trigger(self) -> None:
        # Lines over 300 chars are prose / multi-sentence output, not terminal
        # error one-liners.  They must not trigger even if keywords are present.
        long_line = "access denied " + "x" * 290
        self.assertIsNone(detect_feedback_signal(long_line))

    def test_detects_no_session_created_failure_signal(self) -> None:
        line = "Exploit completed, but no session was created"
        self.assertEqual(detect_feedback_signal(line), "failure")

    def test_detects_success_feedback_signal(self) -> None:
        self.assertEqual(detect_feedback_signal("id: uid=0(root) gid=0(root)"), "success")

    def test_json_hint_trigger_for_unstructured_nmap(self) -> None:
        self.assertTrue(needs_structured_output_hint("nmap -sV 10.10.10.10"))
        self.assertFalse(needs_structured_output_hint("nmap -sV -oJ scan.json 10.10.10.10"))


if __name__ == "__main__":
    unittest.main()
