import os
# [CRITICAL FIX] Disable multi-threading in BLAS/OpenMP so Dlib/Numpy doesn't starve the web server UI threads!
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import cv2
import threading
import time
import requests
import face_recognition
import numpy as np
import mediapipe as mp
import multiprocessing as mp_lib
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
    "name": "",
    "hardware": "unconfigured"
}
state_lock = threading.Lock()
global_sentry = None

def update_global_state(status=None, name=None, hardware=None):
    """Thread-safe state update."""
    global system_state
    with state_lock:
        if status is not None:
            system_state["status"] = status
        if name is not None:
            system_state["name"] = name
        if hardware is not None:
            system_state["hardware"] = hardware

def get_local_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

def telegram_polling_thread():
    print("[*] Starting Telegram Bot listener...")
    offset = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"timeout": 30}
            if offset:
                params["offset"] = offset
            response = requests.get(url, params=params, timeout=35)
            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        message = update.get("message")
                        if message and "text" in message:
                            text = message["text"].strip()
                            chat_id = message["chat"]["id"]
                            if text == "/ip" or text == "/status":
                                local_ip = get_local_ip()
                                msg = "🤖 *AI Sentry Network Status*\n\n"
                                msg += f"😐 *Emo Face UI:* http://{local_ip}:5000/\n"
                                msg += f"📱 *Remote Dashboard:* http://{local_ip}:5000/remote\n"
                                if global_sentry and getattr(global_sentry, 'esp32_ip', None):
                                    msg += f"⚙️ *Hardware IP:* {global_sentry.esp32_ip}\n"
                                else:
                                    msg += f"⚙️ *Hardware:* OFFLINE\n"
                                
                                send_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                                requests.post(send_url, json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"})
        except Exception as e:
            pass
        time.sleep(1)


# =========================================================
# AI WORKER PROCESS (Bypasses Python GIL completely)
# =========================================================
def ai_worker_process(crop_queue, result_queue, known_faces_dir):
    """Runs in a completely separate OS process. Never blocks the video stream."""
    import face_recognition
    import numpy as np
    import os
    
    known_face_encodings = []
    known_face_names = []
    
    def load_faces():
        known_face_encodings.clear()
        known_face_names.clear()
        print(f"[AI Process] Loading known faces from '{known_faces_dir}'...")
        if os.path.exists(known_faces_dir):
            for filename in os.listdir(known_faces_dir):
                if filename.lower().endswith(('.jpg', '.jpeg', '.png')):
                    filepath = os.path.join(known_faces_dir, filename)
                    name = os.path.splitext(filename)[0]
                    try:
                        # Load and convert to a clean uint8 RGB array (dlib is strict about this)
                        raw = face_recognition.load_image_file(filepath)
                        img_array = np.array(raw, dtype=np.uint8)
                        # Strip alpha channel if present (RGBA -> RGB)
                        if img_array.ndim == 3 and img_array.shape[2] == 4:
                            img_array = img_array[:, :, :3]
                        # Downscale large images — dlib fails silently on images > ~1200px
                        h, w = img_array.shape[:2]
                        max_dim = 800
                        if max(h, w) > max_dim:
                            scale = max_dim / max(h, w)
                            new_w, new_h = int(w * scale), int(h * scale)
                            import cv2 as _cv2
                            img_array = _cv2.resize(img_array, (new_w, new_h), interpolation=_cv2.INTER_AREA)
                        # Ensure contiguous memory layout
                        img_array = np.ascontiguousarray(img_array)
                        encodings = face_recognition.face_encodings(img_array)
                        if len(encodings) > 0:
                            known_face_encodings.append(encodings[0])
                            known_face_names.append(name)
                            print(f"[AI Process] ✓ Loaded face: {name}")
                        else:
                            print(f"[AI Process] ✗ No face detected in: {filename} (check the photo has a clear face)")
                    except Exception as e:
                        print(f"[AI Process] ✗ Error loading {filename}: {e}")
        print(f"[AI Process] Loaded {len(known_face_names)} faces.")
        
    load_faces()
    
    print("[AI Process] Ready and listening for crops. No GIL contention!")
    
    while True:
        try:
            face_crop = crop_queue.get()
            if face_crop is None:
                break
                
            # Handle IPC Commands
            if isinstance(face_crop, str):
                if face_crop == "RELOAD":
                    load_faces()
                continue
                
            h, w = face_crop.shape[:2]
            rgb_crop = np.ascontiguousarray(face_crop[:, :, ::-1])
            known_loc = [(0, w, h, 0)]
            face_encodings = face_recognition.face_encodings(rgb_crop, known_loc)

            name = "Intruder"
            if len(face_encodings) > 0:
                face_encoding = face_encodings[0]
                if len(known_face_encodings) > 0:
                    matches = face_recognition.compare_faces(known_face_encodings, face_encoding, tolerance=0.61)
                    face_distances = face_recognition.face_distance(known_face_encodings, face_encoding)
                    best_match_index = np.argmin(face_distances)
                    if matches[best_match_index]:
                        name = known_face_names[best_match_index]

            while not result_queue.empty():
                try:
                    result_queue.get_nowait()
                except:
                    pass
            result_queue.put(name)
            
            # [CRITICAL LAG FIX] Prevent dlib from back-to-back CPU starvation of the main thread
            import time
            time.sleep(0.08)
        except Exception:
            pass

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

import re

@app.route('/api/image/<folder>/<filename>')
def serve_image(folder, filename):
    if folder not in ['known_faces', 'intruders']:
        return "Unauthorized", 403
    from flask import send_from_directory
    return send_from_directory(folder, filename)

@app.route('/api/faces', methods=['GET'])
def list_faces():
    faces = []
    if os.path.exists('known_faces'):
        # Sort by latest modified
        files = [f for f in os.listdir('known_faces') if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
        files.sort(key=lambda x: os.path.getmtime(os.path.join('known_faces', x)), reverse=True)
        for filename in files:
            name = os.path.splitext(filename)[0]
            faces.append({"name": name, "filename": filename})
    return jsonify({"status": "success", "faces": faces})

@app.route('/api/faces/<filename>', methods=['DELETE'])
def delete_face(filename):
    if '..' in filename or not filename.lower().endswith(('.jpg', '.jpeg', '.png')):
        return jsonify({"status": "error", "message": "Invalid file"}), 400
    filepath = os.path.join('known_faces', filename)
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
            # Signal the isolated OS process to reload its models instantly
            if getattr(global_sentry, 'crop_queue', None):
                global_sentry.crop_queue.put("RELOAD")
            return jsonify({"status": "success"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "error", "message": "Not found"}), 404

@app.route('/api/intruders', methods=['GET'])
def list_intruders():
    intruders = []
    if os.path.exists('intruders'):
        files = [f for f in os.listdir('intruders') if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
        # Sort descending by modified time
        files.sort(key=lambda x: os.path.getmtime(os.path.join('intruders', x)), reverse=True)
        for filename in files:
            timestamp = os.path.getmtime(os.path.join('intruders', filename))
            time_str = time.strftime('%b %d, %Y - %I:%M %p', time.localtime(timestamp))
            intruders.append({"filename": filename, "time": time_str})
    return jsonify({"status": "success", "intruders": intruders})

@app.route('/api/intruders/<filename>', methods=['DELETE'])
def delete_intruder(filename):
    if '..' in filename or not filename.lower().endswith(('.jpg', '.jpeg', '.png')):
        return jsonify({"status": "error", "message": "Invalid file"}), 400
    filepath = os.path.join('intruders', filename)
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
            return jsonify({"status": "success"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "error", "message": "Not found"}), 404

@app.route('/api/faces', methods=['POST'])
def add_face():
    name = request.form.get('name', '').strip()
    if not name or 'image' not in request.files:
        return jsonify({"status": "error", "message": "Name and image required"}), 400
        
    file = request.files['image']
    if file.filename == '':
        return jsonify({"status": "error", "message": "No selected file"}), 400
        
    if not os.path.exists('known_faces'):
        os.makedirs('known_faces')
        
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ['.jpg', '.jpeg', '.png']:
        ext = '.jpg'
        
    safe_name = re.sub(r'[^a-zA-Z0-9_\- ]', '_', name)
    filepath = os.path.join('known_faces', f"{safe_name}{ext}")
    file.save(filepath)
    
    if getattr(global_sentry, 'crop_queue', None):
        global_sentry.crop_queue.put("RELOAD")
        
    return jsonify({"status": "success", "message": f"Added {name}"})

def gen_frames():
    """Generator function for MJPEG streaming (capped at ~20 FPS)."""
    while True:
        if global_sentry is None or global_sentry.latest_annotated_frame is None:
            time.sleep(0.05)
            continue
            
        with global_sentry.annotated_frame_lock:
            frame_copy = global_sentry.latest_annotated_frame.copy()
            
        # Optimization: Scale for bandwidth efficiency (target width 800px wide-angle)
        # We no longer artificially crop it to a vertical slice here!
        # The frontend CSS toggles will natively handle all necessary layout scaling/cropping.
        h, w = frame_copy.shape[:2]
        new_w = 800
        new_h = int(h * (new_w / w))
        frame_ready = cv2.resize(frame_copy, (new_w, new_h), interpolation=cv2.INTER_AREA)

        # Debug Ticker to prove if the stream is live or browser cached
        import datetime
        cv2.putText(frame_ready, datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3], (15, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

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
    # Retrieve the state of the sentry
    if not global_sentry:
        return jsonify({"status": "error", "message": "System off"})
        
    step = 5  # Degrees to move per manual click
    
    # Update angles manually
    if motor == 'pan':
        if direction == 'left':
            global_sentry.pan_angle += step
        elif direction == 'right':
            global_sentry.pan_angle -= step
        global_sentry.pan_angle = max(0, min(180, global_sentry.pan_angle))
        
    elif motor == 'tilt':
        if direction == 'up':
            global_sentry.tilt_angle += step
        elif direction == 'down':
             global_sentry.tilt_angle -= step
        global_sentry.tilt_angle = max(0, min(180, global_sentry.tilt_angle))
        
    elif motor == 'center' and direction == 'reset':
        global_sentry.pan_angle = 90
        global_sentry.tilt_angle = 90

    # Send the update to the ESP32 via High-Speed UDP Streaming
    try:
        if global_sentry.esp32_ip is not None:
            import socket
            udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            state_str = system_state.get('status', 'IDLE').upper()
            packet = f"P:{global_sentry.pan_angle},T:{global_sentry.tilt_angle},S:{state_str}".encode('utf-8')
            udp_sock.sendto(packet, (global_sentry.esp32_ip, 8888))
            udp_sock.close()
    except Exception as e:
        print(f"[!] UDP Manual control stream error: {e}")
        pass
        
    global_sentry.last_manual_control_time = time.time()
    print(f"[*] MOTOR CONTROL -> {motor.upper()} : {direction.upper()} (Pan: {global_sentry.pan_angle}, Tilt: {global_sentry.tilt_angle})")
    return jsonify({"status": "success", "motor": motor, "direction": direction})

@app.route('/toggle_patrol', methods=['POST'])
def toggle_patrol():
    if not global_sentry:
        return jsonify({"status": "error", "message": "System off"})
    global_sentry.patrol_enabled = not getattr(global_sentry, 'patrol_enabled', True)
    state = "enabled" if global_sentry.patrol_enabled else "disabled"
    if not global_sentry.patrol_enabled:
        # Snap out of patrol mode instantly when toggled off
        import urllib.request
        try:
            if global_sentry.esp32_ip is not None:
                import socket
                udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                packet = f"P:{global_sentry.pan_angle},T:{global_sentry.tilt_angle},S:IDLE".encode('utf-8')
                udp_sock.sendto(packet, (global_sentry.esp32_ip, 8888))
                udp_sock.close()
        except: pass
    return jsonify({"status": "success", "state": state})


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
    
    with state_lock:
        system_state["audio_timestamp"] = int(time.time() * 1000)
    
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

@app.route('/latest_audio')
def latest_audio():
    filepath = os.path.join(os.getcwd(), 'last_command.webm')
    if os.path.exists(filepath):
        from flask import send_file
        return send_file(filepath, mimetype="audio/webm")
    return "Not found", 404

def send_telegram_alert(filepath):
    """Sends a photo to Telegram."""
    print(f"[*] Sending Telegram Alert to {TELEGRAM_CHAT_ID}...")
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        with open(filepath, 'rb') as photo:
            # Add an Inline Keyboard button to open the Remote Dashboard
            # Dynamically get the local IP so the link works across different networks
            local_ip = get_local_ip()
                
            remote_url = f"http://{local_ip}:5000/remote"
            
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
    """Reads discrete frames in a background thread to completely eliminate all stream buffering."""
    def __init__(self, src=0):
        self.src = src
        self.frame = None
        self.status = False
        self.running = True
        
        if not str(self.src).endswith('/video'):
            self.capture = cv2.VideoCapture(src)
            self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.status, self.frame = self.capture.read()
        
        self.thread = threading.Thread(target=self.update, args=())
        self.thread.daemon = True
        self.thread.start()

    def update(self):
        import urllib.request
        import numpy as np
        if str(self.src).endswith('/video'):
            # Convert /video to /shot.jpg for absolute 0ms latency!
            shot_url = self.src.replace('/video', '/shot.jpg')
            while self.running:
                try:
                    req = urllib.request.urlopen(shot_url, timeout=2.0)
                    img_np = np.frombuffer(req.read(), dtype=np.uint8)
                    frame = cv2.imdecode(img_np, cv2.IMREAD_COLOR)
                    if frame is not None:
                        self.frame = frame
                        self.status = True
                except Exception as e:
                    print(f"[!] Camera Polling Error: {e}")
                    time.sleep(0.5)
        else:
            # Continually empty the OpenCV buffer so we always have the freshest frame!
            while self.running:
                if self.capture.isOpened():
                    self.status, self.frame = self.capture.read()
                else:
                    self.running = False

    def read(self):
        return self.status, self.frame

    def release(self):
        self.running = False
        self.thread.join(timeout=2.0)
        if hasattr(self, 'capture'):
            self.capture.release()

# =========================================================
# SENTRY CORE LOGIC
# =========================================================
class SentryCore:
    def __init__(self, stream_url, known_faces_dir='known_faces'):
        global global_sentry
        global_sentry = self
        
        self.stream_url = stream_url
        self.known_faces_dir = os.path.abspath(known_faces_dir)
        
        # Multiprocessing IPC Queues
        self.crop_queue = mp_lib.Queue(maxsize=1)
        self.result_queue = mp_lib.Queue(maxsize=1)
        
        # Start completely separate AI OS Process
        self.ai_process = mp_lib.Process(
            target=ai_worker_process, 
            args=(self.crop_queue, self.result_queue, self.known_faces_dir)
        )
        self.ai_process.daemon = True
        self.ai_process.start()
        
        # Process States
        self.latest_face_name = "Scanning..."
        
        # UI & Streaming States
        self.latest_annotated_frame = None
        self.annotated_frame_lock = threading.Lock()
        
        self.running = True
        
        # Security/Logic Parameters
        self.intruder_grace_frames = 5
        self.consecutive_unknown_count = 0
        self.last_capture_time = 0.0
        self.last_face_time = time.time()
        self.patrol_direction = 1
        self.patrol_enabled = True
        self.last_manual_control_time = 0.0

        # Hardware Tracking Dynamics
        self.esp32_ip = None # Automatically discovered via UDP
        self.esp_session = requests.Session() # Reuse TCP connection to prevent socket exhaustion
        self.pan_angle = 90
        self.tilt_angle = 90
        self.last_motor_update = 0.0

        # Start Zero-Touch Auto-Discovery Thread
        threading.Thread(target=self.auto_discovery_worker, daemon=True).start()

    def auto_discovery_worker(self):
        """Listens for the ESP32 UDP Broadcast to instantly learn its IP address."""
        import socket
        import concurrent.futures
        import requests
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(("", 8888))
        udp.settimeout(4.0) # If we don't hear a shout for 4 seconds, mark offline
        
        while self.running:
            try:
                data, addr = udp.recvfrom(1024)
                if data == b"ESP32_SENTRY":
                    new_ip = addr[0]
                    if self.esp32_ip != new_ip:
                        print(f"\n[+] ZERO-TOUCH DISCOVERY: Sentry hardware found at {new_ip}!")
                        self.esp32_ip = new_ip
                    update_global_state(hardware="online")
            except socket.timeout:
                current_time = time.time()
                
                # If UDP broadcast is dropping but we know the IP, verify via Unicast HTTP ping
                if self.esp32_ip is not None:
                    try:
                        resp = requests.get(f"http://{self.esp32_ip}/ping", timeout=2.0)
                        if resp.status_code == 200 and "sentry_alive" in resp.text:
                            update_global_state(hardware="online")
                            continue # Still alive via Unicast!
                    except:
                        pass
                    # If it failed HTTP verification too, it's truly offline
                    self.esp32_ip = None
                    
                # Fallback: Active Unicast HTTP Sweep (bypasses Mobile Hotspot isolation)
                if self.esp32_ip is None and (current_time - getattr(self, 'last_http_scan', 0) > 15):
                    self.last_http_scan = current_time
                    local_ip = get_local_ip()
                    base_ip = ".".join(local_ip.split('.')[:-1])
                    
                    def check_ip(ip):
                        try:
                            resp = requests.get(f"http://{ip}/ping", timeout=1.0)
                            if resp.status_code == 200 and "sentry_alive" in resp.text:
                                return ip
                        except:
                            return None
                            
                    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
                        ips_to_check = [f"{base_ip}.{i}" for i in range(1, 255)]
                        results = executor.map(check_ip, ips_to_check)
                        for ip in results:
                            if ip:
                                print(f"\n[+] FALLBACK DISCOVERY: Sentry hardware found at {ip} via HTTP Unicast!")
                                self.esp32_ip = ip
                                update_global_state(hardware="online")
                                break
                
                if self.esp32_ip is None:
                    update_global_state(hardware="offline")
            except Exception:
                pass

    def run(self):
        local_ip = get_local_ip()
        print("\n[*] Starting Sentry System...")
        print(f"[*] Camera Stream: {self.stream_url}")
        print(f"[*] Emo Face UI available at: http://{local_ip}:5000/")
        print(f"[*] Remote Dashboard available at: http://{local_ip}:5000/remote")
        print("[*] Press 'q' in the video window to quit.\n")
        
        # 1. Start Flask UI Thread
        flask_thread = threading.Thread(target=run_flask_server)
        flask_thread.daemon = True
        flask_thread.start()

        # 2. Start Telegram Listener Thread
        tele_thread = threading.Thread(target=telegram_polling_thread)
        tele_thread.daemon = True
        tele_thread.start()

        # 3. Start Video Stream Thread
        cap = VideoStreamWidget(self.stream_url)
        time.sleep(1) # Give camera time to warm up

        if not cap.status:
            print(f"[!] Error: Could not open video stream at {self.stream_url}")
            cap.release()
            self.running = False
            return

        # Initialize Google MediaPipe Face Detection
        mp_face_detection = mp.solutions.face_detection
        
        print("[*] Main Video Loop running seamlessly at target FPS using MediaPipe.")
        with mp_face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.40) as face_detection:
            while self.running:
                ret, frame = cap.read()
                if not ret or frame is None:
                    continue
                    
                h, w = frame.shape[:2]
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                
                # Lightning-fast detection (takes ~3ms)
                results = face_detection.process(rgb_frame)
                
                faces_detected = 0

                if results.detections:
                    for detection in results.detections:
                        faces_detected += 1
                        bbox = detection.location_data.relative_bounding_box
                        x, y = int(bbox.xmin * w), int(bbox.ymin * h)
                        bw, bh = int(bbox.width * w), int(bbox.height * h)
                        
                        # Add some padding to capture the whole head
                        pad_x, pad_y = int(bw * 0.1), int(bh * 0.2)
                        x1 = max(0, x - pad_x)
                        y1 = max(0, y - pad_y * 2)
                        x2 = min(w, x + bw + pad_x)
                        y2 = min(h, y + bh + pad_y)
                        
                        # Only process valid boxes
                        if (y2 - y1) > 20 and (x2 - x1) > 20:
                            # --- 1. AUTONOMOUS SERVOS MATH --- 
                            if faces_detected == 1: # Only track the primary face
                                cx = x + bw / 2
                                cy = y + bh / 2
                                deadzone_x = w * 0.12 # Wider deadzone stops micro-oscillation "bouncing" 
                                deadzone_y = h * 0.12
                                pan_changed = tilt_changed = False
                                
                                error_x = cx - (w / 2)
                                error_y = cy - (h / 2)
                                
                                if abs(error_x) > deadzone_x:
                                    # Proportional Speed: move faster (up to 5 deg/tick) if face is far from center!
                                    step_x = max(1, int(abs(error_x) / (w/2) * 5))
                                    if error_x > 0:
                                        self.pan_angle -= step_x
                                    else:
                                        self.pan_angle += step_x
                                    pan_changed = True
                                    
                                if abs(error_y) > deadzone_y:
                                    step_y = max(1, int(abs(error_y) / (h/2) * 5))
                                    if error_y > 0:
                                        self.tilt_angle -= step_y
                                    else:
                                        self.tilt_angle += step_y
                                    tilt_changed = True
                                    
                                self.pan_angle = max(0, min(180, self.pan_angle))
                                self.tilt_angle = max(0, min(180, self.tilt_angle))
                                
                                current_time = time.time()
                                current_state_str = system_state.get('status', 'IDLE').upper()
                                state_changed = getattr(self, 'last_sent_state', '') != current_state_str
                                
                                # [CRITICAL] Update motors much faster (every 80ms instead of 200ms) for smooth tracking
                                if (pan_changed or tilt_changed or state_changed) and (current_time - self.last_motor_update > 0.08):
                                    self.last_motor_update = current_time
                                    self.last_sent_state = current_state_str
                                    def stream_udp_command(pan, tilt, state):
                                        try:
                                            if self.esp32_ip is not None:
                                                import socket
                                                udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                                                packet = f"P:{pan},T:{tilt},S:{state}".encode('utf-8')
                                                udp_sock.sendto(packet, (self.esp32_ip, 8888))
                                                udp_sock.close()
                                        except: pass
                                    # [CRITICAL LAG FIX] Do not block main loop on synchronous UDP send
                                    threading.Thread(target=stream_udp_command, args=(self.pan_angle, self.tilt_angle, current_state_str), daemon=True).start()
                            
                            # --- 2. FACIAL RECOGNITION PIPELINE ---
                            # 1. Safely send cropped face to deep learning PROCESS over IPC if idle
                            if self.crop_queue.empty():
                                face_crop = frame[y1:y2, x1:x2].copy()
                                max_size = 150
                                crop_h, crop_w = face_crop.shape[:2]
                                if crop_w > max_size or crop_h > max_size:
                                    scale = max_size / float(max(crop_w, crop_h))
                                    face_crop = cv2.resize(face_crop, (0, 0), fx=scale, fy=scale)
                                try:
                                    self.crop_queue.put_nowait(face_crop)
                                except:
                                    pass

                            # 2. Read answer from deep learning process if ready
                            try:
                                name_result = self.result_queue.get_nowait()
                                self.latest_face_name = name_result
                                
                                # Intruder logic shifted here to the main loop since it has full frame access
                                if name_result == "Intruder":
                                    self.consecutive_unknown_count += 1
                                    if self.consecutive_unknown_count >= self.intruder_grace_frames:
                                        update_global_state("unknown", "Intruder")
                                        current_time = time.time()
                                        if current_time - getattr(self, 'last_capture_time', 0) > 60:
                                            if not os.path.exists('intruders'):
                                                os.makedirs('intruders')
                                            timestamp = time.strftime("%Y%m%d-%H%M%S")
                                            filepath = f"intruders/intruder_{timestamp}.jpg"
                                            # We can save the full crisp frame as evidence!
                                            cv2.imwrite(filepath, frame)
                                            print(f"[!] INTRUDER CONFIRMED. Captured evidence to: {filepath}")
                                            self.last_capture_time = current_time
                                            threading.Thread(target=send_telegram_alert, args=(filepath,), daemon=True).start()
                                else:
                                    self.consecutive_unknown_count = 0
                                    update_global_state("known", name_result)
                            except:
                                pass # No new result from AI process yet
                            
                            display_name = getattr(self, 'latest_face_name', 'Scanning...')
                            
                            # Draw smooth tracking box immediately
                            color = (0, 0, 255) if display_name == "Intruder" else (0, 255, 0)
                            if display_name == "Scanning...":
                                color = (0, 255, 255) # Yellow while thinking
                                
                            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                            cv2.rectangle(frame, (x1, y2 - 35), (x2, y2), color, cv2.FILLED)
                            cv2.putText(frame, display_name, (x1 + 6, y2 - 6), cv2.FONT_HERSHEY_DUPLEX, 0.8, (255, 255, 255), 1)

                if faces_detected == 0:
                    missing_count = getattr(self, 'faces_missing_count', 0) + 1
                    setattr(self, 'faces_missing_count', missing_count)
                    if missing_count > 3:
                        self.latest_face_name = "Scanning..."
                        self.consecutive_unknown_count = 0
                        
                        # Trigger Sentinel Patrol Mode after 5 seconds of no face AND no manual input in 10 seconds
                        time_since_face = time.time() - getattr(self, 'last_face_time', time.time())
                        time_since_manual = time.time() - getattr(self, 'last_manual_control_time', 0.0)
                        
                        if getattr(self, 'patrol_enabled', True) and time_since_face > 5.0 and time_since_manual > 10.0:
                            update_global_state("patrol", "")
                            
                            current_time = time.time()
                            # Check if it's time to snap to a new waypoint glance
                            if current_time > getattr(self, 'next_patrol_move_time', 0):
                                import random
                                # Generate erratic waypoint for cinematic snap
                                self.pan_angle = random.randint(30, 150)
                                self.tilt_angle = random.randint(75, 105)
                                
                                # Sentry holds this pose and examines area for 1 to 2.5 seconds
                                self.next_patrol_move_time = current_time + random.uniform(1.0, 2.5)
                                self.last_sent_state = "IDLE"
                                
                                def stream_udp_command(pan, tilt):
                                    try:
                                        if self.esp32_ip is not None:
                                            import socket
                                            udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                                            # We send S:IDLE so the LEDs turn off while simply patrolling
                                            packet = f"P:{pan},T:{tilt},S:IDLE".encode('utf-8')
                                            udp_sock.sendto(packet, (self.esp32_ip, 8888))
                                            udp_sock.close()
                                    except: pass
                                # [CRITICAL LAG FIX] Do not block main loop on synchronous UDP send
                                threading.Thread(target=stream_udp_command, args=(self.pan_angle, self.tilt_angle), daemon=True).start()
                        else:
                            update_global_state("idle", "")
                            # Ensure LEDs turn off if we go idle but patrol is disabled or cooling down
                            if getattr(self, 'last_sent_state', '') != "IDLE":
                                self.last_sent_state = "IDLE"
                                def stream_udp_command(pan, tilt):
                                    try:
                                        if self.esp32_ip is not None:
                                            import socket
                                            udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                                            packet = f"P:{pan},T:{tilt},S:IDLE".encode('utf-8')
                                            udp_sock.sendto(packet, (self.esp32_ip, 8888))
                                            udp_sock.close()
                                    except: pass
                                threading.Thread(target=stream_udp_command, args=(self.pan_angle, self.tilt_angle), daemon=True).start()
                else:
                    setattr(self, 'faces_missing_count', 0)
                    self.last_face_time = time.time()

                # Keep a copy of the fully drawn frame for MJPEG Streaming
                with self.annotated_frame_lock:
                    self.latest_annotated_frame = frame.copy()

                # Show video output smoothly (capped to ~25 FPS)
                cv2.imshow('AI Sentry Platform - Phase 4', frame)

                # Cap the main loop to 25 FPS to avoid spinning the CPU at 100%
                if cv2.waitKey(40) & 0xFF == ord('q'):
                    print("\n[*] Shutting down Sentry Core...")
                    self.running = False
                    break

        # Cleanup
        self.running = False
        cap.release()
        cv2.destroyAllWindows()

# =========================================================
# ENTRY POINT
# =========================================================
def find_camera_ip():
    print("\n[*] Scanning local network for Android Camera (Port 8080)...")
    import socket
    import concurrent.futures
    
    # get_local_ip is now globally defined
    local_ip = get_local_ip()
    base_ip = ".".join(local_ip.split('.')[:-1])
    
    def check_port(ip):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect((ip, 8080))
            s.close()
            return ip
        except:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=100) as executor:
        ips_to_check = [f"{base_ip}.{i}" for i in range(1, 255)]
        results = executor.map(check_port, ips_to_check)
        for ip in results:
            if ip:
                return f"http://{ip}:8080/video"
                
    print("[!] Could not auto-discover camera on port 8080.")
    return 'http://192.168.0.101:8080/video' # Fallback

if __name__ == '__main__':
    # VERY IMPORTANT ON WINDOWS: Required to prevent infinite multiprocessing loops
    mp_lib.freeze_support()
    
    # Intelligently discover the Android IP Webcam URL across any network
    camera_stream_url = find_camera_ip()
    print(f"[+] CAMERA DISCOVERY: Linked to video stream at {camera_stream_url}")
    
    # Initialize and run the system
    sentry = SentryCore(stream_url=camera_stream_url)
    sentry.run()
