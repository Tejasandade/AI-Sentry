import cv2
import threading
import time
import os
import requests
import face_recognition
import numpy as np
from flask import Flask, jsonify, render_template, Response, request
from flask_cors import CORS
from playsound import playsound

# =========================================================
# GLOBAL STATE & CONFIG
# =========================================================
TELEGRAM_BOT_TOKEN = '8616168222:AAGyvjrmS5FoywrP4C3QQNmgbp31Vb1yB1A'
TELEGRAM_CHAT_ID = '7372624003'
system_state = {
    "status": "idle",
    "name": ""
}
state_lock = threading.Lock()
global_sentry = None

def update_global_state(status, name=""):
    """Thread-safe state update."""
    global system_state
    with state_lock:
        system_state["status"] = status
        system_state["name"] = name

# =========================================================
# FLASK WEB SERVER (UI)
# =========================================================
app = Flask(__name__)
# Enable CORS so the React/LiveServer UI can fetch data from port 5000
CORS(app)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/remote')
def remote():
    return render_template('remote.html')

@app.route('/state')
def get_state():
    with state_lock:
        return jsonify(system_state)

def gen_frames():
    """Generator function for MJPEG streaming (capped at ~20 FPS)."""
    while True:
        if global_sentry is None or global_sentry.latest_annotated_frame is None:
            time.sleep(0.05)
            continue
            
        with global_sentry.annotated_frame_lock:
            frame_copy = global_sentry.latest_annotated_frame.copy()
            
        # Optimization: Resize and Center-Crop for a true Vertical (Portrait) view
        # We take the middle 9:16 area of the horizontal frame
        h, w = frame_copy.shape[:2]
        target_w = int(h * (9 / 16))
        start_x = (w - target_w) // 2
        frame_vertical = frame_copy[0:h, start_x:start_x+target_w]

        # Optimization: Scale for bandwidth efficiency (target width 360px portrait)
        h_v, w_v = frame_vertical.shape[:2]
        new_w = 360
        new_h = int(h_v * (new_w / w_v))
        frame_ready = cv2.resize(frame_vertical, (new_w, new_h), interpolation=cv2.INTER_AREA)

        # Optimization: JPEG quality at 65 for a better balance
        ret, buffer = cv2.imencode('.jpg', frame_ready, [cv2.IMWRITE_JPEG_QUALITY, 65])
        if not ret:
            continue
            
        frame_bytes = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        time.sleep(0.05)

@app.route('/video_feed')
def video_feed():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/control/<motor>/<direction>')
def control_motor(motor, direction):
    # Dummy motor control logic, to be replaced with actual GPIO or Serial instructions
    print(f"[*] MOTOR CONTROL -> {motor.upper()} : {direction.upper()}")
    return jsonify({"status": "success", "motor": motor, "direction": direction})

@app.route('/audio_command', methods=['POST'])
def audio_command():
    if 'audio' not in request.files:
        return jsonify({"status": "error", "message": "No audio file provided"}), 400
        
    audio_file = request.files['audio']
    if audio_file.filename == '':
        return jsonify({"status": "error", "message": "Empty filename"}), 400
        
    # Save the audio file temporarily
    filepath = os.path.join(os.getcwd(), 'last_command.webm')
    audio_file.save(filepath)
    print(f"[*] Received Voice Command: {filepath}")
    
    # Play the audio asynchronously so we don't block the server
    def play_audio():
        try:
            print(f"[*] Attempting to play voice command: {filepath}")
            # Note: playsound on Windows can be picky about formats. 
            # If webm fails, we might need a converter or a different library like pydub/sounddevice.
            playsound(filepath)
            print("[*] Voice command played successfully.")
        except Exception as e:
            print(f"[!] Critical audio playback error: {e}")
            print("[!] Suggestion: If this persists, we may need to install ffmpeg and use pydub for conversion.")
            
    threading.Thread(target=play_audio, daemon=True).start()
    
    return jsonify({"status": "success", "message": "Audio received and playing"})

def send_telegram_alert(filepath):
    """Sends a photo to Telegram."""
    print(f"[*] Sending Telegram Alert to {TELEGRAM_CHAT_ID}...")
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        with open(filepath, 'rb') as photo:
            # Add an Inline Keyboard button to open the Remote Dashboard
            # Replace 192.168.0.100 with your actual local IP where vision.py is running
            remote_url = "http://192.168.0.100:5000/remote"
            
            payload = {
                'chat_id': TELEGRAM_CHAT_ID, 
                'caption': '🚨 *AI SENTRY: INTRUDER DETECTED!*\n\nTap below to open the Live Remote Dashboard:',
                'parse_mode': 'Markdown',
                'reply_markup': '{"inline_keyboard": [[{"text": "🔴 Open Remote Dashboard", "url": "' + remote_url + '"}]]}'
            }
            files = {'photo': photo}
            response = requests.post(url, data=payload, files=files)
            if response.status_code == 200:
                print("[*] Telegram Alert Sent Successfully!")
            else:
                print(f"[!] Telegram API Error: {response.text}")
    except Exception as e:
        print(f"[!] Failed to send Telegram alert: {e}")

def run_flask_server():
    """Run Flask in a background thread."""
    # use_reloader=False is critical when running inside a thread
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

# =========================================================
# VIDEO STREAM HANDLING
# =========================================================
class VideoStreamWidget:
    """Reads frames in a background thread to prevent buffer delays."""
    def __init__(self, src=0):
        self.capture = cv2.VideoCapture(src)
        
        # CRITICAL FIX FOR LAG: Minimize the internal OpenCV buffer size!
        # If the buffer queue is full, OpenCV gives us old frames instead of the current one.
        self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        self.status, self.frame = self.capture.read()
        self.running = True
        
        self.thread = threading.Thread(target=self.update, args=())
        self.thread.daemon = True
        self.thread.start()

    def update(self):
        while self.running:
            if self.capture.isOpened():
                self.status, self.frame = self.capture.read()
            else:
                self.running = False

    def read(self):
        return self.status, self.frame

    def release(self):
        self.running = False
        self.thread.join()
        self.capture.release()

# =========================================================
# SENTRY CORE LOGIC
# =========================================================
class SentryCore:
    def __init__(self, stream_url, known_faces_dir='known_faces'):
        global global_sentry
        global_sentry = self
        
        self.stream_url = stream_url
        self.known_faces_dir = known_faces_dir
        self.known_face_encodings = []
        self.known_face_names = []
        
        # AI Worker Thread States
        self.latest_frame_for_ai = None
        self.ai_lock = threading.Lock()
        
        # UI & Streaming States
        self.latest_annotated_frame = None
        self.annotated_frame_lock = threading.Lock()
        
        self.current_face_locations = []
        self.current_face_names = []
        
        self.running = True
        
        # Security/Logic Parameters
        self.intruder_grace_frames = 15  # Increased grace period so it doesn't fire prematurely when jittering
        self.consecutive_unknown_count = 0
        self.last_capture_time = 0.0
        
        self.load_known_faces()

    def load_known_faces(self):
        print(f"[*] Loading known faces from '{self.known_faces_dir}'...")
        if not os.path.exists(self.known_faces_dir):
            os.makedirs(self.known_faces_dir)
            
        count = 0
        for filename in os.listdir(self.known_faces_dir):
            if filename.lower().endswith(('.jpg', '.jpeg', '.png')):
                filepath = os.path.join(self.known_faces_dir, filename)
                name = os.path.splitext(filename)[0]
                
                try:
                    image = face_recognition.load_image_file(filepath)
                    encodings = face_recognition.face_encodings(image)
                    if len(encodings) > 0:
                        self.known_face_encodings.append(encodings[0])
                        self.known_face_names.append(name)
                        print(f"  -> Encoded: {name}")
                        count += 1
                    else:
                        print(f"  -> WARNING: No face found in {filename}.")
                except Exception as e:
                    print(f"  -> Error processing {filename}: {e}")
                    
        print(f"[*] Loaded {count} known faces.")

    def ai_worker_thread(self):
        """Dedicated background thread for heavy Face Recognition processing."""
        print("[*] AI Worker Thread started.")
        while self.running:
            # 1. Safely grab the latest frame
            frame_to_process = None
            with self.ai_lock:
                if self.latest_frame_for_ai is not None:
                    # Make a hard copy to avoid race conditions with main thread
                    # Resolving Lint: Explicitly checking NoneType before copying
                    try:
                        frame_to_process = self.latest_frame_for_ai.copy()
                    except AttributeError:
                        pass
                    self.latest_frame_for_ai = None # Clear it so we don't process the same frame twice

            # 2. If no new frame, sleep briefly and try again
            if frame_to_process is None:
                time.sleep(0.01)
                continue

            # 3. Perform AI Face Recognition (Heavy workload ~0.1s to 0.5s)
            # Resize frame to 1/4 size for faster face recognition processing
            # Optimization: 0.5 scale (was 0.25) and upsample=1 for better accuracy
            small_frame = cv2.resize(frame_to_process, (0, 0), fx=0.5, fy=0.5)
            rgb_small_frame = np.ascontiguousarray(small_frame[:, :, ::-1])

            face_locations = face_recognition.face_locations(rgb_small_frame, number_of_times_to_upsample=1)
            face_encodings = face_recognition.face_encodings(rgb_small_frame, face_locations)

            face_names = []
            
            # --- Logic: Handle Intruder Grace Period ---
            if len(face_locations) == 0:
                update_global_state("idle", "")
                self.consecutive_unknown_count = 0 # Reset counter if no one is seen
            else:
                for face_encoding in face_encodings:
                    matches = face_recognition.compare_faces(self.known_face_encodings, face_encoding, tolerance=0.55)
                    name = "Intruder"

                    # Find the closest match
                    if len(self.known_face_encodings) > 0:
                        face_distances = face_recognition.face_distance(self.known_face_encodings, face_encoding)
                        best_match_index = np.argmin(face_distances)
                        if matches[best_match_index]:
                            name = self.known_face_names[best_match_index]

                    face_names.append(name)

                # Determine overall system state
                if "Intruder" in face_names and len([n for n in face_names if n != "Intruder"]) == 0:
                    # Only pure intruders in frame (Handling Grace Period)
                    self.consecutive_unknown_count += 1
                    
                    if self.consecutive_unknown_count >= self.intruder_grace_frames:
                        update_global_state("unknown", "Intruder")
                        
                        # Trigger Intruder Capture for Telegram (Max 1 per 60 seconds)
                        current_time = time.time()
                        if current_time - getattr(self, 'last_capture_time', 0) > 60:
                            if not os.path.exists('intruders'):
                                os.makedirs('intruders')
                            
                            timestamp = time.strftime("%Y%m%d-%H%M%S")
                            filepath = f"intruders/intruder_{timestamp}.jpg"
                            cv2.imwrite(filepath, frame_to_process)
                            print(f"[!] INTRUDER CONFIRMED. Captured evidence to: {filepath}")
                            self.last_capture_time = current_time
                            
                            # Trigger Telegram asynchronously to not block AI thread
                            threading.Thread(target=send_telegram_alert, args=(filepath,), daemon=True).start()
                    else:
                        # We see an intruder, but waiting to be absolutely sure before triggering alarm
                        pass 
                else:
                    # Known user detected in the frame
                    self.consecutive_unknown_count = 0 # Reset intruder counter
                    # Prioritize displaying the known user's name
                    known_name = next(name for name in face_names if name != "Intruder")
                    update_global_state("known", known_name)

            # 4. Safely update the final tracking variables for the Main Thread to draw
            with self.ai_lock:
                self.current_face_locations = face_locations
                self.current_face_names = face_names
            
            # CRITICAL LAG FIX: Force the AI thread to rest so the OS
            # has enough CPU power to draw the video frames smoothly.
            # 80ms = ~12 face-recognition cycles per second, which is more than enough.
            time.sleep(0.08)


    def run(self):
        print("\n[*] Starting Sentry System...")
        print(f"[*] Camera Stream: {self.stream_url}")
        print("[*] Web UI available at: http://localhost:5000")
        print("[*] Press 'q' in the video window to quit.\n")
        
        # 1. Start Flask UI Thread
        flask_thread = threading.Thread(target=run_flask_server)
        flask_thread.daemon = True
        flask_thread.start()

        # 2. Start AI Worker Thread
        ai_thread = threading.Thread(target=self.ai_worker_thread)
        ai_thread.daemon = True
        ai_thread.start()

        # 3. Start Video Stream Thread
        cap = VideoStreamWidget(self.stream_url)
        time.sleep(1) # Give camera time to warm up

        if not cap.status:
            print(f"[!] Error: Could not open video stream at {self.stream_url}")
            cap.release()
            self.running = False
            return

        print("[*] Main Video Loop running seamlessly at target FPS.")
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                continue

            # Pass the newest frame to the AI worker if it's ready for one
            with self.ai_lock:
                # We only overwrite if the worker grabbed the previous one, or if we just want to drop old frames
                self.latest_frame_for_ai = frame

                # Grab the LATEST bounding box math from the AI thread to draw
                draw_locations = self.current_face_locations.copy()
                draw_names = self.current_face_names.copy()

            # Draw bounding boxes
            for (top, right, bottom, left), name in zip(draw_locations, draw_names):
                # Scale back up (using factor 2 since AI frame is now 0.5 size)
                top *= 2
                right *= 2
                bottom *= 2
                left *= 2

                # Choose color based on whether it's an intruder
                color = (0, 0, 255) if name == "Intruder" else (0, 255, 0) # Red or Green (BGR)

                cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
                cv2.rectangle(frame, (left, bottom - 35), (right, bottom), color, cv2.FILLED)
                cv2.putText(frame, name, (left + 6, bottom - 6), cv2.FONT_HERSHEY_DUPLEX, 0.8, (255, 255, 255), 1)

            # Keep a copy of the fully drawn frame for MJPEG Streaming
            with self.annotated_frame_lock:
                self.latest_annotated_frame = frame.copy()

            # Show video output smoothly (capped to ~25 FPS)
            cv2.imshow('AI Sentry Platform - Phase 3', frame)

            # Cap the main loop to 25 FPS to avoid spinning the CPU at 100%
            if cv2.waitKey(40) & 0xFF == ord('q'):
                print("\n[*] Shutting down Sentry Core...")
                break

        # Cleanup
        self.running = False
        cap.release()
        cv2.destroyAllWindows()

# =========================================================
# ENTRY POINT
# =========================================================
if __name__ == '__main__':
    # Stream endpoint from Android IP Webcam app
    camera_stream_url = 'http://192.168.0.104:8080/video'
    
    # Initialize and run the system
    sentry = SentryCore(stream_url=camera_stream_url)
    sentry.run()
