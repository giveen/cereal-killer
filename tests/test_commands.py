"""Tests for the slash-command router and detect_box_cd."""
from __future__ import annotations

import asyncio
import unittest

from cereal_killer.config import Settings
from mentor.engine.commands import (
    CommandResult,
    dispatch,
    is_slash_command,
    parse_slash_command,
)
from mentor.observer.stalker import detect_box_cd, detect_box_host


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

class SlashCommandParsingTests(unittest.TestCase):
    def test_is_slash_command_true_for_slash_prefix(self) -> None:
        self.assertTrue(is_slash_command("/box lame"))
        self.assertTrue(is_slash_command("/help"))
        self.assertTrue(is_slash_command("/new-box knife"))

    def test_is_slash_command_false_for_normal_text(self) -> None:
        self.assertFalse(is_slash_command("nmap -sV 10.10.10.3"))
        self.assertFalse(is_slash_command(""))
        self.assertFalse(is_slash_command("/"))  # lone slash, no verb

    def test_parse_slash_command_splits_verb_and_args(self) -> None:
        verb, args = parse_slash_command("/box Lame")
        self.assertEqual(verb, "box")
        self.assertEqual(args, ["Lame"])

    def test_parse_slash_command_normalises_verb_to_lowercase(self) -> None:
        verb, _ = parse_slash_command("/BOX lame")
        self.assertEqual(verb, "box")

    def test_parse_slash_command_no_args(self) -> None:
        verb, args = parse_slash_command("/help")
        self.assertEqual(verb, "help")
        self.assertEqual(args, [])


# ---------------------------------------------------------------------------
# Dispatch integration
# ---------------------------------------------------------------------------

def _run(coro) -> object:
    return asyncio.run(coro)


class DispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(redis_url="redis://localhost:59999")

    def test_dispatch_returns_none_for_non_command(self) -> None:
        result = _run(dispatch("nmap -sV 10.10.10.3", None, self.settings))
        self.assertIsNone(result)

    def test_dispatch_help_returns_result(self) -> None:
        result = _run(dispatch("/help", None, self.settings))
        self.assertIsInstance(result, CommandResult)
        assert result is not None
        self.assertIn("/box", result.message)

    def test_dispatch_unknown_command_returns_error(self) -> None:
        result = _run(dispatch("/xyzzy something", None, self.settings))
        self.assertIsInstance(result, CommandResult)
        assert result is not None
        self.assertIn("Unknown command", result.message)

    def test_dispatch_box_no_args_returns_usage(self) -> None:
        result = _run(dispatch("/box", None, self.settings))
        assert result is not None
        self.assertIn("Usage", result.message)

    def test_dispatch_box_sets_new_target(self) -> None:
        result = _run(dispatch("/box lame", None, self.settings))
        assert result is not None
        self.assertEqual(result.new_target, "lame")
        self.assertTrue(result.reset_phase)
        self.assertIsNotNone(result.system_prompt_addendum)
        self.assertIn("LAME", result.system_prompt_addendum)

    def test_dispatch_new_box_sets_exploration_mode(self) -> None:
        result = _run(dispatch("/new-box knife", None, self.settings))
        assert result is not None
        self.assertEqual(result.new_target, "knife")
        self.assertTrue(result.exploration_mode)
        self.assertTrue(result.reset_phase)
        self.assertIn("EXPLORATION MODE", result.system_prompt_addendum)

    def test_dispatch_newbox_alias(self) -> None:
        result = _run(dispatch("/newbox knife", None, self.settings))
        assert result is not None
        self.assertTrue(result.exploration_mode)

    def test_dispatch_box_exploration_mode_false(self) -> None:
        result = _run(dispatch("/box lame", None, self.settings))
        assert result is not None
        self.assertFalse(result.exploration_mode)

    def test_dispatch_loot_alias_returns_sentinel(self) -> None:
        result = _run(dispatch("/loot", None, self.settings))
        assert result is not None
        self.assertEqual(result.session_prefix, "__loot__")

    def test_dispatch_exit_returns_sentinel(self) -> None:
        result = _run(dispatch("/exit", None, self.settings))
        assert result is not None
        self.assertEqual(result.session_prefix, "__exit__")

    def test_dispatch_quit_alias_returns_sentinel(self) -> None:
        result = _run(dispatch("/quit", None, self.settings))
        assert result is not None
        self.assertEqual(result.session_prefix, "__exit__")

    def test_dispatch_box_case_insensitive_name(self) -> None:
        result = _run(dispatch("/box LAME", None, self.settings))
        assert result is not None
        self.assertEqual(result.new_target, "lame")


# ---------------------------------------------------------------------------
# detect_box_cd
# ---------------------------------------------------------------------------

class DetectBoxCdTests(unittest.TestCase):
    def test_returns_name_for_simple_cd(self) -> None:
        self.assertEqual(detect_box_cd("cd lame"), "lame")
        self.assertEqual(detect_box_cd("cd Knife"), "knife")
        self.assertEqual(detect_box_cd("cd Optimum"), "optimum")

    def test_returns_last_component_for_relative_path(self) -> None:
        self.assertEqual(detect_box_cd("cd htb/Lame"), "lame")
        self.assertEqual(detect_box_cd("cd ~/htb/machines/Forest"), "forest")

    def test_ignores_absolute_paths(self) -> None:
        self.assertIsNone(detect_box_cd("cd /home/user/htb/lame"))
        self.assertIsNone(detect_box_cd("cd /tmp"))

    def test_ignores_shell_navigation_tokens(self) -> None:
        self.assertIsNone(detect_box_cd("cd .."))
        self.assertIsNone(detect_box_cd("cd -"))
        self.assertIsNone(detect_box_cd("cd ~"))
        self.assertIsNone(detect_box_cd("cd $HOME"))

    def test_ignores_all_digit_directory_names(self) -> None:
        self.assertIsNone(detect_box_cd("cd 10"))
        self.assertIsNone(detect_box_cd("cd 8080"))

    def test_returns_none_for_non_cd_commands(self) -> None:
        self.assertIsNone(detect_box_cd("nmap -sV 10.10.10.3"))
        self.assertIsNone(detect_box_cd("ls -la"))
        self.assertIsNone(detect_box_cd(""))

    def test_handles_quoted_paths(self) -> None:
        self.assertEqual(detect_box_cd("cd 'lame'"), "lame")
        self.assertEqual(detect_box_cd('cd "Knife"'), "knife")

    def test_long_name_over_limit_ignored(self) -> None:
        long_name = "a" * 25  # > 24 chars
        self.assertIsNone(detect_box_cd(f"cd {long_name}"))


class DetectBoxHostTests(unittest.TestCase):
    def test_detects_machine_from_htb_hostname(self) -> None:
        self.assertEqual(detect_box_host("nmap -sV cap.htb"), "cap")
        self.assertEqual(detect_box_host("curl http://Falafel.htb/login"), "falafel")

    def test_ignores_common_local_aliases(self) -> None:
        self.assertIsNone(detect_box_host("curl http://localhost:8080"))
        self.assertIsNone(detect_box_host("ping host.htb"))

    def test_returns_none_when_no_htb_hostname(self) -> None:
        self.assertIsNone(detect_box_host("nmap -sV 10.10.11.20"))
        self.assertIsNone(detect_box_host("ls -la"))


if __name__ == "__main__":
    unittest.main()
