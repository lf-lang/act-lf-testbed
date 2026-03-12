#!/usr/bin/env python3
"""
ACT for LED module -> test inside a Nix shell.
"""

import subprocess
import shlex
import sys

from pathlib import Path

import argparse

import cv2
import numpy as np
import time
import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import csv
import os

os.environ["QT_QPA_PLAPTFORM"] = "offscreen"

GROUP = 3
MAX_COUNT = 60
PERIOD = 0.5
#CAMERA_INDEX = 0
HIGH_RESOLUTION = (1920, 1080)
ROI = [250, 420, 320, 450]
DEBOUNCE_TIME = 0.1
INACTIVITY_TIMEOUT = 5.0
MAX_IDLE_TIME = 10.0
flag = 1

def find_camera_index(max_index=10):
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            cap.release()
            return i
    return None

def plot():
    #ACT_HOME = Path.home()/"pololu"
    
    df = pd.read_csv("blink_results.csv")

    plt.style.use('seaborn-v0_8-whitegrid'
                  )
    df["Deviation (%)"] = (df["Deviation: (s)"].abs() / df["Actual Period (s)"]) * 100

    # Plot: Total Time vs Deviation %
    plt.figure(figsize=(8,6))
    for actual_period, group in df.groupby("Actual Period (s)"):
        plt.plot(
            group["Total Time (s)"],
            group["Deviation (%)"],
            marker='s',
            linewidth=2,
            label=f"Actual Period : {actual_period:.1f}s"
        )

    plt.axhline(y=0, color='black', linestyle='--', linewidth=1)
    plt.tick_params(axis='x', labelsize=20)
    plt.tick_params(axis='y', labelsize=20)
    #plt.title("Deviation (%) v Total Testing Time", fontsize=24)
    plt.xlabel("Total Testing Time Elapsed (s)", fontsize=24)
    plt.ylabel("Deviation (%)", fontsize=24)
    plt.legend(title="", fontsize=24)
    plt.grid(True, linestyle='--', alpha=0.6)
    plt.tight_layout()
    plt.savefig("time_vs_deviation_percent.svg")
    #plt.savefig(ACT_HOME/"time_vs_deviation_percent.jpg")
    #plt.show()
    return
    
def led_detect2(num):
    
    if num == 1:
        Actual_Period = 0.2
    elif num == 2:
        Actual_Period = 0.5
    else:
        Actual_Period = 1

    
    cam_index = find_camera_index()
    if cam_index is not None:
        flag = 1
        #print(f"Camera found at index {cam_index}")
    else:
        print("No camera found")

    # TODO: Will modify this part of code later.
    # The logic only applies when there are multiple camera modules. However, that would either need to be addressed or
    # given as a constraint since, the testing platform is controlled by ACT.
    #cam_index = 0
    cap = cv2.VideoCapture(cam_index)
    if not cap.isOpened():
        #print("Error: Could not open camera.")
        time.sleep(2)
        for i in range (2):
            cap = cv2.VideoCapture(cam_index)
            if not cap.isOpened():
                continue
        #return
    
    if not cap.isOpened():
        return

    blink_count = 0
    is_on = False
    last_blink_time = time.time()
    start_time = time.time()
    measurement_time = 0
    tp = []
    bc = []
    prev_count = 0

    #print("Monitoring for orange LED blinks... Press 'q' to quit.")

    try:
        while blink_count < MAX_COUNT:
            ret, frame = cap.read()
            if not ret:
                break

            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            #roi_hsv = hsv[ROI[0]:ROI[1], ROI[2]:ROI[3]]

            # Orange LED color range
            lower_orange = (10, 150, 150)
            upper_orange = (25, 255, 255)
            mask = cv2.inRange(hsv, lower_orange, upper_orange)

            led_present = cv2.countNonZero(mask) > 5
            current_time = time.time()

            # Detect LED turn ON
            if led_present and not is_on:
                if current_time - last_blink_time > DEBOUNCE_TIME:
                    blink_count += 1
                    is_on = True
                    last_blink_time = current_time
                    measurement_time += (current_time - last_blink_time)
                    #print(f"Blink {blink_count}")

            # Detect LED turn OFF
            elif not led_present and is_on:
                is_on = False

            # NO blink condition within MAX_IDLE_TIME
            if blink_count == 0 and (current_time - start_time > MAX_IDLE_TIME):
                print("No LED activity detected!! ... Exiting.")
                break

            # Inactivity condition no LED blink happens
            if blink_count > 0 and (current_time - last_blink_time > INACTIVITY_TIMEOUT):
                #print("No blinks detected for a while!!..Stopping.")
                break

            #display ROI
            #cv2.rectangle(frame, (ROI[2], ROI[0]), (ROI[3], ROI[1]), (0, 255, 0), 2)
            cv2.putText(frame, f"Blinks: {blink_count}", (50, 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 255, 255), 3)

            mask_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
            mask_bgr = cv2.resize(mask_bgr, (frame.shape[1], frame.shape[0]))
            combined = np.hstack((frame, mask_bgr))
            #cv2.imshow("Detect LED", combined)
            
            if prev_count != blink_count:
                if blink_count > 0:
                    #bc.append(blink_count) TODO?
                    t2 = time.time()
                    total_time = t2 - start_time
                    bc.append(total_time)
                    freq = (blink_count) / total_time
                    period = 1/ freq
                    tp.append(period)
                    deviation = Actual_Period - period
                    data = {
                    "Group": [num],
                    "Actual Period (s)": [Actual_Period],
                    "Frequency (Hz)": [round(freq, 2)],
                    "Total Time (s)": [round(total_time, 2)],
                    "Blink Count": [blink_count],
                    "Period (s)": [round(period, 2)],
                    "Deviation: (s)": [round(deviation, 3)]
                    }

                    df = pd.DataFrame(data)
                    csv_filename = "blink_results.csv"

                    # Append or create new
                    if os.path.exists(csv_filename):
                        df.to_csv(csv_filename, mode='a', header=False, index=False)
                    else:
                            df.to_csv(csv_filename, mode='w', header=True, index=False)
            
            prev_count = blink_count

            #if cv2.waitKey(1) & 0xFF == ord('q'):
            #    print("Quit requested.")
            #    break
            

    finally:
        cap.release()
        #cv2.destroyAllWindows()
    
    period = 0
    total_time = time.time() - start_time
    #print(f"\nTotal time: {total_time:.2f}s, Total blinks: {blink_count}")
    if blink_count > 1:
        freq = (blink_count )/ (time.time() - start_time)
        period = round(1/freq,1)
        #print(f"Blink frequency: ~{freq:.2f} Hz")
        #print(f"Time period: {period:.2f} sec")
        print(f"{period:.2f}")
    else:
        freq = 0
        #print(f"Blink frequency: ~{freq:.2f} Hz")
        #print("Time period is not defined")
        print("0.0")
    

CLEAN_FILE = "blink_results.csv"

def clean():
    if os.path.exists(CLEAN_FILE):
        try:
            os.remove(CLEAN_FILE)
            print("Removed file")
        except Exception as e:
            print(f"Failed to remove:{e}")
    else:
        print("File does not exist.")


def build(num):
    #clean()
    
    if num == 1:
        lf_file = "src/Blink_1.lf"
        elf_path = "bin/Blink_1.elf"
    elif num == 2:
        lf_file = "src/Blink_2.lf"
        elf_path = "bin/Blink_2.elf"
    else:
        lf_file = "src/Blink_3.lf"
        elf_path = "bin/Blink_3.elf"

    shell_cmd = f"""
        set -e
        echo "Building {lf_file}"
        lfc {shlex.quote(lf_file)}
        echo "Flashing {elf_path}"
        picotool load -x {shlex.quote(elf_path)} -f
        echo "Done"
    """

    try:
        subprocess.run(
            ["nix", "develop", "--command", "bash", "-c", shell_cmd],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"\nBuild or flash failed (exit code {e.returncode})")
        sys.exit(e.returncode)
    except FileNotFoundError:
        print("Error: 'nix' not found. Make sure Nix is installed and in PATH.")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Test for blink program. Give index and -p for plot")
    parser.add_argument("number", type=int, help="Input number")
    parser.add_argument("-p", action="store_true", help="Enable plotting")

    args = parser.parse_args()

    # Always called
    led_detect2(args.number)

    # Called only if -p is present
    if args.p:
        plot()
    
if __name__ == "__main__":
    main()

