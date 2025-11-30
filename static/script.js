// Client-side webcam capture and polling to /detect
let video = document.getElementById("video");
let canvas = document.getElementById("canvas");
let startBtn = document.getElementById("startBtn");
let stopBtn = document.getElementById("stopBtn");
let emotionP = document.getElementById("emotion");
let confidenceP = document.getElementById("confidence");
let songP = document.getElementById("song");
let statusP = document.getElementById("status");

let stream = null;
let running = false;
let pollInterval = 800; // ms between sends
let pollTimer = null;
let audio = null;

async function startCamera(){
  try{
    stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    video.srcObject = stream;
    await video.play();
  } catch(err){
    statusP.textContent = "Status: Cannot access camera — " + err.message;
    throw err;
  }
}

function stopCamera(){
  if(stream){
    stream.getTracks().forEach(t=>t.stop());
    stream = null;
  }
  video.pause();
  video.srcObject = null;
}

function captureFrameDataURL(scale=0.6, mimeType="image/jpeg", quality=0.6){
  const w = video.videoWidth;
  const h = video.videoHeight;
  if(!w || !h) return null;
  canvas.width = Math.max(160, Math.round(w * scale));
  canvas.height = Math.round(h * scale);
  const ctx = canvas.getContext("2d");
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL(mimeType, quality);
}

async function sendFrameAndDetect(){
  if(!running) return;
  const dataURL = captureFrameDataURL(0.5, "image/jpeg", 0.6);
  if(!dataURL) return;

  try{
    const res = await fetch("/detect", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({image: dataURL})
    });
    if(!res.ok){
      const txt = await res.text();
      console.error("Detect error:", txt);
      statusP.textContent = "Status: server error";
      return;
    }
    const json = await res.json();
    const emotion = json.emotion || "—";
    const conf = (json.confidence || 0) * 100;
    emotionP.textContent = `Detected: ${emotion}`;
    confidenceP.textContent = `Confidence: ${conf.toFixed(1)}%`;

    if(json.song_url){
      // play song if not playing or different
      if(!audio || audio.src !== json.song_url){
        if(audio){
          audio.pause();
          audio = null;
        }
        audio = new Audio(json.song_url);
        audio.loop = false; // you may change to loop true if you like
        audio.play().catch(e => console.warn("Audio play failed:", e));
        songP.textContent = `🎵 Playing: ${json.song}`;
        statusP.textContent = `Status: Locked Emotion: ${emotion}`;
      } else {
        // already playing same file
        songP.textContent = `🎵 Playing: ${json.song}`;
      }
    } else {
      songP.textContent = `⚠️ No song found for ${emotion}`;
    }

  } catch(err){
    console.error("Error sending frame:", err);
    statusP.textContent = "Status: network error";
  }
}

async function startDetection(){
  startBtn.disabled = true;
  stopBtn.disabled = false;
  statusP.textContent = "Status: Starting...";
  try{
    await startCamera();
  } catch(e){
    startBtn.disabled = false;
    stopBtn.disabled = true;
    return;
  }
  running = true;
  // send first immediately then schedule interval
  await sendFrameAndDetect();
  pollTimer = setInterval(sendFrameAndDetect, pollInterval);
  statusP.textContent = "Status: Detecting emotions...";
}

function stopDetection(){
  startBtn.disabled = false;
  stopBtn.disabled = true;
  running = false;
  if(pollTimer){
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if(audio){
    audio.pause();
    audio = null;
  }
  stopCamera();
  statusP.textContent = "Status: Detection stopped";
  emotionP.textContent = "Detected: —";
  confidenceP.textContent = "Confidence: —";
  songP.textContent = "🎵 No song playing";
}

startBtn.addEventListener("click", () => {
  startDetection();
});

stopBtn.addEventListener("click", () => {
  stopDetection();
});

// Clean up when user closes tab
window.addEventListener("beforeunload", () => {
  stopDetection();
});
