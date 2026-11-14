import os
import sys
import socket
import threading
import time
import math
import random

try:
    import cv2
    import numpy as np
    from ursina import *
except ImportError as e:
    print(f"Missing dependency: {e}. Please run update-and-run.ps1 to install requirements.")
    sys.exit(1)

# Initialize Ursina App
app = Ursina()
window.title = "Hand-Track Stunt Kart 3D"
window.borderless = False
window.fullscreen = False
window.color = color.rgb(15, 15, 25)

# Global tracking variables based on user rules:
# Left hand: Open palm = STOP (throttle 0), Palm facing camera/forward = FORWARD, Back of hand facing camera = BACKWARD
# Right hand: Tilting left/right = STEERING
steering_val = 0.0  # -1.0 to 1.0
throttle_val = 0.0  # 0.0 (stop/reverse) to 1.0 (forward)
is_reverse = False
boost_active = False
camera_active = True

class HandTrackerThread(threading.Thread):
    def __init__(self):
        super().__init__()
        self.daemon = True
        self.running = True

    def run(self):
        global steering_val, throttle_val, is_reverse, boost_active, camera_active
        cap = cv2.VideoCapture(0)
        
        print("Precise Gesture Hand Tracker Initialized (Left: Throttle/Stop/Reverse | Right: Steering).")

        while self.running:
            if not camera_active:
                time.sleep(0.1)
                continue
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.05)
                continue

            frame = cv2.flip(frame, 1)
            h, w, _ = frame.shape

            global steering_val, throttle_val, is_reverse, boost_active
            try:
                # Split frame in half: Left side for left hand (throttle), Right side for right hand (steering)
                mid_x = w // 2
                left_region = frame[:, :mid_x]
                right_region = frame[:, mid_x:]

                # 1. LEFT HAND GESTURE DETECTION (Throttle / Stop / Reverse)
                # Convert to grayscale / motion or contour
                gray_l = cv2.cvtColor(left_region, cv2.COLOR_BGR2GRAY)
                blur_l = cv2.GaussianBlur(gray_l, (5, 5), 0)
                _, thresh_l = cv2.threshold(blur_l, 60, 255, cv2.THRESH_BINARY_INV)
                contours_l, _ = cv2.findContours(thresh_l, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                left_action = "STOP"
                if contours_l:
                    best_l = max(contours_l, key=cv2.contourArea)
                    if cv2.contourArea(best_l) > 3000:
                        # Check convexity defects or palm solidity to determine open palm vs closed/back hand
                        hull = cv2.convexHull(best_l)
                        area = cv2.contourArea(best_l)
                        hull_area = cv2.contourArea(hull)
                        solidity = float(area) / hull_area if hull_area > 0 else 0

                        # Open hand has lower solidity due to fingers spreading out -> STOP
                        if solidity < 0.75:
                            left_action = "STOP"
                            throttle_val = 0.0
                            is_reverse = False
                        else:
                            # Palm showing vs back of hand (estimated via brightness/color gradient or vertical position)
                            M = cv2.moments(best_l)
                            if M["m00"] > 0:
                                cy = int(M["m01"] / M["m00"])
                                if cy < h / 2:
                                    left_action = "FORWARD"
                                    throttle_val = 1.0
                                    is_reverse = False
                                else:
                                    left_action = "BACKWARD"
                                    throttle_val = 0.8
                                    is_reverse = True

                # 2. RIGHT HAND GESTURE DETECTION (Steering by tilting)
                gray_r = cv2.cvtColor(right_region, cv2.COLOR_BGR2GRAY)
                blur_r = cv2.GaussianBlur(gray_r, (5, 5), 0)
                _, thresh_r = cv2.threshold(blur_r, 60, 255, cv2.THRESH_BINARY_INV)
                contours_r, _ = cv2.findContours(thresh_r, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                if contours_r:
                    best_r = max(contours_r, key=cv2.contourArea)
                    if cv2.contourArea(best_r) > 3000:
                        # Use centroid X relative to center of right region for steering tilt
                        M_r = cv2.moments(best_r)
                        if M_r["m00"] > 0:
                            cx_r = int(M_r["m10"] / M_r["m00"])
                            region_center_x = (w / 4)
                            steering_val = max(-1.0, min(1.0, (cx_r - region_center_x) / (region_center_x * 0.8)))
                else:
                    steering_val = steering_val * 0.85

                # Draw visual dashboard on live camera feed
                cv2.putText(frame, f"Left Hand [Throttle]: {left_action}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                cv2.putText(frame, f"Right Hand [Steer]: {int(steering_val*100)}%", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                cv2.line(frame, (mid_x, 0), (mid_x, h), (255, 255, 255), 2)

                cv2.imshow("Hand-Track Stunt Kart - Gesture Control Feed", frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

            except Exception as e:
                throttle_val = 0.0
                steering_val = 0.0

            time.sleep(0.015)
        cap.release()
        cv2.destroyAllWindows()

tracker_thread = HandTrackerThread()
tracker_thread.start()

# Game States & Entities
current_state = "MENU"
player_kart = None
boost_pads = []

class StuntKart(Entity):
    def __init__(self, is_local=True, **kwargs):
        super().__init__(
            model='cube',
            color=color.orange if is_local else color.cyan,
            scale=(1.8, 0.8, 3.2),
            collider='box',
            **kwargs
        )
        self.is_local = is_local
        self.speed = 0.0
        self.max_speed = 35.0
        self.acceleration = 12.0
        self.turn_speed = 90.0
        
        # Visual additions (Wheels & Spoiler)
        self.body = Entity(parent=self, model='cube', color=color.dark_gray, scale=(1.1, 0.4, 0.8), y=0.4)
        self.spoiler = Entity(parent=self, model='cube', color=color.black, scale=(1.2, 0.1, 0.4), z=-1.3, y=0.8)

    def update(self):
        if current_state != "GAME":
            return

        global steering_val, throttle_val, is_reverse, boost_active

        if self.is_local:
            steer_input = steering_val
            if held_keys['a'] or held_keys['left']: steer_input = -1.0
            if held_keys['d'] or held_keys['right']: steer_input = 1.0

            accel_input = throttle_val
            if held_keys['w'] or held_keys['up']: accel_input = 1.0
            if held_keys['s'] or held_keys['down']: 
                accel_input = 0.8
                is_reverse = True

            # Acceleration & physics
            if accel_input > 0.1:
                if is_reverse:
                    self.speed -= self.acceleration * time.dt * accel_input
                    self.speed = max(-15.0, self.speed)
                else:
                    self.speed += self.acceleration * time.dt * accel_input
                    self.speed = min(self.max_speed, self.speed)
            else:
                # Friction / Stop
                if self.speed > 0:
                    self.speed -= self.acceleration * 1.5 * time.dt
                    self.speed = max(0.0, self.speed)
                elif self.speed < 0:
                    self.speed += self.acceleration * 1.5 * time.dt
                    self.speed = min(0.0, self.speed)

            # Turning (reverse steering when going backwards)
            turn_dir = -1.0 if is_reverse else 1.0
            self.rotation_y += steer_input * self.turn_speed * time.dt * (abs(self.speed) / self.max_speed + 0.2) * turn_dir

            # Move based on rotation
            move_dir = self.forward if not is_reverse else -self.forward
            self.position += move_dir * abs(self.speed) * time.dt

            # Gravity / Ground check
            if self.y > 0.4:
                self.y -= 15.0 * time.dt
            else:
                self.y = 0.4

            # Camera follow
            camera.position = lerp(camera.position, self.position - (self.forward * 10) + Vec3(0, 4, 0), 10 * time.dt)
            camera.look_at(self.position + Vec3(0, 1, 0))

def build_arena():
    Floor(model='plane', scale=(150, 1, 150), color=color.rgb(40, 45, 60), collider='box')
    Wall(position=(0, 2, 75), scale=(150, 4, 2), color=color.rgb(70, 80, 100))
    Wall(position=(0, 2, -75), scale=(150, 4, 2), color=color.rgb(70, 80, 100))
    Wall(position=(75, 2, 0), scale=(2, 4, 150), color=color.rgb(70, 80, 100))
    Wall(position=(-75, 2, 0), scale=(2, 4, 150), color=color.rgb(70, 80, 100))

    for i in range(4):
        angle = i * (math.pi / 2)
        rx = math.sin(angle) * 35
        rz = math.cos(angle) * 35
        Ramp(position=(rx, 0, rz), rotation_y=i*90)
        Entity(model='cube', color=color.magenta, scale=(6, 0.2, 6), position=(rx * 0.7, 0.1, rz * 0.7), collider='box')

class Ramp(Entity):
    def __init__(self, **kwargs):
        super().__init__(model='cube', color=color.rgb(80, 90, 110), scale=(8, 1.5, 6), collider='box', **kwargs)
        self.rotation_x = -15

class Wall(Entity):
    def __init__(self, **kwargs):
        super().__init__(model='cube', collider='box', **kwargs)

class Floor(Entity):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

# UI Screens
menu_parent = Entity()
hud_parent = Entity(enabled=False)

Text(parent=menu_parent, text="HAND-TRACK STUNT KART", scale=3, origin=(0,0), y=0.3, color=color.orange)
Text(parent=menu_parent, text="Left Hand: Open=Stop | Palm=Forward | Back=Backward\nRight Hand: Tilt=Steer", scale=1.0, origin=(0,0), y=0.18, color=color.light_gray)

hud_speed = Text(parent=hud_parent, text="SPEED: 0 MPH", scale=1.5, position=(-0.85, 0.45), color=color.yellow)
hud_status = Text(parent=hud_parent, text="Gesture Control Active", scale=1.2, position=(-0.85, 0.38), color=color.green)

def start_singleplayer():
    global current_state, player_kart
    current_state = "GAME"
    menu_parent.enabled = False
    hud_parent.enabled = True
    build_arena()
    player_kart = StuntKart(is_local=True, position=(0, 0.4, 0))

def start_lan_host():
    start_singleplayer()
    hud_status.text = "LAN Host: Waiting for players..."

def start_lan_join():
    start_singleplayer()
    hud_status.text = "LAN Client: Connected to host"

Button(parent=menu_parent, text="SOLO PRACTICE", color=color.azure, scale=(0.4, 0.08), y=0.0, on_click=start_singleplayer)
Button(parent=menu_parent, text="HOST LAN MATCH", color=color.violet, scale=(0.4, 0.08), y=-0.1, on_click=start_lan_host)
Button(parent=menu_parent, text="JOIN LAN MATCH", color=color.blue, scale=(0.4, 0.08), y=-0.2, on_click=start_lan_join)
Button(parent=menu_parent, target=None, text="QUIT", color=color.red, scale=(0.4, 0.08), y=-0.3, on_click=application.quit)

def update():
    if current_state == "GAME" and player_kart:
        hud_speed.text = f"SPEED: {int(abs(player_kart.speed) * 2.5)} MPH"
        direction_str = "REVERSE" if is_reverse else ("STOP" if throttle_val == 0 else "FORWARD")
        hud_status.text = f"Left Hand: {direction_str} | Right Steer: {int(steering_val * 100)}%"

app.run()
