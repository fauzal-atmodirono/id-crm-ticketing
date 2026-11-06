"""The mock Logisly driver app page (`GET /logisly/driver`).

A phone-sized page standing in for Logisly's future driver app: chat with the
driver agent (text, photo or video, voice notes) and incoming calls from it
through the Twilio Voice JS SDK (browser softphone, no phone number needed).
Holds no secret: the API key comes from its own `?key=` query string.

Voice notes are recorded with MediaRecorder (WebM/Opus in Chrome), then decoded
and re-encoded in the browser as 16 kHz mono WAV, the same approach as the
Proton web voice channel: Gemini accepts WAV, not WebM audio.
"""

DRIVER_HTML = """<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Logisly Driver</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/@twilio/voice-sdk@2.18.3/dist/twilio.min.js"></script>
<style>
  :root { --bg:#e9eaee; --phone:#f4f5f7; --ink:#1f2937; --muted:#6b7280; --accent:#f8485e;
          --ok:#16a34a; --mine:#dcf8c6; --theirs:#ffffff; }
  * { box-sizing:border-box; }
  body { margin:0; min-height:100vh; display:grid; place-items:center; background:var(--bg);
         font-family:Montserrat, system-ui, sans-serif; color:var(--ink); }
  .phone { position:relative; width:min(390px, 100vw); height:min(780px, 100vh); background:var(--phone);
           border-radius:28px; box-shadow:0 20px 60px rgba(0,0,0,.18); overflow:hidden;
           display:flex; flex-direction:column; }
  @media (max-width:420px) { .phone { border-radius:0; box-shadow:none; } }
  .top { background:var(--accent); color:#fff; padding:16px 18px 12px; }
  .top b { font-size:17px; } .top div { font-size:12px; opacity:.9; margin-top:2px; }
  #chat { flex:1; overflow-y:auto; padding:14px 12px; display:flex; flex-direction:column; gap:8px; }
  .msg { max-width:80%; padding:8px 10px; border-radius:12px; font-size:14px; line-height:1.35;
         box-shadow:0 1px 1px rgba(0,0,0,.06); white-space:pre-wrap; word-wrap:break-word; }
  .logisly { align-self:flex-start; background:var(--theirs); border-top-left-radius:4px; }
  .driver { align-self:flex-end; background:var(--mine); border-top-right-radius:4px; }
  .msg img, .msg video { max-width:100%; border-radius:8px; display:block; margin-bottom:4px; }
  .msg audio { width:220px; display:block; }
  .time { font-size:10px; color:var(--muted); text-align:right; margin-top:3px; }
  .empty { margin:auto; color:var(--muted); font-size:13px; text-align:center; padding:0 24px; }
  .composer { display:flex; gap:6px; padding:8px; background:#fff; border-top:1px solid #e5e7eb; align-items:center; }
  .composer input[type=text] { flex:1; font:inherit; font-size:14px; border:1px solid #e5e7eb; border-radius:20px; padding:9px 12px; }
  .icon { width:40px; height:40px; border:0; border-radius:50%; background:#f3f4f6; font-size:18px; cursor:pointer; flex:none; }
  .icon.send { background:var(--accent); color:#fff; }
  .icon.rec { background:#dc2626; color:#fff; animation:blink 1s infinite; }
  @keyframes blink { 50% { opacity:.6; } }
  #status { font-size:11px; color:var(--muted); text-align:center; padding:2px 8px 6px; min-height:16px; background:#fff; }
  .overlay { position:absolute; inset:0; background:var(--phone); display:flex; flex-direction:column;
             align-items:center; justify-content:center; gap:16px; padding:24px; text-align:center; z-index:5; }
  .overlay.dark { background:#111827; color:#fff; }
  .overlay p { color:var(--muted); font-size:13px; margin:0; } .overlay.dark p { color:#cbd5e1; }
  .avatar { width:96px; height:96px; border-radius:50%; background:#fde3e6; display:grid; place-items:center; font-size:40px; }
  .ring .avatar { animation:pulse 1s infinite; }
  @keyframes pulse { 0%{box-shadow:0 0 0 0 rgba(248,72,94,.6)} 100%{box-shadow:0 0 0 24px rgba(248,72,94,0)} }
  h2 { margin:0; font-size:20px; } .timer { font-variant-numeric:tabular-nums; }
  .btns { display:flex; gap:32px; margin-top:12px; }
  button.round { width:64px; height:64px; font-size:24px; border:0; border-radius:50%; cursor:pointer; color:#fff; }
  .answer { background:var(--ok); } .decline, .hang { background:#dc2626; }
  .start { font:inherit; font-weight:700; background:var(--accent); color:#fff; border:0; border-radius:999px; padding:14px 22px; font-size:15px; cursor:pointer; }
  .hidden { display:none !important; }
</style>
</head>
<body>
<div class="phone">
  <div class="top"><b>Logisly Driver</b><div>Pak Budi Santoso · B 9123 KXT</div></div>
  <div id="chat"><div class="empty">Belum ada pesan.</div></div>
  <div id="status"></div>
  <div class="composer">
    <input type="file" id="file" accept="image/*,video/*" class="hidden">
    <button class="icon" id="btn-photo" title="Foto / video">📷</button>
    <input type="text" id="text" placeholder="Tulis pesan…" autocomplete="off">
    <button class="icon" id="btn-mic" title="Pesan suara">🎤</button>
    <button class="icon send" id="btn-send" title="Kirim">➤</button>
  </div>

  <div class="overlay" id="screen-start">
    <div class="avatar">🚚</div>
    <h2>Logisly Driver</h2>
    <p>Aktifkan aplikasi untuk menerima pesan dan panggilan dari Logisly.</p>
    <button class="start" id="btn-start">Mulai</button>
  </div>
  <div class="overlay dark ring hidden" id="screen-ring">
    <div class="avatar">📞</div>
    <h2>Logisly Operations</h2>
    <p>Panggilan masuk…</p>
    <div class="btns">
      <button class="round decline" id="btn-decline" title="Tolak">✕</button>
      <button class="round answer" id="btn-answer" title="Angkat">✆</button>
    </div>
  </div>
  <div class="overlay dark hidden" id="screen-call">
    <div class="avatar">🎧</div>
    <h2>Logisly Operations</h2>
    <p class="timer" id="timer">00:00</p>
    <div class="btns"><button class="round hang" id="btn-hang" title="Tutup">✕</button></div>
  </div>
</div>
<script>
const key = new URLSearchParams(location.search).get("key") || "";
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const status = t => { $("status").textContent = t || ""; };
let device = null, current = null, tick = null, lastSig = "";

// ---- chat -------------------------------------------------------------
function bubble(m) {
  const media = m.attachments.map(a =>
    a.type === "image" ? `<a href="${esc(a.url)}" target="_blank"><img src="${esc(a.thumb || a.url)}"></a>`
    : a.type === "video" ? `<video src="${esc(a.url)}" controls></video>`
    : a.type === "audio" ? `<audio src="${esc(a.url)}" controls></audio>`
    : `<a href="${esc(a.url)}" target="_blank">📎 lampiran</a>`).join("");
  const time = m.at ? new Date(m.at * 1000).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" }) : "";
  return `<div class="msg ${m.from}">${media}${esc(m.text)}<div class="time">${time}</div></div>`;
}
async function loadMessages() {
  try {
    const r = await fetch("/logisly/driver/messages", { headers: { "X-API-Key": key } });
    if (!r.ok) { status(r.status === 401 ? "Buka halaman dengan ?key=<API key>" : "Gagal memuat pesan"); return; }
    const d = await r.json();
    const sig = d.conversation_id + ":" + d.messages.map(m => m.id).join(",");
    if (sig === lastSig) return;
    lastSig = sig;
    const box = $("chat");
    box.innerHTML = d.messages.length ? d.messages.map(bubble).join("") : '<div class="empty">Belum ada pesan.</div>';
    box.scrollTop = box.scrollHeight;
  } catch (e) { /* keep polling */ }
}
async function send(text, file) {
  const fd = new FormData();
  fd.append("text", text || "");
  if (file) fd.append("file", file, file.name);
  status("Mengirim…");
  const r = await fetch("/logisly/driver/messages", { method: "POST", headers: { "X-API-Key": key }, body: fd });
  status(r.ok ? "" : "Gagal mengirim (" + r.status + ")");
  loadMessages();
}
$("btn-send").onclick = () => { const t = $("text").value.trim(); if (!t) return; $("text").value = ""; send(t); };
$("text").addEventListener("keydown", e => { if (e.key === "Enter") $("btn-send").click(); });
$("btn-photo").onclick = () => $("file").click();
$("file").onchange = () => { const f = $("file").files[0]; if (f) send("", f); $("file").value = ""; };

// ---- voice notes: record, then re-encode to 16 kHz mono WAV -----------
let rec = null, chunks = [], recStart = 0, recTick = null;
function wav16k(buffer) {
  const n = buffer.length, out = new ArrayBuffer(44 + n * 2), v = new DataView(out);
  const w = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  w(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); w(8, "WAVE"); w(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, 16000, true); v.setUint32(28, 32000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  w(36, "data"); v.setUint32(40, n * 2, true);
  const d = buffer.getChannelData(0);
  for (let i = 0; i < n; i++) { const s = Math.max(-1, Math.min(1, d[i])); v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true); }
  return new Blob([out], { type: "audio/wav" });
}
async function toWav(blob) {
  const ctx = new AudioContext();
  const decoded = await ctx.decodeAudioData(await blob.arrayBuffer());
  ctx.close();
  const off = new OfflineAudioContext(1, Math.ceil(decoded.duration * 16000), 16000);
  const src = off.createBufferSource(); src.buffer = decoded; src.connect(off.destination); src.start();
  return wav16k(await off.startRendering());
}
$("btn-mic").onclick = async () => {
  if (rec && rec.state === "recording") { rec.stop(); return; }
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  chunks = []; rec = new MediaRecorder(stream);
  rec.ondataavailable = e => chunks.push(e.data);
  rec.onstop = async () => {
    stream.getTracks().forEach(t => t.stop()); clearInterval(recTick);
    $("btn-mic").classList.remove("rec"); $("btn-mic").textContent = "🎤";
    try {
      const wav = await toWav(new Blob(chunks, { type: rec.mimeType }));
      send("", new File([wav], "pesan-suara.wav", { type: "audio/wav" }));
    } catch (e) { status("Rekaman gagal: " + e.message); }
  };
  rec.start(); recStart = Date.now();
  $("btn-mic").classList.add("rec"); $("btn-mic").textContent = "■";
  recTick = setInterval(() => status("Merekam… " + Math.floor((Date.now() - recStart) / 1000) + " dtk, tekan ■ untuk kirim"), 300);
};

// ---- calls (Twilio Voice SDK) ------------------------------------------
const overlay = id => ["screen-start", "screen-ring", "screen-call"].forEach(s => $(s).classList.toggle("hidden", s !== id));
function startTimer() {
  const t0 = Date.now();
  tick = setInterval(() => { const s = Math.floor((Date.now() - t0) / 1000);
    $("timer").textContent = String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0"); }, 500);
}
function endCall(msg) { clearInterval(tick); current = null; overlay(null); status(msg); setTimeout(loadMessages, 3000); }
async function token() {
  const r = await fetch("/logisly/driver/token", { headers: { "X-API-Key": key } });
  if (!r.ok) throw new Error(r.status === 401 ? "Buka halaman dengan ?key=<API key>" : "Token gagal (" + r.status + ")");
  return (await r.json()).token;
}
$("btn-start").onclick = async () => {
  try {
    await navigator.mediaDevices.getUserMedia({ audio: true });
    device = new Twilio.Device(await token(), { closeProtection: true });
    device.on("tokenWillExpire", async () => device.updateToken(await token()));
    device.on("error", e => status("Error: " + e.message));
    device.on("incoming", call => {
      current = call; overlay("screen-ring");
      call.on("cancel", () => endCall("Panggilan tak terjawab"));
      call.on("disconnect", () => endCall("Panggilan selesai"));
    });
    await device.register();
    overlay(null); status("Terhubung ke Logisly");
    loadMessages(); setInterval(loadMessages, 2000);
  } catch (e) { document.querySelector("#screen-start p").textContent = e.message || String(e); }
};
$("btn-answer").onclick = () => { if (!current) return; current.accept(); overlay("screen-call"); startTimer(); };
$("btn-decline").onclick = () => { if (current) current.reject(); endCall("Panggilan ditolak"); };
$("btn-hang").onclick = () => { if (current) current.disconnect(); endCall("Panggilan selesai"); };
</script>
</body>
</html>
"""
