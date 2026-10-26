#!/usr/bin/env python3
"""Blink timing check for the Pololu 3pi+ 2040 LED blink program (Blink.lf).

Blink.lf targets the RP2040, so it cannot run on this machine as-is. This script builds a host
copy of it in a temporary directory, changing only two things:

  * the `target C { platform: ... }` declaration is replaced with a host target, and
  * <pico/stdlib.h> and <hardware/gpio.h> resolve to stand-in headers in which gpio_put() logs
    the pin, the value and the logical time of each write instead of driving the LED.

The timer, state and reactions compile unchanged. The host copy runs in fast mode (logical time
does not wait for the wall clock, so the run takes milliseconds) for the observation time, and
an external oracle checks the LED writes against this timing policy:

  1. the LED starts blinking within the maximum period of startup,
  2. every write toggles the LED (on, off, on, ...),
  3. the time between consecutive writes is between the minimum and maximum period, and
  4. the LED keeps blinking until the end of the run (no final gap over the maximum period).

The period is the time between toggles, i.e. the timer period: Blink.lf's 250 ms means 250 ms
on, 250 ms off. A timer whose period is 0 (or missing) fires only once, so it fails rule 4.

Each option can also be set with the environment variable named in its help, as with the shell
version of this check (MIN_PERIOD_MS=200 python3 run_blink_timing_check.py ...); an option given
on the command line takes precedence. Needs Python 3.8 or newer and lfc.

Exit status: 0 = blink timing legal, 1 = illegal blink timing detected,
             2 = the check could not be completed (missing tool, build or run failure).
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

LED_PIN = 25  # the value the stand-in header gives PICO_DEFAULT_LED_PIN
NS_PER_MS = 1_000_000
LF_TIMEOUT_MAX_MS = 2_147_483_647  # the LF runtime reads its --timeout value into a C int
WALL_CLOCK_LIMIT_S = 60  # stops a host copy that hangs; a fast-mode run takes milliseconds
TAIL_LINES = 30  # lines of output shown when the build or the run fails

TARGET_C = re.compile(r"^\s*target\s+C(?:[\s{;]|$)")
IMPORT = re.compile(r"^\s*import\s")
TIMER = re.compile(r"^\s*timer\s")
WHOLE_NUMBER = re.compile(r"[0-9]+")
LF_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
GPIO_RECORD = re.compile(r"BLINK_GPIO t_ns=(-?[0-9]+) pin=(-?[0-9]+) value=([0-9]+)")

HOST_TARGET = 'target C { single-threaded: true, cmake-include: "blink_host_stubs.cmake" }'

# Stand-in for the Pico SDK calls Blink.lf makes. gpio_put() is a macro so that
# lf_time_logical_elapsed() expands inside the calling reaction, where the LF runtime defines it.
PICO_STDLIB_STUB = rf"""/* Host stand-in for the Pico SDK, written by run_blink_timing_check.py. */
#ifndef BLINK_HOST_PICO_STUB_H
#define BLINK_HOST_PICO_STUB_H
#include <stdio.h>
#define PICO_DEFAULT_LED_PIN {LED_PIN}
#define GPIO_OUT 1
#define gpio_init(pin) ((void)(pin))
#define gpio_set_dir(pin, out) ((void)(pin), (void)(out))
#define gpio_put(pin, value)                                                       \
  printf("BLINK_GPIO t_ns=%lld pin=%d value=%d\n", (long long)lf_time_logical_elapsed(), \
         (int)(pin), (int)((value) != 0))
#endif
"""


class CheckError(Exception):
    """The check could not be completed (exit status 2)."""


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return check(args, parser.prog)
    except CheckError as error:
        warn(f"ERROR: {error}")
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("blink_lf", nargs="?", default="src/Blink.lf", metavar="path/to/Blink.lf",
                        help="the LF program to check (default: src/Blink.lf)")
    parser.add_argument("--min-period-ms", type=whole_ms, metavar="MS",
                        help="shortest legal time between LED toggles "
                             "(default: $MIN_PERIOD_MS, else 100)")
    parser.add_argument("--max-period-ms", type=whole_ms, metavar="MS",
                        help="longest legal time between LED toggles "
                             "(default: $MAX_PERIOD_MS, else 2000)")
    parser.add_argument("--observe-ms", type=whole_ms, metavar="MS",
                        help="logical time to run the program "
                             "(default: $OBSERVE_MS, else 5 x the maximum period)")
    parser.add_argument("--lfc", metavar="PATH",
                        help="Lingua Franca compiler (default: $LFC, else lfc)")
    return parser


def whole_ms(text: str) -> int:
    """argparse type for an option given in whole milliseconds."""
    if not WHOLE_NUMBER.fullmatch(text):
        raise argparse.ArgumentTypeError(f"must be a whole number of milliseconds, not {text!r}")
    return int(text)


def setting_ms(option: int | None, env_name: str, default: int) -> int:
    """The option's value if it was given, else the environment variable's, else the default."""
    if option is not None:
        return option
    text = os.environ.get(env_name, "")
    if text == "":
        return default
    if not WHOLE_NUMBER.fullmatch(text):
        raise CheckError(f"{env_name} must be a whole number of milliseconds")
    return int(text)


def check(args: argparse.Namespace, prog: str) -> int:
    """Run the whole check. Return 0 (legal timing) or 1 (illegal timing), or raise CheckError."""
    min_ms = setting_ms(args.min_period_ms, "MIN_PERIOD_MS", 100)
    max_ms = setting_ms(args.max_period_ms, "MAX_PERIOD_MS", 2000)
    observe_ms = setting_ms(args.observe_ms, "OBSERVE_MS", 5 * max_ms)
    lfc = args.lfc or os.environ.get("LFC") or "lfc"
    if min_ms > max_ms:
        raise CheckError(f"the minimum period ({min_ms} ms) is larger than the maximum period "
                         f"({max_ms} ms)")
    if observe_ms <= max_ms:
        raise CheckError(f"the observation time ({observe_ms} ms) must be longer than the "
                         f"maximum period ({max_ms} ms)")
    if observe_ms > LF_TIMEOUT_MAX_MS:
        raise CheckError("the observation time is too large for the LF runtime's --timeout "
                         f"(at most {LF_TIMEOUT_MAX_MS} ms)")

    source = Path(args.blink_lf)
    if not source.is_file():
        raise CheckError(f"cannot find {args.blink_lf} (usage: {prog} path/to/Blink.lf)")
    compiler = shutil.which(lfc)
    if compiler is None:
        raise CheckError(f"LF compiler '{lfc}' not found on PATH (override with --lfc or LFC=...)")
    lines = source.read_text(encoding="utf-8", errors="surrogateescape").split("\n")
    if not any(TARGET_C.match(line) for line in lines):
        raise CheckError(f"{args.blink_lf} is not a C-target LF program")
    if any(IMPORT.match(line) for line in lines):
        raise CheckError(f"{args.blink_lf} imports other .lf files; this check only stubs the "
                         "GPIO calls a standalone Blink.lf makes")

    print(f"== Timer declaration in {args.blink_lf}")
    timers = [f"  {number}:{line}" for number, line in enumerate(lines, 1) if TIMER.match(line)]
    print("\n".join(timers) if timers else "  (none found)")
    print()

    # A named main reactor must match its file name, so the copy keeps the original file name
    # when that is a valid LF name; otherwise the main reactor is unnamed and any name works.
    name = source.stem if LF_NAME.fullmatch(source.stem) else "BlinkHost"
    # Stop reading once the program has made more LED writes than any legal blink could make in
    # the run; a tiny period would otherwise produce billions of them.
    max_writes = 2 * observe_ms // max(min_ms, 1) + 100
    with tempfile.TemporaryDirectory() as workdir:
        binary = build_host_copy(compiler, lines, Path(workdir), name, args.blink_lf)
        writes, capped, stop_ns = run_host_copy(binary, observe_ms, max_writes)
    return judge(writes, capped, stop_ns, min_ms, max_ms, observe_ms)


def host_copy(lines: list[str]) -> list[str]:
    """Replace the target declaration and its { ... } property block with HOST_TARGET. Removed
    lines become blank lines, so compiler messages keep the original line numbers."""

    def braces(line: str) -> int:
        return line.count("{") - line.count("}")

    out = []
    replaced = False
    depth = 0  # braces of the property block that are still open
    brace_next = False  # the declaration had no "{", so the block may open on the next line
    for line in lines:
        if not replaced and TARGET_C.match(line):
            replaced = True
            depth = braces(line)
            brace_next = "{" not in line
            out.append(HOST_TARGET)
            continue
        if brace_next and line.lstrip().startswith("{"):
            depth = braces(line)
            out.append("")
        elif depth > 0:
            depth += braces(line)
            out.append("")
        else:
            out.append(line)
        brace_next = False
    return out


def build_host_copy(compiler: str, lines: list[str], workdir: Path, name: str,
                    shown_as: str) -> Path:
    """Write the host copy and the stand-in headers to workdir, build it, return the executable."""
    stubs = workdir / "stubs"
    (stubs / "pico").mkdir(parents=True)
    (stubs / "hardware").mkdir()
    (stubs / "pico" / "stdlib.h").write_text(PICO_STDLIB_STUB)
    (stubs / "hardware" / "gpio.h").write_text("#include <pico/stdlib.h>\n")
    src = workdir / "src"
    src.mkdir()
    (src / "blink_host_stubs.cmake").write_text(
        f'target_include_directories(${{LF_MAIN_TARGET}} BEFORE PUBLIC "{stubs.as_posix()}")\n')
    host_lf = src / f"{name}.lf"
    host_lf.write_text("\n".join(host_copy(lines)), encoding="utf-8", errors="surrogateescape")

    print("== Building host copy (Pico SDK calls stubbed)", flush=True)
    build = subprocess.run([compiler, str(host_lf)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if build.returncode != 0:
        log = build.stdout.decode("utf-8", "replace").rstrip("\n").split("\n")
        warn("\n".join(log[-TAIL_LINES:]))
        warn(f"(messages refer to the host copy {host_lf.name}; its line numbers match {shown_as})")
        raise CheckError("lfc could not build the host copy (errors above)")
    binary = workdir / "bin" / name
    if not (binary.is_file() and os.access(binary, os.X_OK)):
        raise CheckError(f"expected executable {binary} not found")
    return binary


def run_host_copy(binary: Path, observe_ms: int,
                  max_writes: int) -> tuple[list[tuple[int, int]], bool, int]:
    """Run the host copy in fast mode. Return its writes to the LED pin as (t_ns, value) pairs,
    whether reading stopped at max_writes, and the logical time of the last write read."""
    print(f"== Running host copy for {observe_ms} ms of logical time (fast mode)", flush=True)
    process = subprocess.Popen([str(binary), "-f", "true", "-o", str(observe_ms), "msec"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
    timed_out = threading.Event()

    def stop_hung_run() -> None:
        timed_out.set()
        process.kill()

    guard = threading.Timer(WALL_CLOCK_LIMIT_S, stop_hung_run)
    guard.daemon = True
    guard.start()
    writes = []  # (t_ns, value) of each write to the LED pin
    records = 0  # writes to any pin, counted against max_writes
    last_ns = 0
    tail = collections.deque(maxlen=TAIL_LINES)
    capped = False
    try:
        for raw in process.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            tail.append(line)
            record = GPIO_RECORD.search(line)  # may follow output that lacked a final newline
            if record is None:
                continue
            last_ns, pin, value = map(int, record.groups())
            if pin == LED_PIN:
                writes.append((last_ns, value))
            records += 1
            if records >= max_writes:
                capped = True
                process.kill()
                break
    except BaseException:
        process.kill()
        raise
    finally:
        guard.cancel()
        process.stdout.close()
        status = process.wait()

    if not capped and (timed_out.is_set() or status != 0):
        if tail:
            warn("\n".join(tail))
        if timed_out.is_set():
            raise CheckError(f"host copy did not finish within {WALL_CLOCK_LIMIT_S} s of "
                             "wall-clock time (a reaction may never return)")
        if status < 0:
            raise CheckError(f"host copy was stopped by signal {-status}")
        raise CheckError(f"host copy exited with status {status}")
    return writes, capped, last_ns


def judge(writes: list[tuple[int, int]], capped: bool, stop_ns: int, min_ms: int, max_ms: int,
          observe_ms: int) -> int:
    """Print the LED writes and the oracle's verdict. Return 0 if the timing is legal, else 1."""
    lo, hi, run_ns = min_ms * NS_PER_MS, max_ms * NS_PER_MS, observe_ms * NS_PER_MS
    times = [t for t, _ in writes]
    # gaps[i] is the time from write i to write i + 1 (zip rather than pairwise, for Python 3.8).
    gaps = [later - earlier for earlier, later in zip(times, times[1:])]

    print("\n== LED writes observed")
    for t, value in writes[:6]:
        print(f"  t={fmt(t):<12} gpio_put({LED_PIN}, {value})")
    if len(writes) > 6:
        print(f"  ... {len(writes) - 6} more")
    if not writes:
        print("  (none)")
    if gaps:
        shortest, longest = min(gaps), max(gaps)
        span = fmt(shortest) if shortest == longest else f"{fmt(shortest)} to {fmt(longest)}"
        print(f"  {len(writes)} writes; time between writes: {span}")
    if capped:
        print(f"  (stopped reading at t={fmt(stop_ns)}: more writes than a legal blink makes "
              f"in {observe_ms} ms)")

    # Rule 1: the LED starts blinking within the maximum period.
    starts = ""
    if not writes:
        starts = f"the LED is never written during the {observe_ms} ms run"
    elif times[0] > hi:
        starts = f"first write at t={fmt(times[0])}"

    # Rule 2: every write toggles the LED.
    toggles = ""
    repeats = [i for i in range(1, len(writes)) if writes[i][1] == writes[i - 1][1]]
    if repeats:
        toggles = (f"{plural(len(repeats), 'write repeats', 'writes repeat')} the previous value "
                   f"(first at t={fmt(times[repeats[0]])})")

    # Rule 3: the time between consecutive writes is within the legal range.
    problems = []
    for kind, limit_ms, bad in (("shorter", min_ms, [i for i, gap in enumerate(gaps) if gap < lo]),
                                ("longer", max_ms, [i for i, gap in enumerate(gaps) if gap > hi])):
        if bad:
            i = bad[0]
            problems.append(f"{plural(len(bad), 'gap', 'gaps')} {kind} than {limit_ms} ms, first "
                            f"t={fmt(times[i])} to t={fmt(times[i + 1])} ({fmt(gaps[i])})")
    spacing = "\n         ".join(problems)

    # Rule 4: the LED keeps blinking until the end of the run.
    keeps = ""
    if writes and run_ns - times[-1] > hi:
        keeps = (f"no write after t={fmt(times[-1])}, so the LED is unchanged for the last "
                 f"{fmt(run_ns - times[-1])}")
        if len(writes) == 1:
            keeps += ("\n         (what a timer period of 0, no period, or one longer than the "
                      "run produces)")

    print(f"\n== Blink timing oracle (legal time between LED toggles: {min_ms}-{max_ms} ms)")
    results = [
        (f"starts blinking within {max_ms} ms", True, starts),
        ("every write toggles the LED", len(writes) >= 2, toggles),
        (f"time between writes is {min_ms}-{max_ms} ms", len(writes) >= 2, spacing),
        ("keeps blinking until the end of the run", bool(writes) and not capped, keeps),
    ]
    failed = False
    for label, applies, problem in results:
        if not applies:
            print(f"  [n/a]  {label}")
        elif not problem:
            print(f"  [ok]   {label}")
        else:
            print(f"  [FAIL] {label}\n         {problem}")
            failed = True
    print()
    if failed:
        print("DETECTED: illegal blink timing")
        print("Blink timing oracle: FAIL")
        return 1
    print("Blink timing oracle: PASS")
    return 0


def fmt(ns: int) -> str:
    """A time in nanoseconds, in the coarsest unit that shows it exactly."""
    if ns % NS_PER_MS == 0:
        return f"{ns // NS_PER_MS} ms"
    if ns % 1000 == 0:
        return f"{ns // 1000} us"
    return f"{ns} ns"


def plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def warn(text: str) -> None:
    sys.stdout.flush()  # keeps stdout and stderr in order when both go to the same place
    print(text, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
