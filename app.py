import os
import time
import random
import threading
from io import BytesIO
from flask import Flask, render_template, Response, jsonify, request, send_from_directory
import cv2
from fer.fer import FER    # use this import as it matches your environment

app = Flask(__name__, template_folder="templates", static_folder="static")

# --------------------
# Configuration
# --------------------
SONG_DIR = os.path.join(app.static_folder, "songs")

emotion_to_songs = {
    "happy": ["happy1.mp3", "happy2.mp3"],
    "sad": ["sad1.mp3", "sad2.mp3"],
    "angry": ["angry1.mp3", "angry2.mp3"],
    "neutral": ["neutral1.mp3", "neutral2.mp3"]
}

# --------------------
# Detector & Camera
# --------------------
detector = FER(mtcnn=True)

# camera (shared)
camera = None

# --------------------
# Playback & state (server-side)
# --------------------
state_lock = threading.Lock()
played_songs = {emotion: [] for emotion in emotion_to_songs}

# State variables
current_emotion = None        # locked emotion for current assigned song
next_emotion = None           # latest detected emotion
current_confidence = 0.0
current_song = None           # filename (e.g. "happy1.mp3") assigned to client
song_assigned = False         # True when server assigned a song and waiting for client to notify start
song_playing = False          # True when client notified that playback started

# Detection timing
DETECT_INTERVAL = 0.8    # seconds between running FER on frames (tweakable)

# Ensure song directory exists
os.makedirs(SONG_DIR, exist_ok=True)

# --------------------
# Helpers
# --------------------
def get_next_song(emotion):
    """Choose next non-repeating song filename for a given emotion (relative to SONG_DIR)."""
    songs = emotion_to_songs.get(emotion, [])
    if not songs:
        return None
    if len(played_songs[emotion]) == len(songs):
        played_songs[emotion] = []
    remaining = list(set(songs) - set(played_songs[emotion]))
    choice = random.choice(remaining)
    played_songs[emotion].append(choice)
    return choice

def ensure_camera_open():
    global camera
    if camera is None or not camera.isOpened():
        # On Windows, CAP_DSHOW sometimes more stable; on Linux/Mac remove the second arg
        try:
            camera = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        except Exception:
            camera = cv2.VideoCapture(0)
    return camera and camera.isOpened()

# --------------------
# Frame generator (MJPEG)
# --------------------
def mjpeg_generator():
    """Read frames from camera, run detection periodically, overlay status, and stream MJPEG."""
    global next_emotion, current_confidence, current_emotion, current_song, song_assigned

    last_detection = 0.0
    while True:
        if not ensure_camera_open():
            # If can't open, stream a blank image (so page doesn't break) and retry
            blank = 255 * (np.ones((480, 640, 3), dtype="uint8"))
            ret, buf = cv2.imencode(".jpg", blank)
            frame_bytes = buf.tobytes()
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n")
            time.sleep(0.5)
            continue

        ret, frame = camera.read()
        if not ret:
            time.sleep(0.02)
            continue

        # mirror frame for more natural webcam feeling
        frame = cv2.flip(frame, 1)

        now = time.time()
        if now - last_detection >= DETECT_INTERVAL:
            # Run FER on the current frame (it expects BGR frames)
            try:
                results = detector.detect_emotions(frame)
            except Exception:
                results = []

            if results:
                emotions = results[0].get("emotions", {})
                if emotions:
                    dominant = max(emotions, key=emotions.get)
                    confidence = emotions.get(dominant, 0.0)
                else:
                    dominant = "neutral"
                    confidence = 0.0
            else:
                dominant = "neutral"
                confidence = 0.0

            with state_lock:
                next_emotion = dominant
                current_confidence = float(confidence)

                # If no song is assigned/playing -> assign a new song and lock emotion
                global song_playing
                if not song_assigned and not song_playing:
                    # lock the current emotion for this next song
                    current_emotion = next_emotion or "neutral"
                    chosen = get_next_song(current_emotion)
                    if chosen and os.path.exists(os.path.join(SONG_DIR, chosen)):
                        current_song = chosen
                        song_assigned = True
                    else:
                        # if missing file or no song, don't assign
                        current_song = None
                        song_assigned = False
            last_detection = now

        # Overlay text
        display_text = f"Detected: {next_emotion or '—'} ({current_confidence*100:.0f}%)"
        locked_text = f"Locked: {current_emotion or '—'}"
        cv2.putText(frame, display_text, (18, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.putText(frame, locked_text, (18, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 200, 0), 2)

        # encode
        ret2, buf = cv2.imencode(".jpg", frame)
        if not ret2:
            time.sleep(0.01)
            continue
        frame_bytes = buf.tobytes()
        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n")

# --------------------
# Routes
# --------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/video_feed")
def video_feed():
    # stream the MJPEG frames
    return Response(mjpeg_generator(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/get_state")
def get_state():
    """Return JSON with the latest detection and assigned song (if any)."""
    with state_lock:
        resp = {
            "next_emotion": next_emotion,
            "locked_emotion": current_emotion,
            "confidence": current_confidence,
            "song_assigned": bool(song_assigned),
            "song_playing": bool(song_playing),
            "song_filename": current_song,
            "song_url": f"/static/songs/{current_song}" if current_song else None
        }
    return jsonify(resp)

@app.route("/song_started", methods=["POST"])
def song_started():
    """Client tells server the assigned song has started playing."""
    global song_playing
    with state_lock:
        song_playing = True
    return jsonify({"ok": True})

@app.route("/song_ended", methods=["POST"])
def song_ended():
    """Client notifies server that the song finished (or was stopped). Clear assigned state so server can pick next."""
    global song_playing, song_assigned, current_song, current_emotion
    with state_lock:
        song_playing = False
        song_assigned = False
        # Keep current_emotion as last locked value until next assignment
        current_song = None
    return jsonify({"ok": True})

# --------------------
# Cleanup on shutdown
# --------------------
def cleanup():
    global camera
    try:
        if camera is not None and camera.isOpened():
            camera.release()
    except Exception:
        pass

import atexit
atexit.register(cleanup)

# --------------------
# Run app
# --------------------
if __name__ == "__main__":
    # Warning: debug=True may spawn multiple processes/threads (reloader).
    # For consistent camera access run with debug=False in production.
    print("Starting Flask app. Make sure your webcam is free (not used by other apps).")
    app.run(host="0.0.0.0", port=5000, debug=True)
