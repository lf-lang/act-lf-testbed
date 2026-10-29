#!/usr/bin/env python3
"""Motor power check for Pololu 3pi+ 2040 Lingua Franca programs.

The programs target the RP2040, so they cannot run on this machine as-is. This script builds a
host copy in a temporary directory, changing only what ties the program to the robot:

  * the `target C { platform: ... }` declaration is replaced with a host target, and
  * imports of the Pololu library reactors Display, Encoders and Motors (from "lib/...lf" or
    <pololu-3pi-c>) are replaced with stand-ins that have the same ports: Motors logs each
    left_power and right_power command with its logical time, Display discards its text, and
    Encoders reports 0 (the wheels never turn).

If the program includes the Pico SDK headers <pico/stdlib.h> or <hardware/gpio.h>, they resolve
to stand-ins in which the GPIO calls do nothing, so the program still builds.

The program's own timers, state, modes and reactions compile unchanged. The host copy runs in
fast mode (logical time does not wait for the wall clock, so the run takes milliseconds) for the
observation time, and every motor command it sends is checked against one rule:

  every left_power and right_power command is between -1.0 and 1.0, the range the Motors
  reactor documents (it silently clamps anything outside it). NaN is outside it too.

Each option can also be set with the environment variable named in its help (for example
OBSERVE_MS=5000 python3 run_motor_check.py ...); an option given on the command line takes
precedence. Needs Python 3.8 or newer and lfc.

Exit status: 0 = every motor command is legal, 1 = illegal motor power detected,
             2 = the check could not be completed (missing tool, build or run failure).
"""

from __future__ import annotations

import argparse
import collections
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

NS_PER_MS = 1_000_000
DEFAULT_OBSERVE_MS = 10_000
LF_TIMEOUT_MAX_MS = 2_147_483_647  # the LF runtime reads its --timeout value into a C int
WALL_CLOCK_LIMIT_S = 60  # stops a host copy that hangs; a fast-mode run takes milliseconds
TAIL_LINES = 30  # lines of output shown when the build or the run fails
SHOWN = 6  # motor commands listed at the start of the report
MAX_MOTOR_COMMANDS = 1_000_000  # reading stops after this many motor commands

TARGET_C = re.compile(r"^\s*target\s+C(?:[\s{;]|$)")
IMPORT = re.compile(r"^\s*import\s")
IMPORT_PARTS = re.compile(r'^\s*import\s+(.+?)\s+from\s+("[^"]*"|<[^>]*>)\s*;?\s*(?://.*)?$')
IMPORTED = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\s+as\s+([A-Za-z_][A-Za-z0-9_]*))?")
TIMER = re.compile(r"^\s*timer\s")
WHOLE_NUMBER = re.compile(r"[0-9]+")
LF_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MOTOR_RECORD = re.compile(r"MOTOR_CMD t_ns=(-?[0-9]+) port=(\S+) power=(\S+)")

HOST_TARGET = 'target C { single-threaded: true, cmake-include: "host_stubs.cmake" }'

# Lets programs that include the Pico SDK headers build on the host; the GPIO calls do nothing.
PICO_STDLIB_STUB = """/* Host stand-in for the Pico SDK, written by run_motor_check.py. */
#ifndef HOST_PICO_STUB_H
#define HOST_PICO_STUB_H
#define PICO_DEFAULT_LED_PIN 25
#define GPIO_OUT 1
#define gpio_init(pin) ((void)(pin))
#define gpio_set_dir(pin, out) ((void)(pin), (void)(out))
#define gpio_put(pin, value) ((void)(pin), (void)(value))
#endif
"""

# Bodies of the stand-ins for the Pololu library reactors. Their ports match the robot's versions,
# which are the same in the old template's src/lib and in the pololu-3pi-c package.
STAND_INS = {
    "Display": r"""
  // Host stand-in for the Pololu Display reactor: discards the text.
  input line0: string
  input line1: string
  input line2: string
  input line3: string
""",
    "Encoders": r"""
  // Host stand-in for the Pololu Encoders reactor: the wheels never turn, so the counts stay 0.
  input trigger: bool
  output right: int32_t
  output left: int32_t

  reaction(trigger) -> left, right {=
    lf_set(right, 0);
    lf_set(left, 0);
  =}
""",
    "Motors": r"""
  // Host stand-in for the Pololu Motors reactor: logs each power command with its logical time
  // instead of driving the motors.
  preamble {=
    #include <stdio.h>
  =}
  input left_power: float
  input right_power: float

  reaction(left_power) {=
    printf("MOTOR_CMD t_ns=%lld port=%s.left_power power=%.9g\n",
           (long long)lf_time_logical_elapsed(), lf_reactor_full_name(self),
           (double)left_power->value);
  =}

  reaction(right_power) {=
    printf("MOTOR_CMD t_ns=%lld port=%s.right_power power=%.9g\n",
           (long long)lf_time_logical_elapsed(), lf_reactor_full_name(self),
           (double)right_power->value);
  =}
""",
}


class CheckError(Exception):
    """The check could not be completed (exit status 2)."""


class MotorLog:
    """Summary of the motor commands read from the host copy, kept as they arrive."""

    def __init__(self) -> None:
        self.count = 0
        self.first: list[tuple[int, str, float]] = []  # the first SHOWN commands
        self.ports: dict[str, list] = {}  # port -> [commands, lowest, highest, any NaN]
        self.illegal = 0
        self.first_illegal: tuple[int, str, float] | None = None

    def add(self, t_ns: int, port: str, power: float) -> None:
        self.count += 1
        if len(self.first) < SHOWN:
            self.first.append((t_ns, port, power))
        stats = self.ports.setdefault(port, [0, None, None, False])
        stats[0] += 1
        if math.isnan(power):
            stats[3] = True
        else:
            stats[1] = power if stats[1] is None else min(stats[1], power)
            stats[2] = power if stats[2] is None else max(stats[2], power)
        if not -1.0 <= power <= 1.0:  # false for NaN as well
            self.illegal += 1
            if self.first_illegal is None:
                self.first_illegal = (t_ns, port, power)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return check(args)
    except CheckError as error:
        warn(f"ERROR: {error}")
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("program", metavar="path/to/program.lf", help="the LF program to check")
    parser.add_argument("--observe-ms", type=whole_ms, metavar="MS",
                        help="logical time to run the program "
                             f"(default: $OBSERVE_MS, else {DEFAULT_OBSERVE_MS})")
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


def check(args: argparse.Namespace) -> int:
    """Run the whole check. Return 0 (all power legal) or 1 (some illegal), or raise CheckError."""
    observe_ms = setting_ms(args.observe_ms, "OBSERVE_MS", DEFAULT_OBSERVE_MS)
    lfc = args.lfc or os.environ.get("LFC") or "lfc"
    if observe_ms == 0:
        raise CheckError("the observation time must be at least 1 ms")
    if observe_ms > LF_TIMEOUT_MAX_MS:
        raise CheckError("the observation time is too large for the LF runtime's --timeout "
                         f"(at most {LF_TIMEOUT_MAX_MS} ms)")

    source = Path(args.program)
    if not source.is_file():
        raise CheckError(f"cannot find {args.program}")
    compiler = shutil.which(lfc)
    if compiler is None:
        raise CheckError(f"LF compiler '{lfc}' not found on PATH (override with --lfc or LFC=...)")
    lines = source.read_text(encoding="utf-8", errors="surrogateescape").split("\n")
    if not any(TARGET_C.match(line) for line in lines):
        raise CheckError(f"{args.program} is not a C-target LF program")
    stand_ins, import_lines = find_stand_ins(lines, args.program)
    text = "\n".join(lines)
    if not any(re.search(rf"\bnew\s*(?:\[[^\]]*\]\s*)?{re.escape(name)}\s*\(", text)
               for name, reactor in stand_ins.items() if reactor == "Motors"):
        raise CheckError(f"found nothing to check in {args.program}: it does not create a Motors "
                         "reactor")

    timers = [f"  {number}:{line}" for number, line in enumerate(lines, 1) if TIMER.match(line)]
    print(f"== Timer declaration{'s' if len(timers) > 1 else ''} in {args.program}")
    print("\n".join(timers) if timers else "  (none found)")
    print()

    # A named main reactor must match its file name, so the copy keeps the original file name
    # when that is a valid LF name; otherwise the main reactor is unnamed and any name works.
    stem = source.stem
    name = stem if LF_NAME.fullmatch(stem) and stem not in stand_ins else "HostCopy"
    copy = host_copy(lines, import_lines, stand_ins)
    with tempfile.TemporaryDirectory() as workdir:
        binary = build_host_copy(compiler, copy, Path(workdir), name, args.program,
                                 list(dict.fromkeys(stand_ins.values())))
        motors, stopped, stop_ns = run_host_copy(binary, observe_ms)

    stop_note = ""
    if stopped:
        stop_note = (f"(stopped reading at t={fmt(stop_ns)}: more than {MAX_MOTOR_COMMANDS:,} "
                     "motor commands)")
    legal = judge_motors(motors, stop_note)
    print()
    if not legal:
        print("DETECTED: illegal motor power")
    print(f"Motor power oracle: {'PASS' if legal else 'FAIL'}")
    return 0 if legal else 1


def find_stand_ins(lines: list[str], shown_as: str) -> tuple[dict[str, str], set[int]]:
    """Read the program's imports. Return {name used in the program: library reactor} for the
    reactors that get a stand-in, and the numbers of the import lines to remove."""
    stand_ins = {}
    import_lines = set()
    unsupported = []
    for number, line in enumerate(lines, 1):
        if not IMPORT.match(line):
            continue
        parts = IMPORT_PARTS.match(line)
        names = parts.group(1).split(",") if parts else [""]
        items = [IMPORTED.fullmatch(item.strip()) for item in names]
        if None in items:
            raise CheckError(f"cannot read the import on line {number} of {shown_as}: "
                             f"{line.strip()}")
        for item in items:
            reactor, alias = item.group(1), item.group(2) or item.group(1)
            if reactor in STAND_INS:
                stand_ins[alias] = reactor
            else:
                unsupported.append(reactor)
        import_lines.add(number)
    if unsupported:
        raise CheckError(f"{shown_as} imports {', '.join(unsupported)}, which this check has no "
                         f"host stand-in for (it has stand-ins for {', '.join(STAND_INS)})")
    return stand_ins, import_lines


def host_copy(lines: list[str], import_lines: set[int], stand_ins: dict[str, str]) -> list[str]:
    """The program's lines with the target declaration and its { ... } property block replaced
    by HOST_TARGET and the library imports removed, plus the stand-in reactors at the end.
    Removed lines become blank lines, so compiler messages keep the original line numbers."""

    def braces(line: str) -> int:
        return line.count("{") - line.count("}")

    out = []
    replaced = False
    depth = 0  # braces of the property block that are still open
    brace_next = False  # the declaration had no "{", so the block may open on the next line
    for number, line in enumerate(lines, 1):
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
        elif number in import_lines:
            out.append("")
        else:
            out.append(line)
        brace_next = False
    if stand_ins:
        out.append("// Host stand-ins for the Pololu library reactors (run_motor_check.py).")
        for alias, reactor in stand_ins.items():
            body = STAND_INS[reactor].strip("\n")
            out.append(f"reactor {alias} {{\n{body}\n}}\n")
    return out


def build_host_copy(compiler: str, copy: list[str], workdir: Path, name: str, shown_as: str,
                    stand_ins: list[str]) -> Path:
    """Write the host copy and the stand-in headers to workdir, build it, return the executable."""
    stubs = workdir / "stubs"
    (stubs / "pico").mkdir(parents=True)
    (stubs / "hardware").mkdir()
    (stubs / "pico" / "stdlib.h").write_text(PICO_STDLIB_STUB)
    (stubs / "hardware" / "gpio.h").write_text("#include <pico/stdlib.h>\n")
    src = workdir / "src"
    src.mkdir()
    (src / "host_stubs.cmake").write_text(
        f'target_include_directories(${{LF_MAIN_TARGET}} BEFORE PUBLIC "{stubs.as_posix()}")\n')
    host_lf = src / f"{name}.lf"
    host_lf.write_text("\n".join(copy), encoding="utf-8", errors="surrogateescape")

    print(f"== Building host copy (stand-ins for {', '.join(stand_ins)})", flush=True)
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


def run_host_copy(binary: Path, observe_ms: int) -> tuple[MotorLog, bool, int]:
    """Run the host copy in fast mode. Return its motor commands, whether reading stopped at
    MAX_MOTOR_COMMANDS, and the logical time of the last command read."""
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
    motors = MotorLog()
    last_ns = 0
    tail = collections.deque(maxlen=TAIL_LINES)
    stopped = False
    try:
        for raw in process.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            tail.append(line)
            record = MOTOR_RECORD.search(line)  # may follow output that lacked a final newline
            if record is None:
                continue
            last_ns = int(record.group(1))
            port = record.group(2).split(".", 1)[-1]  # drop the main reactor's name
            motors.add(last_ns, port, float(record.group(3)))
            if motors.count >= MAX_MOTOR_COMMANDS:
                stopped = True
                process.kill()
                break
    except BaseException:
        process.kill()
        raise
    finally:
        guard.cancel()
        process.stdout.close()
        status = process.wait()

    if not stopped and (timed_out.is_set() or status != 0):
        if tail:
            warn("\n".join(tail))
        if timed_out.is_set():
            raise CheckError(f"host copy did not finish within {WALL_CLOCK_LIMIT_S} s of "
                             "wall-clock time (a reaction may never return)")
        if status < 0:
            raise CheckError(f"host copy was stopped by signal {-status}")
        raise CheckError(f"host copy exited with status {status}")
    return motors, stopped, last_ns


def judge_motors(motors: MotorLog, stop_note: str) -> bool:
    """Print the motor commands and the motor power rule. Return True if every power is legal."""
    print("\n== Motor commands observed")
    width = max((len(port) for _, port, _ in motors.first), default=0)
    for t, port, power in motors.first:
        print(f"  t={fmt(t):<12} {port:<{width}} = {fmt_power(power)}")
    if motors.count > len(motors.first):
        print(f"  ... {motors.count - len(motors.first)} more")
    if motors.count == 0:
        print("  (none)")
    else:
        print(f"  {plural(motors.count, 'command', 'commands')} in all:")
        width = max(len(port) for port in motors.ports)
        for port, (count, lowest, highest, any_nan) in motors.ports.items():
            values = []
            if lowest is not None:
                values.append(fmt_power(lowest) if lowest == highest
                              else f"{fmt_power(lowest)} to {fmt_power(highest)}")
            if any_nan:
                values.append("nan")
            print(f"    {port:<{width}}  {' and '.join(values)} "
                  f"({plural(count, 'command', 'commands')})")
    if stop_note:
        print(f"  {stop_note}")

    rule = "every motor command is within -1.0 to 1.0"
    print("\n== Motor power oracle (legal power: -1.0 to 1.0)")
    if motors.count == 0:
        print(f"  [n/a]  {rule}")
        return True
    if motors.first_illegal is None:
        print(f"  [ok]   {rule}")
        return True
    t, port, power = motors.first_illegal
    print(f"  [FAIL] {rule}\n         {plural(motors.illegal, 'command', 'commands')} outside "
          f"the range, first at t={fmt(t)}: {port} = {fmt_power(power)}")
    return False


def fmt(ns: int) -> str:
    """A time in nanoseconds, in the coarsest unit that shows it exactly."""
    if ns % NS_PER_MS == 0:
        return f"{ns // NS_PER_MS} ms"
    if ns % 1000 == 0:
        return f"{ns // 1000} us"
    return f"{ns} ns"


def fmt_power(power: float) -> str:
    """A motor power, with more digits when the short form would hide that it is out of range."""
    text = f"{power:g}"
    if not -1.0 <= power <= 1.0 and -1.0 <= float(text) <= 1.0:
        text = f"{power:.9g}"
    return text


def plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def warn(text: str) -> None:
    sys.stdout.flush()  # keeps stdout and stderr in order when both go to the same place
    print(text, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
