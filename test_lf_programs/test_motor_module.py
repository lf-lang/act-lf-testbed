#!/usr/bin/env python3
"""
ACT for Motors -> test inside a Nix shell.
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
import math

os.environ["QT_QPA_PLAPTFORM"] = "offscreen"

#CAMERA_INDEX = 2
#HIGH_RESOLUTION = (1920, 1080)
ROI = [250, 420, 320, 450]

HIGH_RESOLUTION = (1920, 1080)
# HSV color ranges (tune these for your setup)
BLUE_LOWER = np.array([90, 80, 80])
BLUE_UPPER = np.array([130, 255, 255])
RED_LOWER1 = np.array([0, 100, 100])
RED_UPPER1 = np.array([10, 255, 255])
RED_LOWER2 = np.array([160, 100, 100])
RED_UPPER2 = np.array([179, 255, 255])

flag = 0

def find_camera_index(max_index=10):
    #i = 1
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            cap.release()
            return i
    return None

def plot():
    return
    
def motor_speed(num):
    
    if num == 1:
        actual_rpm = 33
    elif num == 2:
        actual_rpm = 60
    elif num == 3:
        actual_rpm = 80
    else:
        actual_rpm = 0
    '''
    cam_index = find_camera_index()
    if cam_index is not None:
        flag = cam_index
        #print(f"Camera found at index {cam_index}")
    else:
        print("No camera found")
    '''
    # TODO: Will modify this part of code later.
    # The logic only applies when there are multiple camera modules. However, that would either need to be addressed or
    # given as a constraint since the testing platform is controlled by ACT.
    cam_index = 2     
    cap = cv2.VideoCapture(cam_index, cv2.CAP_V4L2)
    if not cap.isOpened():
        print("Error: Could not open camera.")
        return

    #print("Width:", cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    #print("Height:", cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    #print("FPS:", cap.get(cv2.CAP_PROP_FPS))

    prev_angle = None
    rotations = 0
    start_time = time.time()
    elapsed = 0
    rpm = 0
    def get_centroid(mask):
        M = cv2.moments(mask)
        if M['m00'] > 0:
            return int(M['m10']/M['m00']), int(M['m01']/M['m00'])
        return None

    while elapsed < 60:
        ret, frame = cap.read()
        if not ret:
            break

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        # Detect blue center
        blue_mask = cv2.inRange(hsv, BLUE_LOWER, BLUE_UPPER)
        blue_center = get_centroid(blue_mask)

        # Detect red rotating marker (combine two red hue ranges)
        red_mask1 = cv2.inRange(hsv, RED_LOWER1, RED_UPPER1)
        red_mask2 = cv2.inRange(hsv, RED_LOWER2, RED_UPPER2)
        red_mask = cv2.bitwise_or(red_mask1, red_mask2)
        red_center = get_centroid(red_mask)

        if blue_center and red_center:
            bx, by = blue_center
            rx, ry = red_center

            dx = rx - bx
            dy = ry - by
            angle = math.degrees(math.atan2(dy, dx))

            cv2.circle(frame, blue_center, 6, (255, 0, 0), -1)
            cv2.circle(frame, red_center, 6, (0, 0, 255), -1)
            cv2.line(frame, blue_center, red_center, (0, 255, 0), 2)

            if prev_angle is not None:
                d_angle = angle - prev_angle
                # Handle wrap-around (e.g., +179 to -179)
                if d_angle < -180:
                    d_angle += 360
                    rotations += 1

                ''' TODO: If we are accounting for calculation of speed in counterclockwise.
                elif d_angle > 180:
                    d_angle -= 360
                    rotations -= 1
                '''
                elapsed = time.time() - start_time
                if (rotations > 0) :
                    measured = time.time() - start_time
                    rpm = (abs(rotations) / measured) * 60
                else:
                    rpm = 0
                
                #elapsed = time.time() - start_time
                #rpm = (abs(rotations) / elapsed) * 60
                cv2.putText(frame, f"RPM: {rpm:.2f}", (30, 50),
                            cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 255, 255), 3)
        
            data = {
            "Group": [num],
            "Actual RPM": [actual_rpm],
            "Measured RPM": [rpm],
            "Time": [elapsed]
            }
        
            df = pd.DataFrame(data)
            csv_filename = "motor.csv"
        
            if os.path.exists(csv_filename):
                df.to_csv(csv_filename, mode='a', header=False, index=False)
            else:
                df.to_csv(csv_filename, mode='w', header=True, index=False)

            prev_angle = angle

        #cv2.imshow("RPM Tracker", frame)
        #if cv2.waitKey(1) & 0xFF == ord('q'):
        #    break

    #print(f"RPM: {rpm:.2f}")
    print(f"{rpm:.2f}")

    cap.release()
    #cv2.destroyAllWindows()

def main():
    parser = argparse.ArgumentParser(description="Test for blink program. Give index and -p for plot")
    parser.add_argument("number", type=int, help="Input number")
    parser.add_argument("-p", action="store_true", help="Enable plotting")

    args = parser.parse_args()

    # Always called
    motor_speed(args.number)

    # Called only if -p is present
    if args.p:
        plot()
    
if __name__ == "__main__":
    main()