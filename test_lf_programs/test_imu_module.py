#!/usr/bin/env python3
"""
ACT for IMU sensor -> test inside a Nix shell.
"""

import subprocess
import shlex
import sys
import cv2
import time
import os
import easyocr
from PIL import Image, ImageOps
import numpy as np
import re
import pandas as pd


os.environ["QT_QPA_PLAPTFORM"] = "offscreen"

"""
TODO: We should make this config customizable.
--CONFIG--

"""
CAMERA_INDEX = 0
SAVE_DIR = "captures"
CAPTURE_INTERVAL = 5
HIGH_RESOLUTION = (1920, 1080)
ROI = [200, 600, 400, 1000]

os.makedirs(SAVE_DIR, exist_ok=True)

def extract_number(text):
    #match = re.search(r"[-+]?\d*\.\d+|\d+", text.replace(",", "."))
    text = text.replace(",", ".")
    match = re.search(r"[-+]?(?:\d*\.\d+|\d+)", text)
    return float(match.group()) if match else None

#TODO: Replace this later
def extract_pitch_roll(texts):

    pitch_val, roll_val = None, None
    for t in texts:
        clean = t.lower().replace(" ", "")
        match_pitch = re.search(r"pitch[:\-]?\s*([+-]?\d+\.?\d*)", clean)
        match_roll  = re.search(r"roll[:\-]?\s*([+-]?\d+\.?\d*)", clean)
        if match_pitch:
            pitch_val = float(match_pitch.group(1))
        if match_roll:
            roll_val = float(match_roll.group(1))
    return pitch_val, roll_val

def read_capture(count):
    try:
        img = Image.open('captures/capture.png')
        inverted_img = ImageOps.invert(img)

        frame = np.array(inverted_img)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        enhanced = cv2.convertScaleAbs(gray, alpha=1.5, beta=10)
        frame = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)

       
        reader = easyocr.Reader(['en'], gpu=False)
        results = reader.readtext(frame, allowlist='-+0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.')

        #print("\n---Output ---\n")
        if not results:
            #print("No OCR results.")
            return None, 0

        detections = []
        for (bbox, text, prob) in results:
            #print(f"Detected: {text} (Confidence: {prob:.2f})")
            detections.append((text.strip(), prob))

        pitch_value, roll_value = None, None
        pitch_conf, roll_conf = 0, 0

        for text, prob in detections:
            lowered = text.lower()
            number = extract_number(text)
            if "pitch" in lowered and number is None:
                continue
            elif "roll" in lowered and number is None:
                continue
            #TODO: A better way to do this?!
            if "pitch" in lowered:
                if prob > pitch_conf and number is not None:
                    pitch_value, pitch_conf = number, prob
            elif "roll" in lowered:
                if prob > roll_conf and number is not None:
                    roll_value, roll_conf = number, prob
            elif number is not None:
                if prob > pitch_conf:
                    pitch_value, pitch_conf = number, prob
                elif prob > roll_conf:
                    roll_value, roll_conf = number, prob
                
        #Out of range --> Needs to be recaptured. Corner cases needs to be fixed.
        out_of_range = abs(pitch_value) > 90 or abs(roll_value) > 90
        check = "Failed" if out_of_range else "Passed"
        if count < 3:
            if out_of_range:
                #print(f"Frame {count}: Ignoring out-of-range reading (Pitch={pitch_value}, Roll={roll_value})")
                return None, 0
        else:
            check = "Failed" if out_of_range else "Passed"
            #print(f"Frame {count}: Check={check} (Pitch={pitch_value}, Roll={roll_value})")
            
        
        
        data = {
            "Frame": [count],
            "Pitch": [pitch_value],
            #"Pitch_Conf": [pitch_conf], -> Re-add if needed 
            "Roll": [roll_value],
            #"Roll_Conf": [roll_conf],
            "Check": [check],
        }

        print(f"\nPitch: {pitch_value} (Conf {pitch_conf:.2f})")
        print(f"Roll:  {roll_value} (Conf {roll_conf:.2f})")

        if pitch_value is None and roll_value is None:
            return None, 0
        
        #TODO: Lot of cleanup in code.
        avg_conf = (pitch_conf + roll_conf) / 2 if (pitch_conf > 0 or roll_conf > 0) else 0
        return data, avg_conf

    except Exception as e:
        print(f"[Error in read_capture] {e}")
        return None, 0

def imu_read():
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("Error: Could not open camera.")
        return

    # Set camera resolution
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, HIGH_RESOLUTION[0])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HIGH_RESOLUTION[1])

    #print("Press 's' to save an image, 'q' to quit.")
    last_capture_time = 0
    count = 0
    
    try:
        while count <= 3:
            ret, frame = cap.read()
            if not ret:
                print("Failed to capture frame.")
                break

            cv2.rectangle(frame, (ROI[2], ROI[0]), (ROI[3], ROI[1]), (0, 255, 0), 2)

            roi_frame = frame[ROI[0]:ROI[1], ROI[2]:ROI[3]]

            #cv2.imshow("High-Res Camera", frame)
            #cv2.imshow("ROI View", roi_frame)

            #key = cv2.waitKey(1) & 0xFF
            #TODO: Autocapture and detecting 
            '''
            current_time = time.time()
        
            # Auto save every CAPTURE_INTERVAL seconds
            if current_time - last_capture_time >= CAPTURE_INTERVAL:
                filename = os.path.join(SAVE_DIR, "capture.png")
                cv2.imwrite(filename, roi_frame)  # Save ROI only
                print(f"Saved ROI: {filename}")
                last_capture_time = current_time
            '''
            
            current_time = time.time()
            if current_time - last_capture_time >= CAPTURE_INTERVAL:
                filename = os.path.join(SAVE_DIR, "capture.png")
                cv2.imwrite(filename, roi_frame)  # Save ROI only
                last_capture_time = current_time
                #print(f"Manually saved ROI: {filename}")
                result = read_capture(count)
                count += 1
#                data, prob = read_capture(count)

            if result is None or not isinstance(result, tuple) or len(result) != 2:
                print("Invalid read_capture() result.")
                data, prob = None, 0
                break
            else:
                data, prob = result
            
            if data is not None and prob > 0.5:
                df = pd.DataFrame(data)
                csv_filename = "display.csv"
                if os.path.exists(csv_filename):
                    df.to_csv(csv_filename, mode='a', header=False, index=False)
                else:
                    df.to_csv(csv_filename, mode='w', header=True, index=False)
                break

            #if key == ord('q'):
            #    break

    finally:
        cap.release()
        #cv2.destroyAllWindows()
        #print("Camera released and windows closed.")

def main():
    imu_read()
    
if __name__ == "__main__":
    main()

