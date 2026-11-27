"""Serve the production UI and API in the Windows app."""

import argparse
import hmac
import mimetypes
import os
from pathlib import Path
import runpy
import sys
from threading import Thread
import traceback


HOST = "127.0.0.1"
PORT = 27395
ORIGIN = f"http://{HOST}:{PORT}"


def configure_data(root):
    models = root / "Models"
    os.environ.update(
        HF_HOME=str(models / "huggingface"),
        HF_HUB_CACHE=str(models / "huggingface" / "hub"),
        SENTENCE_TRANSFORMERS_HOME=str(models / "huggingface" / "hub"),
        HF_HUB_DISABLE_XET="1",
        HF_HUB_DISABLE_SYMLINKS_WARNING="1",
        EASYOCR_MODULE_PATH=str(models / "easyocr"),
        DOCLING_CACHE_DIR=str(models / "docling"),
        TORCH_HOME=str(models / "torch"),
        SPEAKSPOT_ANALYSIS_CACHE_DIR=str(root / "Documents" / "Analysis"),
    )


def exit_with_parent(pid):
    from ctypes import WinDLL, get_last_error, wintypes

    kernel = WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    parent = kernel.OpenProcess(0x00100000, False, pid)
    if not parent:
        raise OSError(get_last_error(), "Could not monitor the SpeakSpot launcher")

    def wait():
        kernel.WaitForSingleObject(parent, 0xFFFFFFFF)
        kernel.CloseHandle(parent)
        os._exit(0)

    Thread(target=wait, daemon=True).start()


def serve(parent_pid):
    import uvicorn
    from fastapi import Request, Response
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    from starlette.staticfiles import StaticFiles
    from main import app

    token = os.environ.pop("SPEAKSPOT_LAUNCH_TOKEN").encode("ascii")
    exit_with_parent(parent_pid)
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, timeout_graceful_shutdown=3))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[HOST])

    @app.middleware("http")
    async def restrict_local_api(request: Request, call_next):
        if request.url.path.startswith(("/api/", "/_speakspot/")):
            origin = request.headers.get("origin")
            site = request.headers.get("sec-fetch-site")
            if (origin and origin != ORIGIN) or site in {"cross-site", "same-site"}:
                return Response(status_code=403)
        return await call_next(request)

    @app.api_route("/_speakspot/control", methods=["GET", "POST"], include_in_schema=False)
    async def control(request: Request):
        supplied = request.headers.get("x-speakspot-launch-token", "").encode("utf-8")
        if not hmac.compare_digest(supplied, token):
            return Response(status_code=404)
        if request.method == "POST":
            server.should_exit = True
        return Response(status_code=204)

    frontend = Path(__file__).resolve().parents[1] / "web"
    # Windows file associations can override the MIME type of the PDF.js worker.
    mimetypes.add_type("text/javascript", ".mjs")
    app.mount("/", StaticFiles(directory=frontend, html=True), name="desktop")
    server.run()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "serve"])
    parser.add_argument("data", type=Path)
    parser.add_argument("--parent", type=int)
    args = parser.parse_args()
    configure_data(args.data)
    logs = args.data / "Logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / ("setup.log" if args.mode == "prepare" else "backend.log")
    with log_path.open("w", encoding="utf-8", buffering=1) as log:
        sys.stdout = sys.stderr = log
        try:
            if args.mode == "prepare":
                runpy.run_module("pdf", run_name="__main__")
                from kiwipiepy import Kiwi

                Kiwi().tokenize("SpeakSpot 설치 확인")
            else:
                serve(args.parent)
        except Exception:
            traceback.print_exc(file=log)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
