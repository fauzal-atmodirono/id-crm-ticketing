"""The one-page operator console for the Logisly tracking simulator.

Served by `GET /logisly/console`. Holds no secret: it reads the API key from
its own `?key=` query string and sends it as `X-API-Key`.
"""

CONSOLE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tracking Simulator</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  :root { --bg:#f7f7f7; --card:#fff; --ink:#3c3c3a; --muted:#64748b; --line:#e2e8f0;
          --accent:#f8485e; --ok:#047857; --okbg:#dcfce7; --warn:#b45309; --warnbg:#fef3c7;
          --bad:#b91c1c; --badbg:#fee2e2; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#18181b; --card:#232326; --ink:#f4f4f5; --muted:#a1a1aa; --line:#3f3f46;
            --okbg:#064e3b; --ok:#a7f3d0; --warnbg:#78350f; --warn:#fde68a; --badbg:#7f1d1d; --bad:#fecaca; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font-family:Montserrat, system-ui, sans-serif; }
  header { padding:20px 24px 8px; }
  h1 { margin:0; font-size:20px; }
  header p { margin:4px 0 0; color:var(--muted); font-size:13px; }
  main { padding:12px 24px 32px; display:grid; gap:14px; max-width:1100px; }
  .trip { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px;
          display:grid; grid-template-columns: 1fr auto; gap:10px; }
  .id { font-weight:700; } .meta { color:var(--muted); font-size:13px; margin-top:2px; }
  .chip { display:inline-block; font-size:11px; font-weight:700; padding:2px 8px; border-radius:999px;
          margin-left:8px; text-transform:uppercase; letter-spacing:.03em; }
  .s-on_trip,.s-to_pickup,.s-delivered,.s-arrived { background:var(--okbg); color:var(--ok); }
  .s-delayed { background:var(--warnbg); color:var(--warn); }
  .s-issue { background:var(--badbg); color:var(--bad); }
  .actions { display:flex; flex-direction:column; gap:6px; }
  button { font:inherit; font-size:12px; font-weight:600; border:1px solid var(--accent); color:var(--accent);
           background:transparent; border-radius:6px; padding:6px 10px; cursor:pointer; }
  button:hover { background:var(--accent); color:#fff; }
  button:disabled { opacity:.5; cursor:wait; }
  button.call { border-color:var(--ok); color:var(--ok); }
  button.call:hover { background:var(--ok); color:#fff; }
  ol { margin:8px 0 0; padding-left:18px; font-size:12px; color:var(--muted); }
  ol li { margin:2px 0; }
  #msg { font-size:13px; min-height:18px; color:var(--muted); }
  @media (max-width:640px) { .trip { grid-template-columns:1fr; } .actions { flex-direction:row; flex-wrap:wrap; } }
</style>
</head>
<body>
<header>
  <h1>Tracking Simulator</h1>
  <p>Raise an alert on a trip: the driver assistant contacts the driver on WhatsApp.</p>
</header>
<main>
  <div id="msg"></div>
  <div id="trips"></div>
</main>
<script>
const key = new URLSearchParams(location.search).get("key") || "";
const msg = document.getElementById("msg");
const box = document.getElementById("trips");
let anomalies = {};
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

async function load() {
  const r = await fetch("/logisly/trips", { headers: { "X-API-Key": key } });
  if (!r.ok) { msg.textContent = r.status === 401 ? "Open this page with ?key=<backend API key>." : "Could not load trips."; return; }
  const data = await r.json();
  anomalies = data.anomalies;
  box.innerHTML = data.trips.map(t => `
    <div class="trip">
      <div>
        <div class="id">${esc(t.order_id)}<span class="chip s-${esc(t.status)}">${esc(t.status.replace("_"," "))}</span></div>
        <div class="meta">${esc(t.driver_name)} · ${esc(t.plate)} · ${esc(t.origin)} → ${esc(t.destination)}</div>
        <div class="meta">Last position: ${esc(t.location)} (${esc(t.milestone)}) · ETA ${esc(t.eta)}</div>
        ${t.events.length ? `<ol>${t.events.slice(-4).map(e => `<li>${esc(e.at.slice(11,16))} UTC · ${esc(e.note)}</li>`).join("")}</ol>` : ""}
      </div>
      <div class="actions">
        ${Object.entries(anomalies).map(([k, label]) => `<button data-order="${esc(t.order_id)}" data-kind="${esc(k)}">${esc(label)}</button>`).join("")}
        <button class="call" data-order="${esc(t.order_id)}" data-call="1">📞 Telepon driver</button>
      </div>
    </div>`).join("");
}

box.addEventListener("click", async ev => {
  const b = ev.target.closest("button"); if (!b) return;
  b.disabled = true; msg.textContent = b.dataset.call ? "Calling driver…" : "Sending to driver…";
  try {
    const path = b.dataset.call ? "/logisly/call" : "/logisly/anomaly";
    const payload = b.dataset.call ? { order_id: b.dataset.order } : { order_id: b.dataset.order, kind: b.dataset.kind };
    const r = await fetch(path, { method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": key },
      body: JSON.stringify(payload) });
    const d = await r.json();
    msg.textContent = !r.ok ? `Failed: ${d.detail || r.status}`
      : b.dataset.call ? "Ringing the driver app…"
      : `Sent to driver (conversation #${d.conversation_id}): "${d.message}"`;
  } catch (e) { msg.textContent = "Failed: " + e; }
  b.disabled = false; load();
});

load(); setInterval(load, 4000);
</script>
</body>
</html>
"""
