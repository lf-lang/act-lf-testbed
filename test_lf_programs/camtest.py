from picamera2 import Picamera2
import cv2
import time
import os

SAVE_DIR = "captures"
JPEG_QUALITY = 90

os.makedirs(SAVE_DIR, exist_ok=True)

cam0 = Picamera2(0)
cam1 = Picamera2(1)
for cam in (cam0, cam1):
    cam.configure(cam.create_video_configuration(
        main={"size": (1280, 720), "format": "RGB888"}))
    cam.start()

print("Cameras started, settling for 5 seconds...")
time.sleep(5)

frame0 = cam0.capture_array()
frame1 = cam1.capture_array()

ts = time.strftime("%Y%m%d_%H%M%S")
p0 = os.path.join(SAVE_DIR, f"cam0_{ts}.jpg")
p1 = os.path.join(SAVE_DIR, f"cam1_{ts}.jpg")
cv2.imwrite(p0, frame0, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
cv2.imwrite(p1, frame1, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
print(f"Saved {p0}")
print(f"Saved {p1}")

for cam in (cam0, cam1):
    cam.stop()
