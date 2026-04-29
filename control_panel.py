import re
import subprocess
import threading
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Lé Sang Pipeline Control Panel")

LAST_LOG = ""
PROGRESS = 0
STATUS = "idle"
CURRENT_STEP = "Esperando acción"
IS_RUNNING = False


def set_progress(value: int, step: str | None = None):
    global PROGRESS, CURRENT_STEP
    PROGRESS = max(0, min(100, value))
    if step:
        CURRENT_STEP = step


def parse_progress_from_line(line: str, script_name: str):
    match = re.search(r"PROGRESS:\s*(\d+)\s*/\s*(\d+)", line)
    if match:
        current = int(match.group(1))
        total = int(match.group(2))
        if total > 0:
            set_progress(int((current / total) * 100))
        return

    if script_name == "push_to_shopify.py":
        total_match = re.search(r"Se encontraron\s+(\d+)\s+items", line)
        if total_match:
            parse_progress_from_line.total_items = int(total_match.group(1))
            parse_progress_from_line.current_item = 0
            set_progress(5, "Productos encontrados")
            return

        if "Procesando:" in line:
            total = getattr(parse_progress_from_line, "total_items", 0)
            current = getattr(parse_progress_from_line, "current_item", 0) + 1
            parse_progress_from_line.current_item = current

            if total > 0:
                percent = int((current / total) * 100)
                set_progress(percent, line.strip())
            return


def run_script_thread(script_name: str):
    global LAST_LOG, STATUS, IS_RUNNING

    script_path = BASE_DIR / script_name

    if not script_path.exists():
        LAST_LOG = f"ERROR: No existe {script_name}"
        STATUS = "error"
        IS_RUNNING = False
        return

    LAST_LOG = ""
    STATUS = "running"
    IS_RUNNING = True
    set_progress(0, f"Ejecutando {script_name}")

    process = subprocess.Popen(
        ["python", "-u", str(script_path)],
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    output_lines = []

    for line in process.stdout:
        output_lines.append(line)
        LAST_LOG = "".join(output_lines)
        parse_progress_from_line(line, script_name)

    process.wait()

    LAST_LOG = "".join(output_lines)

    if process.returncode == 0:
        STATUS = "done"
        set_progress(100, "Completado")
    else:
        STATUS = "error"

    IS_RUNNING = False


def start_script(script_name: str):
    global IS_RUNNING

    if IS_RUNNING:
        return False, "Ya hay un proceso corriendo."

    thread = threading.Thread(target=run_script_thread, args=(script_name,))
    thread.start()

    return True, f"Iniciado: {script_name}"


HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lé Sang — Panel</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --black: #0a0a0a;
    --white: #f5f4f0;
    --accent: #e05a00;
    --gray: #888;
    --border: #d0cfc9;
  }

  html, body {
    height: 100%;
    background: var(--white);
    color: var(--black);
    font-family: 'Space Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.08em;
    overflow: hidden;
  }

  header {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    align-items: end;
    padding: 28px 40px 18px;
    border-bottom: 1px solid var(--black);
  }

  .logo {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 26px;
    letter-spacing: 0.25em;
    line-height: 1;
  }

  .header-center {
    text-align: center;
    font-size: 9px;
    letter-spacing: 0.3em;
    text-transform: uppercase;
    color: var(--gray);
  }

  .header-right {
    text-align: right;
    font-size: 9px;
    letter-spacing: 0.2em;
    color: var(--gray);
    font-family: 'Space Mono', monospace;
  }

  .layout {
    display: grid;
    grid-template-columns: 1fr 1fr;
    height: calc(100vh - 109px);
  }

  .panel-left {
    border-right: 1px solid var(--black);
    display: flex;
    flex-direction: column;
  }

  .panel-title {
    font-size: 9px;
    letter-spacing: 0.35em;
    text-transform: uppercase;
    padding: 16px 40px 14px;
    border-bottom: 1px solid var(--border);
    color: var(--gray);
  }

  .actions {
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: stretch;
  }

  .action-btn {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 40px;
    border: none;
    border-bottom: 1px solid var(--border);
    background: transparent;
    cursor: pointer;
    text-align: left;
    transition: background 0.15s;
    font-family: inherit;
    flex: 1;
  }

  .action-btn:last-child { border-bottom: none; }
  .action-btn:hover:not(:disabled) { background: var(--black); }
  .action-btn:hover:not(:disabled) .btn-label { color: var(--white); }
  .action-btn:hover:not(:disabled) .btn-sub { color: #666; }
  .action-btn:hover:not(:disabled) .btn-arrow { color: var(--white); }
  .action-btn:hover:not(:disabled) .btn-num { color: #555; }

  .action-btn.active { background: var(--black); }
  .action-btn.active .btn-label { color: var(--white); }
  .action-btn.active .btn-sub { color: var(--accent); }
  .action-btn.active .btn-arrow { color: var(--accent); }
  .action-btn.active .btn-num { color: #555; }

  .action-btn:disabled { opacity: 0.35; cursor: not-allowed; }

  .btn-left { display: flex; flex-direction: column; gap: 5px; }

  .btn-num {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 10px;
    color: var(--gray);
    letter-spacing: 0.25em;
    transition: color 0.15s;
  }

  .btn-label {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 30px;
    letter-spacing: 0.15em;
    line-height: 1;
    color: var(--black);
    transition: color 0.15s;
  }

  .btn-sub {
    font-size: 9px;
    letter-spacing: 0.2em;
    color: var(--gray);
    text-transform: uppercase;
    transition: color 0.15s;
  }

  .btn-arrow {
    font-size: 18px;
    color: var(--border);
    transition: color 0.15s;
    font-family: 'Bebas Neue', sans-serif;
  }

  .panel-right {
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .status-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding: 16px 40px 14px;
    border-bottom: 1px solid var(--border);
  }

  .status-title {
    font-size: 9px;
    letter-spacing: 0.35em;
    text-transform: uppercase;
    color: var(--gray);
  }

  .status-badge {
    font-size: 9px;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    padding: 3px 8px;
  }

  .status-badge.idle    { background: var(--border); color: var(--gray); }
  .status-badge.running { background: var(--black);  color: var(--white); }
  .status-badge.done    { background: var(--accent);  color: var(--white); }
  .status-badge.error   { background: #c0392b;        color: var(--white); }

  .progress-section {
    padding: 24px 40px 20px;
    border-bottom: 1px solid var(--border);
  }

  .progress-top {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 10px;
  }

  .progress-label {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 12px;
    letter-spacing: 0.3em;
  }

  .progress-pct {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 52px;
    line-height: 1;
    letter-spacing: 0.03em;
    transition: all 0.3s;
  }

  .progress-bar-track {
    width: 100%;
    height: 1px;
    background: var(--border);
    margin-top: 14px;
    position: relative;
  }

  .progress-bar-fill {
    height: 100%;
    background: var(--black);
    transition: width 0.4s cubic-bezier(0.4, 0, 0.2, 1);
    width: 0%;
  }

  .progress-bar-fill.done { background: var(--accent); }

  .progress-step {
    margin-top: 8px;
    font-size: 9px;
    letter-spacing: 0.2em;
    color: var(--gray);
    text-transform: uppercase;
    min-height: 14px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .log-section {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .log-header {
    padding: 14px 40px 12px;
    border-bottom: 1px solid var(--border);
    font-size: 9px;
    letter-spacing: 0.35em;
    text-transform: uppercase;
    color: var(--gray);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .log-clear {
    background: none;
    border: none;
    font-family: inherit;
    font-size: 9px;
    letter-spacing: 0.2em;
    color: var(--gray);
    cursor: pointer;
    text-transform: uppercase;
  }
  .log-clear:hover { color: var(--black); }

  .log-body {
    flex: 1;
    overflow-y: auto;
    padding: 16px 40px 80px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .log-body::-webkit-scrollbar { width: 2px; }
  .log-body::-webkit-scrollbar-thumb { background: var(--border); }

  .log-entry {
    display: grid;
    grid-template-columns: 64px 1fr;
    gap: 14px;
    align-items: baseline;
    animation: fadeIn 0.15s ease;
  }

  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(3px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  .log-time {
    font-size: 9px;
    color: var(--border);
    letter-spacing: 0.1em;
    white-space: nowrap;
  }

  .log-msg {
    font-size: 10px;
    letter-spacing: 0.06em;
    line-height: 1.5;
    word-break: break-word;
  }

  .log-msg.ok     { color: var(--black); }
  .log-msg.info   { color: var(--gray); }
  .log-msg.accent { color: var(--accent); }
  .log-msg.error  { color: #c0392b; }
  .log-msg.sep    { color: var(--border); }

  footer {
    border-top: 1px solid var(--black);
    padding: 10px 40px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 9px;
    letter-spacing: 0.2em;
    color: var(--gray);
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    background: var(--white);
  }

  .spinner {
    display: none;
    width: 8px;
    height: 8px;
    border: 1px solid var(--border);
    border-top-color: var(--black);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    margin-left: 6px;
    vertical-align: middle;
  }
  .spinner.visible { display: inline-block; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<header>
  <div class="logo">LÉ SANG</div>
  <div class="header-center">PANEL DE OPERACIONES</div>
  <div class="header-right" id="clock">—</div>
</header>

<div class="layout">

  <div class="panel-left">
    <div class="panel-title">ACCIONES</div>
    <div class="actions">

      <button class="action-btn" id="btn-group" onclick="run('/run-group', 'btn-group', 'CREAR CARPETAS')">
        <div class="btn-left">
          <span class="btn-num">01</span>
          <span class="btn-label">CREAR CARPETAS</span>
          <span class="btn-sub">Agrupar imágenes en Drive</span>
        </div>
        <span class="btn-arrow">→</span>
      </button>

      <button class="action-btn" id="btn-ingest" onclick="run('/run-ingest', 'btn-ingest', 'SUBIR A LA NUBE')">
        <div class="btn-left">
          <span class="btn-num">02</span>
          <span class="btn-label">SUBIR A LA NUBE</span>
          <span class="btn-sub">Procesar e ingestar en Supabase</span>
        </div>
        <span class="btn-arrow">→</span>
      </button>

      <button class="action-btn" id="btn-shopify" onclick="run('/run-shopify', 'btn-shopify', 'PUBLICAR')">
        <div class="btn-left">
          <span class="btn-num">03</span>
          <span class="btn-label">PUBLICAR</span>
          <span class="btn-sub">Crear drafts en Shopify</span>
        </div>
        <span class="btn-arrow">→</span>
      </button>

    </div>
  </div>

  <div class="panel-right">

    <div class="status-header">
      <span class="status-title">ESTADO DEL SISTEMA</span>
      <span class="status-badge idle" id="status-badge">ESPERANDO</span>
    </div>

    <div class="progress-section">
      <div class="progress-top">
        <span class="progress-label">PROGRESO</span>
        <span class="progress-pct" id="pct-display">0%</span>
      </div>
      <div class="progress-bar-track">
        <div class="progress-bar-fill" id="progress-fill"></div>
      </div>
      <div class="progress-step" id="step-label">Esperando acción...</div>
    </div>

    <div class="log-section">
      <div class="log-header">
        <span>LOG <span class="spinner" id="spinner"></span></span>
        <button class="log-clear" onclick="clearLog()">LIMPIAR</button>
      </div>
      <div class="log-body" id="log-body">
        <div class="log-entry">
          <span class="log-time">—</span>
          <span class="log-msg info">Sistema listo. Seleccioná una acción.</span>
        </div>
      </div>
    </div>

  </div>
</div>

<footer>
  <span>LÉ SANG © 2025</span>
  <span id="footer-action">—</span>
  <span>ALFREDO BARROS ERRAZURIZ #1982</span>
</footer>

<script>
  // ── RELOJ ──
  function updateClock() {
    const now = new Date();
    const pad = n => String(n).padStart(2,'0');
    document.getElementById('clock').textContent = pad(now.getHours()) + ':' + pad(now.getMinutes()) + ':' + pad(now.getSeconds());
  }
  setInterval(updateClock, 1000);
  updateClock();

  // ── LOG ──
  let prevLog = '';

  function addLog(msg, type = 'ok') {
    if (!msg.trim()) return;
    const body = document.getElementById('log-body');
    const now = new Date();
    const pad = n => String(n).padStart(2,'0');
    const time = pad(now.getHours()) + ':' + pad(now.getMinutes()) + ':' + pad(now.getSeconds());
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    entry.innerHTML = '<span class="log-time">' + time + '</span><span class="log-msg ' + type + '">' + msg + '</span>';
    body.appendChild(entry);
    body.scrollTop = body.scrollHeight;
  }

  function clearLog() {
    document.getElementById('log-body').innerHTML = '';
    prevLog = '';
  }

  // Convierte líneas nuevas del log del servidor en entradas individuales
  function processNewLog(fullLog) {
    const newText = fullLog.slice(prevLog.length);
    prevLog = fullLog;
    const lines = newText.split('\\n').filter(l => l.trim());
    lines.forEach(line => {
      // Filtrar líneas de separación (====)
      if (/^=+$/.test(line.trim())) return;
      const type = line.includes('✔') ? 'ok'
                 : line.includes('✘') ? 'error'
                 : line.startsWith('  ') ? 'info'
                 : 'ok';
      addLog(line.trim(), type);
    });
  }

  // ── BADGE ──
  function setBadge(state) {
    const badge = document.getElementById('status-badge');
    badge.className = 'status-badge ' + state;
    const labels = { idle: 'ESPERANDO', running: 'EN CURSO', done: 'COMPLETADO', error: 'ERROR' };
    badge.textContent = labels[state] || state.toUpperCase();
  }

  // ── PROGRESS ──
  function setProgress(pct, step) {
    document.getElementById('pct-display').textContent = Math.round(pct) + '%';
    const fill = document.getElementById('progress-fill');
    fill.style.width = pct + '%';
    if (pct >= 100) fill.classList.add('done');
    else fill.classList.remove('done');
    if (step) document.getElementById('step-label').textContent = step.toUpperCase();
  }

  // ── BOTONES ──
  function setAllDisabled(disabled) {
    ['btn-group','btn-ingest','btn-shopify'].forEach(id => {
      document.getElementById(id).disabled = disabled;
    });
  }

  function setActive(btnId) {
    ['btn-group','btn-ingest','btn-shopify'].forEach(id => {
      document.getElementById(id).classList.remove('active');
    });
    if (btnId) document.getElementById(btnId).classList.add('active');
  }

  // ── POLLING ──
  let pollInterval = null;

  function startPolling() {
    pollInterval = setInterval(async () => {
      try {
        const res = await fetch('/status');
        const data = await res.json();

        setProgress(data.progress, data.step);
        setBadge(data.status);

        if (data.log) processNewLog(data.log);

        if (data.status === 'done' || data.status === 'error') {
          clearInterval(pollInterval);
          setAllDisabled(false);
          setActive(null);
          document.getElementById('spinner').classList.remove('visible');
          document.getElementById('footer-action').textContent = '—';
          if (data.status === 'done') {
            addLog('Proceso completado.', 'accent');
          } else {
            addLog('Error en el proceso. Revisá el log.', 'error');
          }
        }
      } catch (e) {
        // silencioso — red temporalmente caída
      }
    }, 500);
  }

  // ── RUN ──
  async function run(endpoint, btnId, label) {
    setAllDisabled(true);
    setActive(btnId);
    setBadge('running');
    setProgress(0, 'Iniciando...');
    prevLog = '';
    document.getElementById('spinner').classList.add('visible');
    document.getElementById('footer-action').textContent = label;
    addLog('→ Iniciando: ' + label, 'accent');

    try {
      const res = await fetch(endpoint, { method: 'POST' });
      const data = await res.json();

      if (!data.ok) {
        setBadge('error');
        addLog('✘ ' + data.message, 'error');
        setAllDisabled(false);
        setActive(null);
        document.getElementById('spinner').classList.remove('visible');
        document.getElementById('footer-action').textContent = '—';
        return;
      }

      startPolling();

    } catch (e) {
      setBadge('error');
      addLog('✘ Error de conexión: ' + e.message, 'error');
      setAllDisabled(false);
      setActive(null);
      document.getElementById('spinner').classList.remove('visible');
      document.getElementById('footer-action').textContent = '—';
    }
  }
</script>

</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML


@app.get("/status")
def status():
    return JSONResponse({
        "progress": PROGRESS,
        "status": STATUS,
        "step": CURRENT_STEP,
        "log": LAST_LOG,
        "running": IS_RUNNING,
    })


@app.post("/run-group")
def run_group():
    ok, message = start_script("auto_group_to_drive.py")
    return JSONResponse({"ok": ok, "message": message})


@app.post("/run-ingest")
def run_ingest():
    ok, message = start_script("ingest.py")
    return JSONResponse({"ok": ok, "message": message})


@app.post("/run-shopify")
def run_shopify():
    ok, message = start_script("push_to_shopify.py")
    return JSONResponse({"ok": ok, "message": message})