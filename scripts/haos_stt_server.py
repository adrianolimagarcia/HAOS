#!/usr/bin/env python3
"""haos-stt: OpenAI-compatible transcription server backed by the HAOS faster-whisper pipeline.

POST /v1/audio/transcriptions (multipart) with the same contract as OpenAI's
whisper-1 API. Reuses tools.transcription_local (build_local_transcribe_kwargs +
_load_local_whisper_model) so results match what the HAOS gateway produces.

Auth: Authorization: Bearer *** — token read from HAOS_STT_TOKEN env var
(stored in ~/.haos/.env as HAOS_STT_TOKEN). If unset, server binds loopback-only
and requires no auth.
"""
import asyncio
import gc
import io
import os
import sys
import time
from contextlib import asynccontextmanager, suppress

# Repo root (this file lives in <root>/scripts/) so the host install path is not baked in.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, Response
from fastapi.responses import JSONResponse, PlainTextResponse

HERMES_HOME = os.environ.get("HAOS_HOME", "/root/.haos")
API_TOKEN = os.environ.get("HAOS_STT_TOKEN", "")
# Regra do dono (2026-09-13): o STT roda em medium ou large — nunca base/small.
# Usado apenas quando config.yaml nao define stt.local.model.
HAOS_STT_FALLBACK_MODEL = "large-v3"

# Unload por ociosidade de verdade (correcao 2026-09-15): antes o check rodava no
# `finally` do handler, depois de `_get_model()` renovar `last_used`, entao a
# ociosidade media ~0s e o modelo NUNCA saia da VRAM — a GTX 1050 Ti (4 GB) e
# compartilhada com o docling-serve, que ficava sem placa.
_IDLE_CHECK_SECONDS = 15.0
_state = {"model": None, "key": None, "last_used": 0.0, "effective": None, "inflight": 0}


def _release_model():
    """Solta o modelo e devolve a VRAM ao host."""
    _state["model"] = None
    _state["key"] = None
    _state["effective"] = None  # /health nao pode reportar "loaded_as" com o modelo solto
    gc.collect()
    try:
        import torch  # opcional: installs so-CTranslate2 nao tem torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass
    print("[haos-stt] model unloaded after idle", flush=True)


async def _idle_unload_loop():
    while True:
        await asyncio.sleep(_IDLE_CHECK_SECONDS)
        try:
            _maybe_unload()
        except Exception as exc:  # noqa: BLE001
            print("[haos-stt] idle unload check falhou:", exc, flush=True)


@asynccontextmanager
async def _lifespan(_app):
    task = None
    if _unload_after_idle_seconds():
        task = asyncio.create_task(_idle_unload_loop())
        print("[haos-stt] idle unload ativo:", _unload_after_idle_seconds(), "s", flush=True)
    yield
    if task:
        # Await the cancellation: returning with the task still pending lets the loop
        # close under it and asyncio reports "Task was destroyed but it is pending".
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="HAOS STT", version="1.0.0", lifespan=_lifespan)


def _stt_config():
    path = os.path.join(HERMES_HOME, "config.yaml")
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
    except FileNotFoundError:
        return {}
    return (cfg or {}).get("stt") or {}


def _unload_after_idle_seconds():
    loc = _stt_config().get("local") or {}
    return int(loc.get("unload_after_idle_seconds") or 0)


def _load_with_fallback(model_name: str, device: str, compute_type: str):
    """Carrega o modelo pedido; se a GPU estiver sem VRAM, cai para CPU int8.

    A GTX 1050 Ti tem 4 GB e o host compartilha a placa com outros servicos
    (ex.: docling-serve). OOM nao e erro de configuracao: sem este fallback o
    servico devolve 500 em TODA requisicao enquanto a VRAM estiver ocupada.
    Mantemos o modelo pedido (medium/large) — so muda o device.
    """
    from tools.transcription_local import _load_local_whisper_model

    try:
        return (_load_local_whisper_model(model_name, device, compute_type),
                (model_name, device, compute_type))
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        oom = "out of memory" in msg or "cuda failed" in msg or "cuda_error" in msg
        if device != "cpu" and oom:
            print(f"[haos-stt] VRAM indisponivel ({exc}) — recarregando {model_name} em CPU int8",
                  flush=True)
            return (_load_local_whisper_model(model_name, "cpu", "int8"),
                    (model_name, "cpu", "int8"))
        raise


def _get_model():
    stt = _stt_config()
    loc = stt.get("local") or {}
    # Fonte de verdade = config.yaml (stt.local.*). O servidor NAO fixa modelo:
    # o hardcode anterior ("small") divergia do config e do contrato do STT
    # (large-v3/pt/GPU), mantendo o gate de evals vermelho sem ninguem notar.
    # Fallback deliberado = large-v3 (regra do dono: STT so em medium ou large;
    # nunca base/small — por isso NAO usar DEFAULT_LOCAL_MODEL, que e "base").
    key = (
        loc.get("model") or HAOS_STT_FALLBACK_MODEL,
        loc.get("device") or "cuda",
        loc.get("compute_type") or "int8",
    )
    if _state["model"] is None or _state["key"] != key:
        model, effective = _load_with_fallback(*key)
        _state["model"] = model
        # guarda a chave PEDIDA (nao a efetiva): o proximo load apos o unload
        # por inatividade tenta a GPU de novo, caso ela tenha sido liberada.
        _state["key"] = key
        _state["effective"] = effective
        print("[haos-stt] model loaded:", effective, "| requested:", key, flush=True)
    _state["last_used"] = time.time()
    return _state["model"], stt


def _maybe_unload():
    secs = _unload_after_idle_seconds()
    if not secs or _state["model"] is None:
        return
    if _state.get("inflight"):  # nunca soltar o modelo com requisicao em andamento
        return
    if time.time() - _state["last_used"] > secs:
        _release_model()


def _check_auth(request: Request):
    if not API_TOKEN:
        return
    auth = request.headers.get("authorization", "")
    if auth != "Bearer " + API_TOKEN:
        raise HTTPException(status_code=401, detail="invalid bearer token")


def _transcribe_bytes(data: bytes, filename: str):
    import tempfile

    from tools.transcription_local import build_local_transcribe_kwargs

    model, stt = _get_model()
    suffix = os.path.splitext(filename or "audio.ogg")[1] or ".ogg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as f:
        f.write(data)
        f.flush()
        kw = build_local_transcribe_kwargs(stt)
        segments, info = model.transcribe(f.name, **kw)
        text = " ".join(s.text.strip() for s in segments)
    return text, info


@app.get("/health")
def health():
    return {"status": "ok", "service": "haos-stt",
            "model_loaded": _state["model"] is not None,
            "loaded_as": list(_state["effective"]) if _state["effective"] else None}


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    request: Request,
    file: UploadFile = File(...),
    model: str = Form("whisper-1"),
    language: str = Form(None),
    response_format: str = Form("text"),
):
    _check_auth(request)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    t0 = time.time()
    _state["inflight"] += 1
    try:
        text, info = _transcribe_bytes(data, file.filename or "audio.ogg")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"transcription failed: {e}")
    finally:
        _state["inflight"] -= 1

    if response_format == "text":
        return PlainTextResponse(text)

    return JSONResponse({
        "text": text,
        "duration": round(getattr(info, "duration", 0.0) or 0.0, 2),
        "elapsed_s": round(time.time() - t0, 2),
    })


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HAOS_STT_HOST", "0.0.0.0" if API_TOKEN else "127.0.0.1")
    port = int(os.environ.get("HAOS_STT_PORT", "8645"))
    uvicorn.run(app, host=host, port=port, log_level="info")
