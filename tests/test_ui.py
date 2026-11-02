import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bnet_steam import ui


class Tty(io.StringIO):
    encoding = "utf-8"

    def isatty(self):
        return True


def term(tty=False, verbose=False, **kw):
    out = Tty() if tty else io.StringIO()
    with mock.patch.dict(os.environ, {"TERM": "xterm"}), \
            mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("NO_COLOR", None)
        return ui.Terminal(verbose, out=out, **kw)


class Drawing(unittest.TestCase):
    def test_plain_output_has_no_escape_codes_and_is_ascii(self):
        t = term()
        t.screen("Adding", 2, 4)
        t.box(["hello"])
        t.good("done")
        out = t.out.getvalue()
        self.assertNotIn("\033", out)
        self.assertTrue(out.isascii())
        self.assertIn("Step 2 of 4", out)
        self.assertIn("OK done", out)

    def test_a_terminal_gets_colour_clearing_and_unicode(self):
        t = term(tty=True)
        t.screen("Adding", 2, 4)
        t.box(["hello"])
        out = t.out.getvalue()
        self.assertIn("\033[2J", out)           # fresh screen per step
        self.assertIn("\033[38;5;39m", out)
        self.assertIn("╭", out)
        self.assertIn("● ● ○ ○", out.replace("\033[38;5;39m", "").replace("\033[38;5;245m", "")
                      .replace("\033[0m", ""))

    def test_no_color_is_respected(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1", "TERM": "xterm"}):
            t = ui.Terminal(out=Tty())
        t.good("x")
        self.assertNotIn("\033[38", t.out.getvalue())

    def test_boxes_wrap_and_stay_aligned(self):
        t = term()
        t.box(["word " * 60, "1. a numbered item that is long " * 6])
        lines = [l for l in t.out.getvalue().splitlines() if l.strip()]
        self.assertEqual(len({len(l) for l in lines}), 1)
        self.assertLessEqual(len(lines[0]), 80)

    def test_wrapped_list_items_hang_under_their_text(self):
        t = term()
        t.say("1. " + "word " * 40)
        second = t.out.getvalue().splitlines()[1]
        self.assertTrue(second.startswith("     word"))


class Running(unittest.TestCase):
    def test_what_a_command_prints_is_hidden_and_saved_to_the_log(self):
        with tempfile.TemporaryDirectory() as d:
            t = term(log_path=Path(d) / "logs" / "last.log")
            self.assertEqual(t.run("Working", lambda: (print("secret detail"), 7)[1]), 7)
            self.assertNotIn("secret detail", t.out.getvalue())
            self.assertIn("Working", t.out.getvalue())
            self.assertIn("secret detail", (Path(d) / "logs" / "last.log").read_text())

    def test_verbose_shows_it(self):
        t = term(verbose=True)
        with mock.patch("sys.stdout", new=io.StringIO()) as shown:
            t.run("Working", lambda: print("secret detail"))
        self.assertIn("secret detail", shown.getvalue())

    def test_a_failure_still_saves_the_log_and_propagates(self):
        with tempfile.TemporaryDirectory() as d:
            t = term(log_path=Path(d) / "last.log")

            def boom():
                print("about to fail")
                raise ValueError("bad")
            with self.assertRaises(ValueError):
                t.run("Working", boom)
            self.assertIn("about to fail", (Path(d) / "last.log").read_text())

    def test_progress_lines_change_the_status_text(self):
        t = term(tty=True)
        seen = []
        real = t.spinner

        def spying(message):
            cm = real(message)
            state = cm.__enter__()
            seen.append(state)
            return type("C", (), {"__enter__": lambda s: state,
                                  "__exit__": lambda s, *a: cm.__exit__(*a)})()
        t.spinner = spying
        t.run("Working", lambda: print("Closing Steam..."), {"Closing": "Closing Steam now"})
        self.assertEqual(seen[0].text, "Closing Steam now")

    def test_the_spinner_hides_then_restores_the_cursor(self):
        t = term(tty=True)
        with t.spinner("x"):
            pass
        out = t.out.getvalue()
        self.assertTrue(out.index("\033[?25l") < out.index("\033[?25h"))
        self.assertTrue(out.endswith("\033[?25h"))


class Asking(unittest.TestCase):
    def test_confirm_defaults_to_yes_on_a_bare_enter(self):
        for typed, want in (("", True), ("y", True), ("YES", True), ("n", False), ("no", False)):
            with mock.patch("builtins.input", return_value=typed):
                self.assertEqual(term().confirm("ok?"), want, typed)

    def test_confirm_can_default_to_no(self):
        with mock.patch("builtins.input", return_value=""):
            self.assertFalse(term().confirm("remove?", default=False))


class Waiting(unittest.TestCase):
    def test_returns_when_the_condition_comes_true(self):
        flips = iter([False, False, True])
        with mock.patch("sys.stdin.isatty", return_value=False), mock.patch("time.sleep"):
            self.assertTrue(term().wait_for(lambda: next(flips), 60, "w", poll=0))

    def test_gives_up_after_the_time_limit(self):
        clock = iter(range(0, 1000, 5))
        with mock.patch("sys.stdin.isatty", return_value=False), mock.patch("time.sleep"), \
                mock.patch("time.time", side_effect=lambda: next(clock)):
            self.assertFalse(term().wait_for(lambda: False, 10, "w", poll=0))

    def test_pressing_enter_rechecks_and_reports_when_it_is_not_ready_yet(self):
        said = []
        flips = iter([False, False, True])
        with mock.patch("sys.stdin") as stdin, \
                mock.patch("select.select", return_value=([stdin], [], [])):
            stdin.isatty.return_value = True
            stdin.readline.return_value = "\n"
            ok = term().wait_for(lambda: next(flips), 60, "w", poll=1000,
                                 on_enter=lambda: said.append("not yet"))
        self.assertTrue(ok)
        self.assertEqual(said, ["not yet"])


if __name__ == "__main__":
    unittest.main()
