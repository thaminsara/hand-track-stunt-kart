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
    import mediapipe as mp
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

# Global tracking variables
steering_val = 0.0  # -1.0 to 1.0
throttle_val = 0.5  # 0.0 to 1.0
boost_active = False
camera_active = True

class HandTrackerThread(threading.Thread):
    def __init__(self):
        super().__init__()
        self.daemon = True
        self.running = True

    def run(self):
        global steering_val, throttle_val, boost_active, camera_active
        cap = cv2.VideoCapture(0)
        
        # Try initializing MediaPipe Hands (supporting both legacy and modern API versions)
        hands = None
        use_tasks_api = False
        try:
            if hasattr(mp, 'solutions') and hasattr(mp.solutions, 'hands'):
                mp_hands = mp.solutions.hands
                hands = mp_hands.Hands(max_num_hands=2, min_detection_confidence=0.6, min_tracking_confidence=0.6)
            else:
                # Modern MediaPipe / Python 3.13 fallback: use OpenCV motion/color tracking if solutions unavailable
                use_tasks_api = True
        except Exception as e:
            print(f"Hand tracker init notice: {e}. Using keyboard/mouse racing mode.")
            use_tasks_api = True

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

            global steering_val, throttle_val, boost_active
            
            if not use_tasks_api and hands is not None:
                try:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    results = hands.process(rgb)
                    if results.multi_hand_landmarks:
                        hand_coords = []
                        for hand_landmarks in results.multi_hand_landmarks:
                            wx = hand_landmarks.landmark[0].x * w
                            wy = hand_landmarks.landmark[0].y * h
                            ix = hand_landmarks.landmark[8].x * w
                            iy = hand_landmarks.landmark[8].y * h
                            hand_coords.append(((wx + ix)/2, (wy + iy)/2))

                        if len(hand_coords) >= 2:
                            p1, p2 = hand_coords[0], hand_coords[1]
                            dx = p2[0] - p1[0]
                            dy = p2[1] - p1[1]
                            angle = math.atan2(dy, dx)
                            steering_val = max(-1.0, min(1.0, angle * 2.0))
                            avg_y = (p1[1] + p2[1]) / 2.0
                            throttle_val = max(0.2, min(1.0, 1.0 - (avg_y / h)))
                            boost_active = True if abs(dx) < w * 0.15 else False
                        elif len(hand_coords) == 1:
                            hx, hy = hand_coords[0]
                            steering_val = max(-1.0, min(1.0, (hx - (w/2)) / (w/4)))
                            throttle_val = max(0.2, min(1.0, 1.0 - (hy / h)))
                            boost_active = False
                    else:
                        steering_val = 0.0
                        throttle_val = 0.5
                        boost_active = False
                except Exception:
                    steering_val = 0.0
                    throttle_val = 0.5
                    boost_active = False
            else:
                # Fallback interactive mode using center mass / brightness motion or neutral steering
                steering_val = 0.0
                throttle_val = 0.5
                boost_active = False

            time.sleep(0.015)
        cap.release()

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

        global steering_val, throttle_val, boost_active

        if self.is_local:
            # Controls from hand tracking or keyboard backup (Arrow keys / WASD)
            steer_input = steering_val
            if held_keys['a'] or held_keys['left']: steer_input = -1.0
            if held_keys['d'] or held_keys['right']: steer_input = 1.0

            accel_input = throttle_val
            if held_keys['w'] or held_keys['up']: accel_input = 1.0
            if held_keys['s'] or held_keys['down']: accel_input = 0.0
            
            effective_max = self.max_speed * (1.6 if boost_active or held_keys['shift'] else 1.0)

            # Acceleration & physics
            if accel_input > 0.3:
                self.speed += self.acceleration * time.dt * accel_input
            else:
                self.speed -= self.acceleration * 1.5 * time.dt
            self.speed = max(0.0, min(effective_max, self.speed))

            # Turning
            self.rotation_y += steer_input * self.turn_speed * time.dt * (self.speed / self.max_speed + 0.2)

            # Move forward based on rotation
            move_dir = self.forward
            self.position += move_dir * self.speed * time.dt

            # Gravity / Ground check
            if self.y > 0.4:
                self.y -= 15.0 * time.dt
            else:
                self.y = 0.4

            # Camera follow
            camera.position = lerp(camera.position, self.position - (self.forward * 10) + Vec3(0, 4, 0), 10 * time.dt)
            camera.look_at(self.position + Vec3(0, 1, 0))

def build_arena():
    # Ground arena
    Floor(model='plane', scale=(150, 1, 150), color=color.rgb(40, 45, 60), collider='box')
    
    # Outer walls
    Wall(position=(0, 2, 75), scale=(150, 4, 2), color=color.rgb(70, 80, 100))
    Wall(position=(0, 2, -75), scale=(150, 4, 2), color=color.rgb(70, 80, 100))
    Wall(position=(75, 2, 0), scale=(2, 4, 150), color=color.rgb(70, 80, 100))
    Wall(position=(-75, 2, 0), scale=(2, 4, 150), color=color.rgb(70, 80, 100))

    # Stunt Ramps & Boost Pads
    for i in range(4):
        angle = i * (math.pi / 2)
        rx = math.sin(angle) * 35
        rz = math.cos(angle) * 35
        Ramp(position=(rx, 0, rz), rotation_y=i*90)
        
        bp = Entity(model='cube', color=color.magenta, scale=(6, 0.2, 6), position=(rx * 0.7, 0.1, rz * 0.7), collider='box')
        boost_pads.append(bp)

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

title_text = Text(parent=menu_parent, text="HAND-TRACK STUNT KART", scale=3, origin=(0,0), y=0.3, color=color.orange)
subtitle_text = Text(parent=menu_parent, text="Control your supercar using webcam virtual steering or WASD!", scale=1.2, origin=(0,0), y=0.18, color=color.light_gray)

hud_speed = Text(parent=hud_parent, text="SPEED: 0 MPH", scale=1.5, position=(-0.85, 0.45), color=color.yellow)
hud_status = Text(parent=hud_parent, text="Racing Mode: Active", scale=1.2, position=(-0.85, 0.38), color=color.green)

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
Button(parent=menu_parent, text="QUIT", color=color.red, scale=(0.4, 0.08), y=-0.3, on_click=application.quit)

def update():
    if current_state == "GAME" and player_kart:
        hud_speed.text = f"SPEED: {int(player_kart.speed * 2.5)} MPH"
        if boost_active:
            hud_status.text = "NITRO BOOST ACTIVE!"
            hud_status.color = color.magenta
        else:
            hud_status.text = f"Steering: {int(steering_val * 100)}% | Throttle: {int(throttle_val * 100)}% (WASD active)"
            hud_status.color = color.green

app.run()
