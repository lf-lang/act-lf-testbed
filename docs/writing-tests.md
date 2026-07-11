# Writing and Adding ACT Test Cases

This document explains how the hardware test suite in this repo is put
together, and how to add to it: new test variants or a
new external repo that wants to trigger these tests. For the high-level
purpose of the project, see the [README](../README.md).

## 1. How a test run is put together

Every test run compiles an LF program, flashes it to a physical
[Pololu 3pi+ 2040 robot](https://www.pololu.com/docs/0J86), the self-hosted runner watches the robot
with a camera, and asserts on what it measured. The jobs run on
`runs-on: [self-hosted, linux, lf-act-ast]`. `lf-act-ast` is a label on that
runner used during creation of the runner. 

Note: This label will be changed later on. It is not accessible to any user outside the organization, it will be a designated label
for a particular machine or runner. This is so that when newer hardware modules are incorporated, tests can be triggered using the 
specific hardware module.

Three parts of the repo cooperate:

| Path | Role |
|---|---|
| `lf-3pi-template/` | Git submodule ([lf-lang/lf-3pi-template](https://github.com/lf-lang/lf-3pi-template)). The actual LF project (pico-sdk, robot-lib, `shell.nix`) that gets compiled and flashed. Test files are staged into it before every run. |
| `test_lf_programs/` | The `.lf` programs under test, plus the Python scripts that build/detect/measure/plot for each hardware module, plus `cleanup.sh`. |
| `.github/workflows/` | Orchestration: label-gated dispatch → one reusable workflow per hardware module → a chain of jobs that compile, flash, measure, plot, and upload results. |

A per-module workflow (e.g. `act-lf-led.yml`) follows the same job chain for
every module:

```
setup            checkout (with submodules), `nix develop --command true` to prime the shell
pythonpackages   set up a Python venv/interpreter, install opencv/numpy/pandas/...
filecleanup      copy test_lf_programs/cleanup.sh into lf-3pi-template/ and run it
filesetup        copy the module's *.py and *.lf files into lf-3pi-template/(src)
compile<N>       nix develop --command lfc src/<Program_N>.lf
flash<N>         nix develop --command picotool load -x bin/<Program_N>.elf -f
run<N>           python3 test_<module>_module.py <N> — prints a measured value,
                 which the job asserts falls inside an expected range (awk)
  ...            (repeated per variant N = 1, 2, 3, ...; each flash needs the
                 previous run to finish since there's one robot)
plotgraph        python3 plot_<module>.py — renders a chart from the run's CSV
<module>upload   actions/upload-artifact of the CSV/SVG produced above
```

## 2. Where things happen: in-repo vs. cross-repo

There are two independent trigger paths, both gated by PR labels:

**In-repo** — `act-lf-test-flow.yml` runs on `pull_request` (`closed`) against
this repo's `main`, checks the PR's labels, and calls the matching
`act-lf-<module>.yml` reusable workflow directly. This is what runs when
*this* repo's own `test_lf_programs/` content changes.

**Cross-repo** — other repos (e.g. an LF board template or the `lingua-franca`
compiler repo itself) can trigger the *same* robot tests against their own
checkout by calling a reusable workflow published from here:

```yaml
# in another repo's .github/workflows/*.yml
on:
  pull_request:
    types: [opened]
jobs:
  led-test:
    if: contains(github.event.pull_request.labels.*.name, 'act-lf-led')
    uses: lf-lang/act-lf-testbed/.github/workflows/<filename_of_your_test>.yml@main
    secrets: inherit
```

The file name should be your test script and the "@main" is the branch. It can be a different branch if you are testing a different
version of the test suite. 

TODO: Need to test this part.

For your own test cases, just refer the already existing test scripts (.yml) file and your test scripts and upload the script to
the repo and you can call the test script from a different repo and utilize the runner and the ACT test suite.


## 3. Labels

Labels are how a PR selects which part of the hardware gets tested. `contains(github.event.pull_request.labels.*.name, 'act-lf-led')`
requires the literal label to exist on the repo *and* be attached to the PR —
creating the workflow gate alone does nothing without the label.

| Label | Triggers |
|---|---|
| `act-lf-led` | LED blink-timing test (`act-lf-led.yml` / `act-lf-led_cross.yml` / `act-lfdev-led.yml`) |
| `act-lf-motor` | Motor RPM test (`act-lf-motor.yml`) |
| `act-lf-imu` | IMU/tilt display test (`act-lf-imu.yml`) |
| `act-lf-bump` | Bump sensor/display test (`act-lf-bump.yml`) |

The convention is `act-lf-<module>`, one label per hardware module tested.

TODO: A new label that encompasses all the tests should be added.

## 4. Runner prerequisites

The self-hosted runner needs the packages `env_setup.py` installs
(`gh`, `git`, `curl`, `openjdk-17-jdk`, `nix`, `screen`, `cmake`), the LF
toolchain (installed via `install.lf-lang.org`), and Nix's `nix-users` group
membership with `experimental-features = nix-command flakes` set. Run
`python3 env_setup.py` manually on a new runner host — several steps need
interactive `sudo`/reboot, which is why it isn't part of the CI itself.

The hardware and OS installations are done offline and not through the CI.