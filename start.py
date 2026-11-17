"""Start the venv backend and pnpm dev in visible server consoles."""
from pathlib import Path
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parent

def ready(url, name):
    try:
        with urlopen(url,timeout=1) as response:
            text = response.read().decode('utf-8')
            return json.loads(text).get('service') == name if name == 'SpeakSpot' else '<title>SpeakSpot</title>' in text and '/@vite/client' in text
    except Exception:
        return False

def occupied(port):
    with socket.socket() as client:
        client.settimeout(.5)
        return client.connect_ex(('127.0.0.1',port)) == 0

def main():
    python = ROOT / ('backend/.venv/Scripts/python.exe' if os.name == 'nt' else 'backend/.venv/bin/python')
    pnpm = shutil.which('pnpm.cmd' if os.name == 'nt' else 'pnpm')
    if not python.is_file() or not pnpm or not (ROOT/'frontend/node_modules/vite').exists():
        raise RuntimeError('Install dependencies using README.md first.')
    started = []
    try:
        services = [('backend',8000,'http://127.0.0.1:8000/api/health','SpeakSpot',
                     [str(python),'-m','uvicorn','main:app','--host','127.0.0.1','--port','8000']),
                    ('frontend',5173,'http://127.0.0.1:5173/','frontend',[pnpm,'dev'])]
        for folder,port,url,name,command in services:
            if ready(url,name):
                print(f'{folder}: already running',flush=True)
                continue
            if occupied(port):
                raise RuntimeError(f'Port {port} is occupied by another service.')
            if os.name == 'nt':
                invocation = '& ' + ' '.join("'" + part.replace("'", "''") + "'" for part in command)
                process = subprocess.Popen(['powershell.exe','-NoLogo','-NoProfile','-NoExit','-Command',
                    f"$Host.UI.RawUI.WindowTitle = 'SpeakSpot {folder}'; {invocation}"],
                    cwd=ROOT/folder,creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                process = subprocess.Popen(command,cwd=ROOT/folder)
            started.append(process)
            deadline = time.monotonic()+60
            while not ready(url,name):
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(f'{folder} could not start. Check the {folder} server window.')
                time.sleep(.3)
            print(f'{folder}: ready',flush=True)
    except Exception:
        if os.name != 'nt':
            for process in started:
                if process.poll() is None:
                    process.terminate()
        raise
    if '--no-browser' not in sys.argv:
        webbrowser.open('http://127.0.0.1:5173')
    print('SpeakSpot: http://127.0.0.1:5173',flush=True)
    print('Logs appear in the backend/frontend server windows. Close a server window to stop it.',flush=True)

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(error,file=sys.stderr)
        sys.exit(1)
