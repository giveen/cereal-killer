import unittest

from cereal_killer.observer import filter_context_commands, parse_history_lines


class ObserverTests(unittest.TestCase):
    def test_parse_zsh_extended_history(self) -> None:
        commands = parse_history_lines(": 1700000000:0;cd /tmp\n: 1700000001:0;ls -la")
        self.assertEqual(commands, ["cd /tmp", "ls -la"])

    def test_filter_limits_to_50(self) -> None:
        commands = [f"cd /tmp/project && cmd {i}" for i in range(80)]
        filtered = filter_context_commands(commands, "/tmp/project")
        self.assertEqual(len(filtered), 50)
        self.assertEqual(filtered[0], "cd /tmp/project && cmd 30")


if __name__ == "__main__":
    unittest.main()
