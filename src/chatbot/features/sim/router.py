from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

if TYPE_CHECKING:
    from chatbot.features.chat.service import OrchestratorService


class SimChatRequest(BaseModel):
    session_id: str
    text: str


class SimChatResponse(BaseModel):
    reply: str | None
    language: str | None = None
    sentiment: str | None = None
    handoff: dict[str, Any] | None = None


def build_sim_router(orchestrator: OrchestratorService) -> APIRouter:
    """Builds the developer simulator router. Mount under `DEBUG=true` only."""
    router = APIRouter()

    @router.get("/sim", response_class=HTMLResponse)
    async def sim_index() -> HTMLResponse:
        return HTMLResponse(_INDEX_HTML)

    @router.post("/sim/chat", response_model=SimChatResponse)
    async def sim_chat(req: SimChatRequest) -> SimChatResponse:
        result = await orchestrator.handle_turn(session_id=req.session_id, text=req.text)
        handoff_dict: dict[str, Any] | None = None
        if result.handoff is not None:
            handoff_dict = {
                "reason": result.handoff.reason,
                "language": result.handoff.language,
                "summary": result.handoff.summary,
                "urgency": result.handoff.urgency,
            }
        return SimChatResponse(
            reply=result.reply,
            language=result.language,
            sentiment=result.sentiment,
            handoff=handoff_dict,
        )

    return router


_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Proton Conversational AI &mdash; Simulator</title>
  <style>
    :root {
      --bg: #0f172a;
      --surface: #1e293b;
      --border: #334155;
      --text: #e2e8f0;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --user: #38bdf8;
      --assistant: #a78bfa;
      --error: #f87171;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    header {
      padding: 1rem 1.5rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 1rem;
    }
    h1 { margin: 0; font-size: 1.05rem; font-weight: 600; }
    .badges { display: flex; gap: 0.5rem; flex-wrap: wrap; }
    .badge {
      background: var(--surface);
      border: 1px solid var(--border);
      padding: 0.25rem 0.75rem;
      border-radius: 999px;
      font-size: 0.8rem;
      color: var(--muted);
    }
    .badge strong { color: var(--text); }
    main {
      flex: 1;
      max-width: 800px;
      margin: 0 auto;
      width: 100%;
      padding: 1.25rem;
      display: flex;
      flex-direction: column;
      gap: 1rem;
    }
    #log {
      flex: 1;
      min-height: 320px;
      max-height: 60vh;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 0.5rem;
      padding: 1rem;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }
    .msg { display: flex; flex-direction: column; gap: 0.25rem; }
    .msg .who {
      font-size: 0.7rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      font-weight: 600;
    }
    .msg .text {
      background: var(--bg);
      padding: 0.7rem 0.9rem;
      border-radius: 0.5rem;
      white-space: pre-wrap;
      border: 1px solid var(--border);
      line-height: 1.45;
    }
    .msg .meta { font-size: 0.7rem; color: var(--muted); font-style: italic; }
    .msg.user .who { color: var(--user); }
    .msg.assistant .who { color: var(--assistant); }
    .msg.system .who { color: var(--muted); }
    .msg.system .text {
      background: transparent;
      border-style: dashed;
      color: var(--muted);
      font-size: 0.85rem;
    }
    .msg.error .who { color: var(--error); }
    .msg.error .text { border-color: var(--error); color: var(--error); }
    .controls {
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }
    .channel-tabs { display: flex; gap: 0.5rem; }
    .channel-tabs button {
      background: var(--surface);
      border: 1px solid var(--border);
      color: var(--muted);
      padding: 0.4rem 0.9rem;
      border-radius: 999px;
      cursor: pointer;
      font-size: 0.85rem;
      font-family: inherit;
    }
    .channel-tabs button.active {
      background: var(--accent);
      border-color: var(--accent);
      color: var(--bg);
      font-weight: 600;
    }
    .input-row { display: flex; gap: 0.5rem; }
    textarea {
      flex: 1;
      background: var(--surface);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 0.5rem;
      padding: 0.7rem;
      font-family: inherit;
      font-size: 0.95rem;
      resize: vertical;
      min-height: 56px;
    }
    button.send {
      background: var(--accent);
      color: var(--bg);
      border: none;
      border-radius: 0.5rem;
      padding: 0 1.4rem;
      cursor: pointer;
      font-weight: 600;
      font-size: 0.9rem;
      font-family: inherit;
    }
    button:disabled { opacity: 0.5; cursor: not-allowed; }
    .session-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 0.85rem;
      color: var(--muted);
      gap: 1rem;
      flex-wrap: wrap;
    }
    .session-row input {
      background: var(--surface);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 0.25rem;
      padding: 0.25rem 0.5rem;
      font-family: inherit;
      width: 11ch;
    }
    .session-row button {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--muted);
      padding: 0.25rem 0.75rem;
      border-radius: 0.25rem;
      cursor: pointer;
      font-size: 0.8rem;
      font-family: inherit;
    }
    a { color: var(--accent); }
  </style>
</head>
<body>
  <header>
    <h1>Proton Conversational AI &mdash; Simulator</h1>
    <div class="badges" id="status">
      <span class="badge">CRM: <strong id="crm">&hellip;</strong></span>
      <span class="badge">Voice: <strong id="voice">&hellip;</strong></span>
      <span class="badge">Model: <strong id="model">&hellip;</strong></span>
    </div>
  </header>
  <main>
    <div id="log">
      <div class="msg system">
        <span class="who">System</span>
        <span class="text">Send a message below to talk to the agent. Switch to the Voice tab to simulate a Twilio call &mdash; your text becomes the speech transcript and the response shows what the bot would say.</span>
      </div>
    </div>
    <div class="controls">
      <div class="channel-tabs">
        <button data-channel="chat" class="active" type="button">Chat</button>
        <button data-channel="voice" type="button">Voice</button>
      </div>
      <div class="input-row">
        <textarea id="input" placeholder="Type a message..." rows="2"></textarea>
        <button class="send" id="send" type="button">Send</button>
      </div>
      <div class="session-row">
        <span>Session: <input id="session" value="sim-1" /></span>
        <button id="reset" type="button">New session</button>
      </div>
    </div>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    const log = $("log");
    const input = $("input");
    const send = $("send");
    let channel = "chat";

    document.querySelectorAll(".channel-tabs button").forEach((b) => {
      b.addEventListener("click", () => {
        document.querySelectorAll(".channel-tabs button").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        channel = b.dataset.channel;
        input.placeholder = channel === "chat"
          ? "Type a message..."
          : "Type what the customer said over the phone...";
      });
    });

    $("reset").addEventListener("click", () => {
      $("session").value = "sim-" + Math.floor(Math.random() * 9999);
      log.innerHTML = "";
      addMsg("system", "New session: " + $("session").value);
    });

    function addMsg(role, text, metaText) {
      const el = document.createElement("div");
      el.className = "msg " + role;
      const who = document.createElement("span");
      who.className = "who";
      who.textContent = role;
      const body = document.createElement("span");
      body.className = "text";
      body.textContent = text;
      el.append(who, body);
      if (metaText) {
        const meta = document.createElement("span");
        meta.className = "meta";
        meta.textContent = metaText;
        el.appendChild(meta);
      }
      log.appendChild(el);
      log.scrollTop = log.scrollHeight;
    }

    async function sendChat(sessionId, text) {
      const res = await fetch("/sim/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, text: text }),
      });
      if (!res.ok) {
        throw new Error("Chat error " + res.status + ": " + (await res.text()));
      }
      return await res.json();
    }

    async function sendVoice(sessionId, text) {
      const body = new URLSearchParams();
      body.set("CallSid", sessionId);
      body.set("SpeechResult", text);
      const res = await fetch("/webhooks/voice/twilio/process", {
        method: "POST",
        body,
      });
      if (!res.ok) {
        throw new Error("Voice error " + res.status);
      }
      const xml = await res.text();
      const m = xml.match(/<Say[^>]*>([\\s\\S]*?)<\\/Say>/);
      return m ? m[1].trim() : "(no spoken reply)";
    }

    async function submitMessage() {
      const text = input.value.trim();
      if (!text) return;
      const sessionId = $("session").value || "sim-1";
      addMsg("user", text);
      input.value = "";
      send.disabled = true;
      try {
        if (channel === "chat") {
          const result = await sendChat(sessionId, text);
          if (result.handoff) {
            const h = result.handoff;
            const summary = h.summary || h.reason || "escalated";
            addMsg("system", "Escalated to human agent: " + summary, "urgency=" + h.urgency + " lang=" + h.language);
          } else if (result.reply) {
            const metaBits = [];
            if (result.language && result.language !== "unknown") metaBits.push("lang=" + result.language);
            if (result.sentiment) metaBits.push("sentiment=" + result.sentiment);
            addMsg("assistant", result.reply, metaBits.join(" · ") || null);
          } else {
            addMsg("system", "(no reply &mdash; AI may be paused or response was empty)");
          }
        } else {
          const reply = await sendVoice(sessionId, text);
          addMsg("assistant", reply, "spoken via TwiML <Say>");
        }
      } catch (e) {
        addMsg("error", e.message);
      } finally {
        send.disabled = false;
        input.focus();
      }
    }

    send.addEventListener("click", submitMessage);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        submitMessage();
      }
    });

    fetch("/").then((r) => r.json()).then((d) => {
      $("crm").textContent = d.crm_provider;
      $("voice").textContent = d.voice_provider;
      $("model").textContent = d.model;
    }).catch(() => {});
  </script>
</body>
</html>
"""
