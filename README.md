# ACT: Automated CPS Testing for Open-Source Robotic Platforms

## Abstract: 

Open-source software for cyber-physical systems (CPS) often lacks
robust testing involving robotic platforms, resulting in critical er-
rors that remain undetected. This is especially challenging when
multiple modules of CPS software are developed by various open-
source contributors. To address this gap, we propose Automated
CPS Testing (ACT) that performs automated, continuous testing of
open-source software with its robotic platforms, integrated with
the open-source infrastructure such as GitHub.

## Block Diagram:

<p align="center">
<img src="misc/systemarch-1.jpg" width="60%" height="60%" alt="ACT Block Diagram">
</p>

This figure represents the illustrates the proposed approach, for a testing framework
for CPS with physical robotic hardware.

## Methodology:
The current iteration of ACT is targetted towards the Pololu 3pi+ 2040 Robot 
which runs Lingua Franca programs. This robot is currently used in the
embedded systems lab of the Lingua Franca ecosystem. We have testing methods 
for the 
1. On-board LED 
2. Motors
3. Bump sensor and display
4. IMU and display

The testing method, clones the repository into the self-hosted runner, where it 
installs the pre-requisite packages. The next step is compiling the lf files, 
flashing the binary to the robot, monitor the behavior of the robot and then 
upload the test data (graphs, csv) to this repo as artifacts.

The script "env_setup.py" needs to be run manually since many of the commands 
require user permission. The next step would be to host the runner in your local 
machine and it will await the trigger to start testing.

## Writing and adding tests:

See [docs/writing-tests.md](docs/writing-tests.md) for how the label-gated
workflow suite is put together, and step-by-step instructions for adding a
new test variant, a new hardware module, or wiring up a new repo for
cross-repo testing (`act-lf-led`, `act-lf-motor`, `act-lf-imu`, `act-lf-bump`
labels and friends).

## TODO:

1. Adding test cases for Bump sensor and display, IMU paired with display.
2. Test cases where hardware is offline. Corner cases where certain tests fail 
or hardware becomes unavailable.
