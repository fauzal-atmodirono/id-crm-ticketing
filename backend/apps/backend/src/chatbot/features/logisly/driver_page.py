"""The mock Logisly driver app page (`GET /logisly/driver`).

A phone-sized page standing in for Logisly's future driver app. This version
receives the agent's call through the Twilio Voice JS SDK (browser softphone,
no phone number needed); chat is added on top later. Holds no secret: the
API key comes from its own `?key=` query string.
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
  :root { --bg:#e9eaee; --phone:#ffffff; --ink:#1f2937; --muted:#6b7280; --accent:#f8485e;
          --ok:#16a34a; --line:#e5e7eb; }
  * { box-sizing:border-box; }
  body { margin:0; min-height:100vh; display:grid; place-items:center; background:var(--bg);
         font-family:Montserrat, system-ui, sans-serif; color:var(--ink); }
  .phone { width:min(380px, 100vw); height:min(760px, 100vh); background:var(--phone);
           border-radius:28px; box-shadow:0 20px 60px rgba(0,0,0,.18); overflow:hidden;
           display:flex; flex-direction:column; }
  @media (max-width:420px) { .phone { border-radius:0; box-shadow:none; } }
  .top { background:var(--accent); color:#fff; padding:18px 20px 14px; }
  .top b { font-size:18px; } .top div { font-size:12px; opacity:.9; margin-top:2px; }
  .body { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center;
          gap:16px; padding:24px; text-align:center; }
  .status { font-size:13px; color:var(--muted); }
  .avatar { width:96px; height:96px; border-radius:50%; background:#fde3e6; display:grid; place-items:center;
            font-size:40px; }
  .ring .avatar { animation:pulse 1s infinite; }
  @keyframes pulse { 0%{box-shadow:0 0 0 0 rgba(248,72,94,.5)} 100%{box-shadow:0 0 0 22px rgba(248,72,94,0)} }
  h2 { margin:0; font-size:20px; } .timer { font-variant-numeric:tabular-nums; color:var(--muted); }
  .btns { display:flex; gap:28px; margin-top:12px; }
  button { font:inherit; font-weight:700; border:0; border-radius:999px; cursor:pointer; color:#fff; }
  .round { width:64px; height:64px; font-size:24px; }
  .answer { background:var(--ok); } .decline, .hang { background:#dc2626; }
  .start { background:var(--accent); padding:14px 22px; font-size:15px; }
  .hidden { display:none !important; }
</style>
</head>
<body>
<div class="phone">
  <div class="top"><b>Logisly Driver</b><div id="who">Pak Budi Santoso · B 9123 KXT</div></div>
  <div class="body" id="screen-start">
    <div class="avatar">🚚</div>
    <p class="status">Aktifkan aplikasi agar bisa menerima panggilan dari Logisly.</p>
    <button class="start" id="btn-start">Mulai</button>
  </div>
  <div class="body hidden" id="screen-idle">
    <div class="avatar">🚚</div>
    <h2>Siap menerima panggilan</h2>
    <p class="status" id="idle-status">Terhubung ke Logisly</p>
  </div>
  <div class="body hidden ring" id="screen-ring">
    <div class="avatar">📞</div>
    <h2>Logisly Operations</h2>
    <p class="status">Panggilan masuk…</p>
    <div class="btns">
      <button class="round decline" id="btn-decline" title="Tolak">✕</button>
      <button class="round answer" id="btn-answer" title="Angkat">✆</button>
    </div>
  </div>
  <div class="body hidden" id="screen-call">
    <div class="avatar">🎧</div>
    <h2>Logisly Operations</h2>
    <p class="timer" id="timer">00:00</p>
    <div class="btns"><button class="round hang" id="btn-hang" title="Tutup">✕</button></div>
  </div>
</div>
<script>
const key = new URLSearchParams(location.search).get("key") || "";
const screens = ["start", "idle", "ring", "call"];
const show = s => screens.forEach(n => document.getElementById("screen-" + n).classList.toggle("hidden", n !== s));
let device = null, current = null, tick = null;

function startTimer() {
  const t0 = Date.now(), el = document.getElementById("timer");
  tick = setInterval(() => { const s = Math.floor((Date.now() - t0) / 1000);
    el.textContent = String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0"); }, 500);
}
function endCall(msg) {
  clearInterval(tick); current = null; show("idle");
  if (msg) document.getElementById("idle-status").textContent = msg;
}

async function token() {
  const r = await fetch("/logisly/driver/token", { headers: { "X-API-Key": key } });
  if (!r.ok) throw new Error(r.status === 401 ? "Buka halaman dengan ?key=<API key>" : "Token gagal (" + r.status + ")");
  return (await r.json()).token;
}

document.getElementById("btn-start").onclick = async () => {
  try {
    await navigator.mediaDevices.getUserMedia({ audio: true });
    device = new Twilio.Device(await token(), { closeProtection: true });
    device.on("tokenWillExpire", async () => device.updateToken(await token()));
    device.on("error", e => { document.getElementById("idle-status").textContent = "Error: " + e.message; });
    device.on("incoming", call => {
      current = call; show("ring");
      call.on("cancel", () => endCall("Panggilan tak terjawab"));
      call.on("disconnect", () => endCall("Panggilan selesai"));
    });
    await device.register();
    show("idle");
  } catch (e) { document.querySelector("#screen-start .status").textContent = e.message || String(e); }
};
document.getElementById("btn-answer").onclick = () => { if (!current) return; current.accept(); show("call"); startTimer(); };
document.getElementById("btn-decline").onclick = () => { if (current) current.reject(); endCall("Panggilan ditolak"); };
document.getElementById("btn-hang").onclick = () => { if (current) current.disconnect(); endCall("Panggilan selesai"); };
</script>
</body>
</html>
"""
