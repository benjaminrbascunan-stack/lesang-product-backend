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


@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
    <head>
        <title>Lé Sang</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                background: white;
                color: black;
                padding: 40px;
                text-align: center;
            }

            .logo {
                display: block;
                margin: 0 auto 40px auto;
                width: 220px;
            }

            .container {
                width: 300px;
                margin: 0 auto;
                text-align: left;
            }

            button {
                width: 100%;
                padding: 16px;
                margin: 12px 0;
                font-size: 16px;
                cursor: pointer;
                background: black;
                color: white;
                border: none;
                font-family: Arial, sans-serif;
            }

            button:disabled {
                background: #444;
            }

            #status, #step, #percent {
                margin-top: 12px;
                font-size: 14px;
                text-align: left;
            }

            #progress-bar {
                width: 100%;
                height: 10px;
                background: #eee;
                margin-top: 14px;
            }

            #progress-fill {
                height: 10px;
                width: 0%;
                background: black;
                transition: width 0.25s linear;
            }

            pre {
                background: black;
                color: white;
                padding: 20px;
                margin-top: 30px;
                white-space: pre-wrap;
                max-height: 500px;
                overflow: auto;
                font-size: 12px;
                font-family: Arial, sans-serif;
                text-align: left;
            }
        </style>
    </head>
    <body>

        <img class="logo" src="https://cdn.shopify.com/s/files/1/0862/4262/3795/files/logo.png?v=1776360657" />

        <div class="container">

            <button onclick="run('/run-group')">Crear carpetas</button>
            <button onclick="run('/run-ingest')">Subir a la nube</button>
            <button onclick="run('/run-shopify')">Publicar</button>

            <div id="status">Estado: esperando</div>
            <div id="step">Acción: ninguna</div>

            <div id="progress-bar">
                <div id="progress-fill"></div>
            </div>

            <div id="percent">0%</div>

        </div>

        <pre id="log">Esperando acción...</pre>

        <script>
            let interval = null;

            async function run(endpoint) {
                toggleButtons(true);

                document.getElementById('status').textContent = 'Estado: iniciando...';
                document.getElementById('step').textContent = 'Acción: preparando...';
                document.getElementById('log').textContent = '';

                const res = await fetch(endpoint, { method: 'POST' });
                const data = await res.json();

                if (!data.ok) {
                    document.getElementById('status').textContent = 'Estado: error';
                    document.getElementById('log').textContent = data.message;
                    toggleButtons(false);
                    return;
                }

                interval = setInterval(fetchStatus, 500);
            }

            async function fetchStatus() {
                const res = await fetch('/status');
                const data = await res.json();

                document.getElementById('progress-fill').style.width = data.progress + '%';
                document.getElementById('percent').textContent = data.progress + '%';
                document.getElementById('status').textContent = 'Estado: ' + data.status;
                document.getElementById('step').textContent = 'Acción: ' + data.step;
                document.getElementById('log').textContent = data.log || '';

                const logBox = document.getElementById('log');
                logBox.scrollTop = logBox.scrollHeight;

                if (data.status === 'done' || data.status === 'error') {
                    clearInterval(interval);
                    toggleButtons(false);
                }
            }

            function toggleButtons(disabled) {
                document.querySelectorAll("button").forEach(btn => {
                    btn.disabled = disabled;
                });
            }
        </script>

    </body>
    </html>
    """


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