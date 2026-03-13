#!/usr/bin/env python3
"""
ACT for Bump sensor -> test inside a Nix shell.
"""
import cv2
import easyocr
from PIL import Image, ImageOps
import numpy as np
import time

CAMERA_INDEX = 0
MAX_IDLE_TIME = 10.0

# Define ROI [y_start:y_end, x_start:x_end]
ROI = [100, 300, 200, 500]   # adjust based on your display position
def img_capture_2():
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("Error: Could not open camera.")
        return
    
    # Initialize EasyOCR
    reader = easyocr.Reader(['en'])
    start_time = time.time()

    flag = False
    try:
        while not flag:
            
            ret, frame = cap.read()
            if not ret:
                flag = True
                continue
            
            roi = frame[ROI[0]:ROI[1], ROI[2]:ROI[3]]
            
            # Convert OpenCV frame -> PIL (RGB)
            pil_img = Image.fromarray(cv2.cvtColor(roi, cv2.COLOR_BGR2RGB))

            # Invert colors (for white text on black displays)
            inverted_img = ImageOps.invert(pil_img)
            
            inv_frame = np.array(inverted_img)
            inv_frame = cv2.cvtColor(inv_frame, cv2.COLOR_RGB2BGR)  # Convert RGB (PIL) to BGR (OpenCV)

            cv2.rectangle(frame, (ROI[2], ROI[0]), (ROI[3], ROI[1]), (0, 255, 0), 2)
            #cv2.imshow("Camera", frame)

            # Run OCR
            results = reader.readtext(frame)
            for (bbox, text, prob) in results:
                text_clean = text.strip().lower()
                #print(f"Detected: {text} (Confidence: {prob:.2f})")
                if text.find("Right") != -1:
                    bump = "Right"
                    flag = True
                elif text.find("Left") != -1:
                    bump = "Left"
                    flag = True
                elif text_clean == "left":
                    #print(text_clean)
                    bump = "Left"
                    flag = True
                elif text_clean == "right":
                    #print(text_clean)
                    bump = "Right"
                    flag = True
                else:
                    bump = " "
                    flag = False
                    continue
            
            current_time = time.time()
            if current_time - start_time >= MAX_IDLE_TIME:
                flag = True
                bump = "None"
                    
            #if cv2.waitKey(1) & 0xFF == ord('q'):
            #    flag = True
            #    break
        
        print(bump)

    finally:
        cap.release()
        #cv2.destroyAllWindows()
        #print("Monitoring stopped.")

def main():
    img_capture_2()

if __name__ == "__main__":
    main()