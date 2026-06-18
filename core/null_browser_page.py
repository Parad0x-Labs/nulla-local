"""
core/null_browser_page.py
=========================
Renders the .null browser panel — an HTML UI that lets users dispatch
null:// protocol URIs through the NULLA agent loop and see receipts.

Served at GET /null-browser on the meet server.
The UI talks to the NULLA API at /api/null (port 11435 by default).
"""
from __future__ import annotations

_PAGE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>.null browser · NULLA</title>
<style>
  :root {
    --bg: #0a0a0a; --panel: #111; --border: #222; --accent: #6cf; --green: #4c4;
    --text: #ddd; --muted: #666; --radius: 6px; --font: "Courier New", monospace;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: var(--font);
    font-size: 14px; min-height: 100vh; display: flex; flex-direction: column; }
  header { border-bottom: 1px solid var(--border); padding: 12px 20px;
    display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 15px; color: var(--accent); letter-spacing: 1px; }
  header .sub { color: var(--muted); font-size: 12px; }
  main { flex: 1; display: flex; flex-direction: column; gap: 16px;
    padding: 20px; max-width: 860px; width: 100%; margin: 0 auto; }
  .bar-row { display: flex; gap: 8px; }
  input[type=text], textarea {
    background: var(--panel); border: 1px solid var(--border); color: var(--text);
    font-family: var(--font); font-size: 13px; border-radius: var(--radius);
    padding: 8px 12px; outline: none; transition: border-color .15s;
  }
  input[type=text]:focus, textarea:focus { border-color: var(--accent); }
  #uri-input { flex: 1; }
  button {
    background: var(--accent); color: #000; border: none; border-radius: var(--radius);
    padding: 8px 18px; font-family: var(--font); font-size: 13px; font-weight: bold;
    cursor: pointer; transition: opacity .15s; white-space: nowrap;
  }
  button:disabled { opacity: .4; cursor: default; }
  button:hover:not(:disabled) { opacity: .85; }
  #prompt-area { width: 100%; resize: vertical; min-height: 72px; }
  .section { display: flex; flex-direction: column; gap: 6px; }
  .label { font-size: 11px; color: var(--muted); text-transform: uppercase;
    letter-spacing: .6px; }
  #output {
    background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 14px; min-height: 180px; white-space: pre-wrap; word-break: break-word;
    color: var(--text); font-size: 13px; line-height: 1.6;
  }
  .receipt-row { display: flex; gap: 8px; flex-wrap: wrap; }
  .badge {
    background: #181818; border: 1px solid var(--border); border-radius: 4px;
    padding: 4px 10px; font-size: 11px; color: var(--muted);
  }
  .badge span { color: var(--green); margin-left: 4px; }
  .workers-link { font-size: 11px; color: var(--muted); text-decoration: none; }
  .workers-link:hover { color: var(--accent); }
  footer { border-top: 1px solid var(--border); padding: 10px 20px;
    font-size: 11px; color: var(--muted); display: flex; gap: 16px; }
  .spinner { display: none; width: 14px; height: 14px;
    border: 2px solid var(--border); border-top-color: var(--accent);
    border-radius: 50%; animation: spin .6s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<header>
  <h1>∅ .null browser</h1>
  <span class="sub">null:// protocol · NULLA agent dispatch</span>
</header>

<main>
  <div class="section">
    <div class="label">null:// URI</div>
    <div class="bar-row">
      <input id="uri-input" type="text" placeholder="null://task/code-review?price=0.001"
             value="null://task/hello" autocomplete="off" spellcheck="false"/>
      <div class="spinner" id="spinner"></div>
      <button id="send-btn" onclick="dispatch()">Dispatch ↗</button>
    </div>
  </div>

  <div class="section">
    <div class="label">Prompt (optional)</div>
    <textarea id="prompt-area"
      placeholder="Describe the task in natural language, or leave blank to use the URI path as the prompt."></textarea>
  </div>

  <div class="section">
    <div class="label">Output</div>
    <div id="output">Ready. Enter a null:// URI and press Dispatch.</div>
  </div>

  <div class="section" id="receipt-section" style="display:none">
    <div class="label">Receipt</div>
    <div class="receipt-row" id="receipt-row"></div>
  </div>

  <div class="section" id="quote-section" style="display:none">
    <div class="label">x402 Quote</div>
    <div class="receipt-row" id="quote-row"></div>
  </div>
</main>

<footer>
  <a href="/v1/workers" class="workers-link">⬡ mesh workers</a>
  <a href="/v1/health" class="workers-link">♥ health</a>
  <span>NULLA · null:// protocol · Web0</span>
</footer>

<script>
const NULLA_API = window.__NULLA_API__ || (window.location.port === "11435"
  ? "" : "http://localhost:11435");

async function dispatch() {
  const uri = document.getElementById("uri-input").value.trim();
  const prompt = document.getElementById("prompt-area").value.trim();
  if (!uri) { alert("Enter a null:// URI first."); return; }

  const btn = document.getElementById("send-btn");
  const spinner = document.getElementById("spinner");
  btn.disabled = true;
  spinner.style.display = "inline-block";
  document.getElementById("output").textContent = "Dispatching…";
  document.getElementById("receipt-section").style.display = "none";
  document.getElementById("quote-section").style.display = "none";

  try {
    const body = { uri };
    if (prompt) body.prompt = prompt;
    const resp = await fetch(NULLA_API + "/api/null", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok) {
      document.getElementById("output").textContent = "Error " + resp.status + ":\\n"
        + (data.error || JSON.stringify(data, null, 2));
      return;
    }
    document.getElementById("output").textContent = data.result || "(empty response)";

    if (data.receipt_id || data.session_id) {
      const rr = document.getElementById("receipt-row");
      rr.innerHTML = "";
      if (data.receipt_id) rr.appendChild(badge("receipt_id", data.receipt_id));
      if (data.session_id) rr.appendChild(badge("session_id", data.session_id));
      if (data.service)    rr.appendChild(badge("service", data.service));
      if (data.path)       rr.appendChild(badge("path", data.path));
      document.getElementById("receipt-section").style.display = "";
    }
    if (data.quote && data.quote.amount_usdc) {
      const qr = document.getElementById("quote-row");
      qr.innerHTML = "";
      qr.appendChild(badge("amount_usdc", data.quote.amount_usdc.toFixed(6)));
      if (data.quote.recipient_wallet) {
        const short = data.quote.recipient_wallet.slice(0, 6) + "…" +
                      data.quote.recipient_wallet.slice(-4);
        qr.appendChild(badge("recipient", short));
      }
      document.getElementById("quote-section").style.display = "";
    }
  } catch (e) {
    document.getElementById("output").textContent = "Network error:\\n" + e.message;
  } finally {
    btn.disabled = false;
    spinner.style.display = "none";
  }
}

function badge(key, val) {
  const d = document.createElement("div");
  d.className = "badge";
  d.innerHTML = key + "<span>" + val + "</span>";
  return d;
}

document.getElementById("uri-input").addEventListener("keydown", function(e) {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); dispatch(); }
});
</script>
</body>
</html>
"""


def render_null_browser_html() -> str:
    return _PAGE


__all__ = ["render_null_browser_html"]
