"""Friendly terminal screens for the guided setup: one step per screen, a progress bar, boxed
messages, a spinner while work happens. The detailed log of what the commands did is kept out of
sight (and saved to a file) unless -v is given.

Stays plain when output is not a terminal (pipes, tests) or NO_COLOR is set: no colour, no
clearing, no animation, just the same words.
"""
import contextlib
import io
import os
import select
import shutil
import sys
import textwrap
import threading
import time

ACCENT, DIM, OK, WARN, BAD = "38;5;39", "38;5;245", "38;5;78", "38;5;221", "38;5;203"
FRAMES_UNICODE, FRAMES_ASCII = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", "|/-\\"


class _Sink(io.TextIOBase):
    """Collects what a command prints; calls on_line(line) for each complete line."""

    def __init__(self, on_line=None):
        self.lines, self._part, self.on_line = [], "", on_line

    def writable(self):
        return True

    def write(self, s):
        self._part += s
        *done, self._part = self._part.split("\n")
        for line in done:
            self.lines.append(line)
            if self.on_line:
                self.on_line(line)
        return len(s)

    def text(self):
        return "\n".join(self.lines + ([self._part] if self._part else []))


class Terminal:
    def __init__(self, verbose=False, out=None, log_path=None):
        self.out = out or sys.stdout
        self.verbose, self.log_path = verbose, log_path
        self.fancy = bool(getattr(self.out, "isatty", lambda: False)()) and \
            os.environ.get("TERM", "") != "dumb"
        self.color = self.fancy and "NO_COLOR" not in os.environ
        enc = (getattr(self.out, "encoding", None) or "").lower()
        self.unicode = "utf" in enc
        self.width = max(40, min(shutil.get_terminal_size((80, 24)).columns - 4, 76))
        self.log = []

    # --- drawing ---------------------------------------------------------------

    def paint(self, text, *codes):
        return f"\033[{';'.join(codes)}m{text}\033[0m" if self.color and codes else text

    def _write(self, s):
        self.out.write(s)
        self.out.flush()

    def blank(self):
        self._write("\n")

    def clear(self):
        if self.fancy:
            self._write("\033[2J\033[H")

    def screen(self, title, step=None, total=None, done=()):
        """Start a fresh screen: app name, optional "step n of m" progress, the title, and a
        checklist of what has already been finished."""
        self.clear()
        self._write("\n  " + self.paint("Battle.net for Steam", "1", ACCENT) + "\n")
        if step:
            on, off = ("●", "○") if self.unicode else ("*", "-")
            dots = " ".join(self.paint(on if i < step else off, ACCENT if i < step else DIM)
                            for i in range(total))
            self._write("  " + dots + "  " + self.paint(f"Step {step} of {total}", DIM) + "\n")
        self._write("\n  " + self.paint(title, "1") + "\n")
        self._write("  " + self.paint(("─" if self.unicode else "-") * min(len(title), 40), DIM)
                    + "\n\n")
        for line in done:
            self.good(line)
        if done:
            self.blank()

    def say(self, text="", indent=2):
        for para in str(text).split("\n"):
            lines = textwrap.wrap(para, self.width - indent, subsequent_indent=_hang(para)) \
                or [""]
            for line in lines:
                self._write(" " * indent + line + "\n")

    def box(self, lines, tone=ACCENT):
        """Text in a rounded box."""
        inner = self.width - 4
        wrapped = []
        for para in ([lines] if isinstance(lines, str) else lines):
            wrapped += textwrap.wrap(para, inner, subsequent_indent=_hang(para)) or [""]
        if not self.unicode:
            tl, tr, bl, br, h, v = "+", "+", "+", "+", "-", "|"
        else:
            tl, tr, bl, br, h, v = "╭", "╮", "╰", "╯", "─", "│"
        edge = lambda a, b: "  " + self.paint(a + h * (inner + 2) + b, tone) + "\n"
        self._write(edge(tl, tr))
        for line in wrapped:
            self._write("  " + self.paint(v, tone) + " " + line.ljust(inner) + " "
                        + self.paint(v, tone) + "\n")
        self._write(edge(bl, br))

    def good(self, text):
        self._write("  " + self.paint("✓" if self.unicode else "OK", OK) + " " + text + "\n")

    def note(self, text):
        self._write("  " + self.paint("•" if self.unicode else "-", DIM) + " "
                    + self.paint(text, DIM) + "\n")

    def problem(self, text, detail=None):
        self.blank()
        self.box(["Something went wrong.", "", text] +
                 (["", detail] if detail else []), BAD)

    # --- asking ----------------------------------------------------------------

    def confirm(self, prompt, default=True):
        hint = "Y/n" if default else "y/N"
        self.blank()
        for line in textwrap.wrap(prompt, self.width - 2):
            self._write("  " + self.paint(line, "1") + "\n")
        ans = input("  " + self.paint(f"[{hint}] ", ACCENT)).strip().lower()
        return default if not ans else ans in ("y", "yes")

    def pause(self, text="Press Enter to continue"):
        input("  " + self.paint(text + " ", DIM))

    # --- working ---------------------------------------------------------------

    @contextlib.contextmanager
    def spinner(self, message):
        """Animated "working" line. Yields an object whose .text can be changed."""
        state = type("S", (), {"text": message})()
        if not self.fancy:
            self._write(f"  {message}\n")
            yield state
            return
        frames = FRAMES_UNICODE if self.unicode else FRAMES_ASCII
        stop = threading.Event()

        def spin():
            i = 0
            while not stop.is_set():
                self._write("\r\033[K  " + self.paint(frames[i % len(frames)], ACCENT) + " "
                            + state.text)
                i += 1
                stop.wait(0.09)

        self._write("\033[?25l")
        t = threading.Thread(target=spin, daemon=True)
        t.start()
        try:
            yield state
        finally:
            stop.set()
            t.join()
            self._write("\r\033[K\033[?25h")

    def run(self, message, fn, progress=None):
        """Run fn() while showing `message`. What fn prints is hidden (kept in the log file)
        unless -v. `progress` maps the start of a printed line to a friendlier status."""
        if self.verbose:
            self.note(message)
            return fn()
        progress = progress or {}
        sink = _Sink()
        with self.spinner(message) as sp:
            def on_line(line):
                for prefix, text in progress.items():
                    if line.startswith(prefix):
                        sp.text = text
            sink.on_line = on_line
            try:
                with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                    return fn()
            finally:
                self.log.append(f"== {message}\n{sink.text()}")
                self.save_log()

    def save_log(self):
        if not self.log_path:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text("\n\n".join(self.log) + "\n")
        except OSError:
            pass

    def wait_for(self, condition, seconds, message, poll=3, on_enter=None):
        """Show a spinner until condition() is true. Pressing Enter re-checks right away (after
        on_enter, if the check fails). Returns True when it held, False on timeout."""
        deadline, last = time.time() + seconds, 0.0
        with self.spinner(message):
            while time.time() < deadline:
                if time.time() - last >= poll:
                    last = time.time()
                    if condition():
                        return True
                if sys.stdin.isatty() and select.select([sys.stdin], [], [], 0.25)[0]:
                    sys.stdin.readline()
                    last = time.time()
                    if condition():
                        return True
                    if on_enter:
                        on_enter()
                else:
                    time.sleep(0.25 if not sys.stdin.isatty() else 0)
        return False


def _hang(para):
    """Wrapped lines of a "  1. text" or "- text" item line up under the text."""
    stripped = para.lstrip()
    lead = len(para) - len(stripped)
    for mark in ("- ", "* ", "• "):
        if stripped.startswith(mark):
            return " " * (lead + 2)
    head, _, _ = stripped.partition(" ")
    if head.endswith(".") and head[:-1].isdigit():
        return " " * (lead + len(head) + 1)
    return " " * lead
