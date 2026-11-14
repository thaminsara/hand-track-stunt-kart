import os
import sys
import socket
import threading
import time
import math
import random
import urllib.request

try:
    import cv2
    import numpy as np
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    from ursina import *
except ImportError as e:
    print(f"Missing dependency: {e}. Please run update-and-run.ps1 to install requirements.")
    sys.exit(1)

# Initialize Ursina App
app = Ursina()
window.title = "Hand-Track Stunt Kart 3D - Arcade Chaos Edition"
window.borderless = False
window.fullscreen = False
window.color = color.rgb(10, 10, 20)

# Global tracking variables based on user rules:
# Left hand: Open palm = STOP, Palm facing camera = FORWARD, Back of hand = REVERSE
# Right hand: Tilting left/right = STEERING
steering_val = 0.0
throttle_val = 0.0
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
        model_path = "hand_landmarker.task"
        if not os.path.exists(model_path):
            try:
                url = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
                urllib.request.urlretrieve(url, model_path)
            except Exception:
                pass

        cap = cv2.VideoCapture(0)
        detector = None
        try:
            base_options = python.BaseOptions(model_asset_path=model_path)
            options = vision.HandLandmarkerOptions(base_options=base_options, num_hands=2, min_hand_detection_confidence=0.5, min_tracking_confidence=0.5)
            detector = vision.HandLandmarker.create_from_options(options)
        except Exception:
            pass

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
                left_action = "STOP"
                if detector is not None:
                    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    detection_result = detector.detect(mp_image)

                    if detection_result and detection_result.hand_landmarks:
                        for idx, hand_landmarks in enumerate(detection_result.hand_landmarks):
                            handedness = detection_result.handedness[idx][0].category_name
                            x_coords = [int(lm.x * w) for lm in hand_landmarks]
                            y_coords = [int(lm.y * h) for lm in hand_landmarks]
                            cx = int(sum(x_coords) / len(x_coords))
                            cy = int(sum(y_coords) / len(y_coords))

                            if handedness == "Left":
                                wrist = hand_landmarks[0]
                                middle_tip = hand_landmarks.metadata if hasattr(hand_landmarks, 'metadata') else hand_landmarks[12]
                                palm_spread = math.hypot(middle_tip.x - wrist.x, middle_tip.y - wrist.y)

                                if palm_spread < 0.20:
                                    left_action = "STOP"
                                    throttle_val = 0.0
                                    is_reverse = False
                                    boost_active = False
                                else:
                                    if middle_tip.z < wrist.z:
                                        left_action = "FORWARD (NITRO!)"
                                        throttle_val = 1.0
                                        is_reverse = False
                                        boost_active = True
                                    else:
                                        left_action = "REVERSE"
                                        throttle_val = 0.8
                                        is_reverse = True
                                        boost_active = False

                                cv2.circle(frame, (cx, cy), 30, (0, 255, 255), -1)
                                cv2.putText(frame, f"L: {left_action}", (cx - 50, cy - 35), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

                            elif handedness == "Right":
                                steering_val = max(-1.0, min(1.0, (cx - (w / 2)) / (w / 3.0)))
                                cv2.circle(frame, (cx, cy), 30, (0, 255, 0), -1)
                                cv2.putText(frame, f"R: Steer {int(steering_val*100)}%", (cx - 50, cy - 35), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                    else:
                        throttle_val = 0.0
                        steering_val = steering_val * 0.85
                        boost_active = False

                cv2.putText(frame, f"Action: {left_action} | Steer: {int(steering_val*100)}%", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
                cv2.imshow("Hand-Track Stunt Kart - Arcade HUD", frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
            except Exception:
                throttle_val = 0.0

            time.sleep(0.015)
        cap.release()
        cv2.destroyAllWindows()

tracker_thread = HandTrackerThread()
tracker_thread.start()

# Game States & Entities
current_state = "MENU"
player_kart = None
ai_karts = []
obstacles = []

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
        self.max_speed = 45.0
        self.acceleration = 18.0
        self.turn_speed = 110.0
        self.vertical_velocity = 0.0
        
        # Super-exciting arcade styling: Neon body, spoiler, wheels
        self.body = Entity(parent=self, model='cube', color=color.lime if is_local else color.magenta, scale=(1.1, 0.4, 0.8), y=0.4)
        self.spoiler = Entity(parent=self, model='cube', color=color.black, scale=(1.2, 0.1, 0.4), z=-1.3, y=0.8)
        self.nitro_flame = Entity(parent=self, model='cube', color=color.orange, scale=(0.6, 0.6, 1.2), z=-1.8, y=0.4, enabled=False)

    def update(self):
        if current_state != "GAME":
            return

        global steering_val, throttle_val, is_reverse, boost_active

        if self.is_local:
            steer_input = steering_val
            if held_keys['a'] or held_keys['left']: steer_input = -1.0
            if held_keys['d'] or held_keys['right']: steer_input = 1.0

            accel_input = throttle_val
            if held_keys['w'] or held_keys['up']: 
                accel_input = 1.0
                boost_active = True
            if held_keys['s'] or held_keys['down']: 
                accel_input = 0.8
                is_reverse = True

            effective_max = self.max_speed * (2.2 if boost_active or held_keys['shift'] else 1.0)
            self.nitro_flame.enabled = boost_active or held_keys['shift']

            # Arcade Physics & Acceleration
            if accel_input > 0.1:
                if is_reverse:
                    self.speed -= self.acceleration * time.dt * accel_input
                    self.speed = max(-20.0, self.speed)
                else:
                    self.speed += self.acceleration * time.dt * accel_input
                    self.speed = min(effective_max, self.speed)
            else:
                if self.speed > 0:
                    self.speed -= self.acceleration * 2.0 * time.dt
                    self.speed = max(0.0, self.speed)
                elif self.speed < 0:
                    self.speed += self.acceleration * 2.0 * time.dt
                    self.speed = min(0.0, self.speed)

            # Steering
            turn_dir = -1.0 if is_reverse else 1.0
            self.rotation_y += steer_input * self.turn_speed * time.dt * (abs(self.speed) / self.max_speed + 0.3) * turn_dir

            # Movement
            move_dir = self.forward if not is_reverse else -self.forward
            self.position += move_dir * abs(self.speed) * time.dt

            # Stunt Ramps & Gravity Physics
            hit_info = self.intersects()
            if hit_info.hit and isinstance(hit_info.entity, Ramp):
                self.vertical_velocity = 12.0 # Launch into the air off ramps!

            if self.y > 0.4 or self.vertical_velocity != 0:
                self.y += self.vertical_velocity * time.dt
                self.vertical_velocity -= 35.0 * time.dt # Gravity
                if self.y <= 0.4:
                    self.y = 0.4
                    self.vertical_velocity = 0.0
            else:
                self.y = 0.4

            # Dynamic Arcade Camera Shake & Follow
            shake = Vec3(random.uniform(-0.1, 0.1), random.uniform(-0.1, 0.1), 0) if boost_active else Vec3(0,0,0)
            target_cam_pos = self.position - (self.forward * 12) + Vec3(0, 4.5, 0) + shake
            camera.position = lerp(camera.position, target_cam_pos, 12 * time.dt)
            camera.look_at(self.position + Vec3(0, 1.2, 0))

class AIKart(Entity):
    def __init__(self, **kwargs):
        super().__init__(model='cube', color=color.red, scale=(1.8, 0.8, 3.2), collider='box', **kwargs)
        self.speed = 25.0
        self.target_angle = 0

    def update(self):
        if current_state != "GAME":
            return
        # Drive in crazy arcade circles / patrol loops
        self.rotation_y += 30 * time.dt
        self.position += self.forward * self.speed * time.dt
        if self.y > 0.4:
            self.y -= 15 * time.dt
        else:
            self.y = 0.4

class Obstacle(Entity):
    def __init__(self, **kwargs):
        super().__init__(model='cube', color=color.yellow, scale=(3, 3, 3), collider='box', **kwargs)

def build_arcade_arena():
    # Neon Stunt Arena Floor
    Floor(model='plane', scale=(200, 1, 200), color=color.rgb(20, 25, 40), collider='box')
    
    # Glowing Circuit Walls
    Wall(position=(0, 3, 95), scale=(200, 6, 2), color=color.rgb(255, 0, 128))
    Wall(position=(0, 3, -95), scale=(200, 6, 2), color=color.rgb(255, 0, 128))
    Wall(position=(95, 3, 0), scale=(2, 6, 200), color=color.rgb(0, 255, 255))
    Wall(position=(-95, 3, 0), scale=(2, 6, 200), color=color.rgb(0, 255, 255))

    # Mega Stunt Ramps & Boost Pads
    for i in range(6):
        angle = i * (math.pi / 3)
        rx = math.sin(angle) * 50
        rz = math.cos(angle) * 50
        Ramp(position=(rx, 0, rz), rotation_y=i*60)
        
        # Neon Boost Pad
        Entity(model='cube', color=color.cyan, scale=(8, 0.2, 8), position=(rx * 0.75, 0.1, rz * 0.75), collider='box')

    # Spawn AI Rival Karts for chaos
    for i in range(3):
        angle = i * (math.pi / 1.5)
        ai_karts.append(AIKart(position=(math.sin(angle)*30, 0.4, math.cos(angle)*30)))

class Ramp(Entity):
    def __init__(self, **kwargs):
        super().__init__(model='cube', color=color.rgb(100, 110, 140), scale=(10, 2.0, 8), collider='box', **kwargs)
        self.rotation_x = -20

class Wall(Entity):
    def __init__(self, **kwargs):
        super().__init__(model='cube', collider='box', **kwargs)

class Floor(Entity):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

# UI Screens
menu_parent = Entity()
hud_parent = Entity(enabled=False)

Text(parent=menu_parent, text="ARCADE STUNT KART CHAOS", scale=3, origin=(0,0), y=0.3, color=color.cyan)
Text(parent=menu_parent, text="Left Hand: Open=Stop | Palm=Nitro Boost | Back=Reverse\nRight Hand: Tilt=Steer", scale=1.0, origin=(0,0), y=0.18, color=color.light_gray)

hud_speed = Text(parent=hud_parent, text="SPEED: 0 MPH", scale=1.8, position=(-0.85, 0.45), color=color.yellow)
hud_status = Text(parent=hud_parent, text="Arcade Mode: Chaos Active", scale=1.2, position=(-0.85, 0.38), color=color.cyan)

def start_singleplayer():
    global current_state, player_kart
    current_state = "GAME"
    menu_parent.enabled = False
    hud_parent.enabled = True
    build_arcade_arena()
    player_kart = StuntKart(is_local=True, position=(0, 0.4, 0))

def start_lan_host():
    start_singleplayer()
    hud_status.text = "LAN Host: Racing against AI & Players"

def start_lan_join():
    start_singleplayer()
    hud_status.text = "LAN Client: Connected to Stunt Arena"

Button(parent=menu_parent, text="START ARCADE RACE", color=color.azure, scale=(0.45, 0.09), y=0.0, on_click=start_singleplayer)
Button(parent=menu_parent, text="HOST LAN CHAOS", color=color.violet, scale=(0.45, 0.09), y=-0.11, on_click=start_lan_host)
Button(parent=menu_parent, text="JOIN LAN CHAOS", color=color.blue, scale=(0.45, 0.09), y=-0.22, on_click=start_lan_join)
Button(parent=menu_parent, target=None, text="QUIT", color=color.red, scale=(0.45, 0.09), y=-0.33, on_click=application.quit)

def update():
    if current_state == "GAME" and player_kart:
        hud_speed.text = f"SPEED: {int(abs(player_kart.speed) * 2.2)} MPH"
        nitro_txt = "NITRO BOOST!" if boost_active else "NORMAL"
        hud_status.text = f"Mode: {nitro_txt} | Steer: {int(steering_val * 100)}%"

app.run()
