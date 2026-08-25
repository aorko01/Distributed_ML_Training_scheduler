import os
import time
import asyncio
import threading
import platform
import logging

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import config as config_module
import runtime_config
import telemetry
from io_monitor import io_monitor
from hardware import (
    get_or_create_worker_id, get_hostname, get_ip_address, get_gpu_info,
    count_gpus_in_use, get_cpu_load, get_mem_usage, get_mem_total_gb,
    get_gpu_temperature, docker_available, cuda_available, get_os_info,
    get_gpus_info,
)

logger = logging.getLogger("server")

app = FastAPI(title="Worker Agent API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class WorkerInfo(BaseModel):
    workerId: str
    hostname: str
    ipAddress: str
    os: str
    platform: str
    arch: str
    schedulerUrl: str
    heartbeatIntervalSec: int
    jobPollIntervalSec: int
    dockerAvailable: bool
    cudaAvailable: bool
    cpus: int
    memTotalGb: float
    gpuCount: int
    gpuName: str
    gpuVramTotalGb: float


class Metrics(BaseModel):
    cpuLoad: float
    memUsage: float
    memTotalGb: float
    gpuLoad: float
    vramUsedGb: float
    vramFreeGb: float
    vramTotalGb: float
    gpuTempC: float
    gpusInUse: int
    diskReadBytesPerS: float
    diskWriteBytesPerS: float
    netRecvBytesPerS: float
    netSentBytesPerS: float
    timestamp: float


class GpuInfo(BaseModel):
    index: int
    name: str
    load: float
    vramUsedGb: float
    vramFreeGb: float
    vramTotalGb: float
    temperatureC: float
    inUse: bool


class JobRecord(BaseModel):
    id: str
    image: str
    type: str
    status: str
    vramEstimateGb: float
    startedAt: str
    durationSec: int


class EventRecord(BaseModel):
    time: str
    level: str
    message: str


class ConfigState(BaseModel):
    schedulerUrl: str
    heartbeatIntervalSec: float
    jobPollIntervalSec: float
    logPushIntervalSec: float
    logUploadIntervalSec: float


class Status(BaseModel):
    connected: bool
    lastHeartbeatAt: str | None
    schedulerUrl: str
    paused: bool


def _build_worker_info() -> WorkerInfo:
    gpu_name, total_vram, _, num_gpus, _ = get_gpu_info()
    return WorkerInfo(
        workerId=get_or_create_worker_id(),
        hostname=get_hostname(),
        ipAddress=get_ip_address(),
        os=get_os_info(),
        platform=platform.system().lower(),
        arch=platform.machine(),
        schedulerUrl=config_module.get_scheduler_url(),
        heartbeatIntervalSec=int(runtime_config.get("heartbeat_interval")),
        jobPollIntervalSec=int(runtime_config.get("job_poll_interval")),
        dockerAvailable=docker_available(),
        cudaAvailable=cuda_available(),
        cpus=os.cpu_count() or 1,
        memTotalGb=get_mem_total_gb(),
        gpuCount=num_gpus,
        gpuName=gpu_name,
        gpuVramTotalGb=total_vram,
    )


def _build_metrics() -> Metrics:
    gpu_name, total_vram, free_vram, _, gpu_load = get_gpu_info()
    used_vram = round(max(0.0, total_vram - free_vram), 2)
    io = io_monitor.sample()
    return Metrics(
        cpuLoad=get_cpu_load(),
        memUsage=get_mem_usage(),
        memTotalGb=get_mem_total_gb(),
        gpuLoad=gpu_load,
        vramUsedGb=used_vram,
        vramFreeGb=free_vram,
        vramTotalGb=total_vram,
        gpuTempC=get_gpu_temperature(),
        gpusInUse=count_gpus_in_use(),
        diskReadBytesPerS=io["diskReadBytesPerS"],
        diskWriteBytesPerS=io["diskWriteBytesPerS"],
        netRecvBytesPerS=io["netRecvBytesPerS"],
        netSentBytesPerS=io["netSentBytesPerS"],
        timestamp=time.time(),
    )


def _build_status() -> Status:
    heartbeat = telemetry.get_heartbeat()
    last_at = heartbeat["last_success_at"]
    connected = last_at is not None and (time.time() - last_at) <= runtime_config.get("heartbeat_interval") * 3
    last_heartbeat_at = (
        time.strftime("%H:%M:%S", time.localtime(last_at)) if last_at else None
    )
    return Status(
        connected=connected,
        lastHeartbeatAt=last_heartbeat_at,
        schedulerUrl=config_module.get_scheduler_url(),
        paused=telemetry.is_paused(),
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/worker", response_model=WorkerInfo)
def get_worker():
    return _build_worker_info()


@app.get("/api/metrics", response_model=Metrics)
def get_metrics():
    return _build_metrics()


@app.get("/api/gpus", response_model=list[GpuInfo])
def get_gpus():
    return get_gpus_info()


@app.get("/api/jobs", response_model=list[JobRecord])
def get_jobs():
    return telemetry.get_jobs()


@app.get("/api/events", response_model=list[EventRecord])
def get_events():
    return telemetry.get_events()


@app.get("/api/status", response_model=Status)
def get_status():
    return _build_status()


@app.get("/api/config", response_model=ConfigState)
def get_config():
    return ConfigState(
        schedulerUrl=config_module.get_scheduler_url(),
        heartbeatIntervalSec=runtime_config.get("heartbeat_interval"),
        jobPollIntervalSec=runtime_config.get("job_poll_interval"),
        logPushIntervalSec=runtime_config.get("log_push_interval"),
        logUploadIntervalSec=runtime_config.get("log_upload_interval"),
    )


class ConfigUpdate(BaseModel):
    schedulerUrl: str | None = None
    heartbeatIntervalSec: float | None = None
    jobPollIntervalSec: float | None = None
    logPushIntervalSec: float | None = None
    logUploadIntervalSec: float | None = None


@app.put("/api/config", response_model=ConfigState)
def update_config(update: ConfigUpdate):
    pairs = {}
    if update.heartbeatIntervalSec is not None:
        pairs["heartbeat_interval"] = update.heartbeatIntervalSec
    if update.jobPollIntervalSec is not None:
        pairs["job_poll_interval"] = update.jobPollIntervalSec
    if update.logPushIntervalSec is not None:
        pairs["log_push_interval"] = update.logPushIntervalSec
    if update.logUploadIntervalSec is not None:
        pairs["log_upload_interval"] = update.logUploadIntervalSec
    runtime_config.set_many(pairs)

    if update.schedulerUrl is not None and update.schedulerUrl.strip() != config_module.get_scheduler_url():
        new_url = update.schedulerUrl.strip().rstrip("/")
        config_module.set_scheduler_url(new_url)
        config_module.persist_env({"SCHEDULER_URL": new_url})
        telemetry.record_event("info", f"Scheduler URL updated to {new_url}")

    if pairs:
        telemetry.record_event("info", f"Runtime config updated: {pairs}")
    return get_config()


@app.post("/api/control/pause")
def pause():
    telemetry.set_paused(True)
    telemetry.record_event(
        "warn",
        "Worker paused by user — heartbeats and job polling stopped",
    )
    return _build_status()


@app.post("/api/control/resume")
def resume():
    telemetry.set_paused(False)
    telemetry.record_event("info", "Worker resumed; job polling restarted")
    return _build_status()


class InteractiveRunRequest(BaseModel):
    flag: str = "interactive"
    session_id: str
    image_tag: str
    headscale_url: str
    headscale_auth_key: str
    ssh_public_key: str


@app.post("/api/interactive/run")
def run_interactive(body: InteractiveRunRequest):
    """Scheduler push-dispatch: deploy an interactive sandbox container in the
    background and return immediately."""
    import interactive_handler

    if body.flag != "interactive":
        raise HTTPException(status_code=400, detail="Unsupported flag")

    threading.Thread(
        target=interactive_handler.run_interactive_container,
        args=(
            _get_executor_api(),
            body.session_id,
            body.image_tag,
            body.headscale_url,
            body.headscale_auth_key,
            body.ssh_public_key,
        ),
        kwargs={},
        name=f"interactive-run-{body.session_id[:8]}",
        daemon=True,
    ).start()

    telemetry.record_event("info", f"Interactive session {body.session_id} dispatch accepted")
    return {"session_id": body.session_id, "status": "deploying"}


_executor_api = None


def set_executor_api(api):
    """Called by main.py so server routes can reach SchedulerAPI."""
    global _executor_api
    _executor_api = api


def _get_executor_api():
    if _executor_api is None:
        import config as cfg
        from api import SchedulerAPI
        from hardware import get_or_create_worker_id

        return SchedulerAPI(get_or_create_worker_id())
    return _executor_api


def _dump(model: BaseModel) -> dict:
    dump = getattr(model, "model_dump", None)
    if dump is not None:
        return dump()
    return model.dict()


@app.websocket("/ws/metrics")
async def ws_metrics(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            payload = {
                "metrics": _dump(_build_metrics()),
                "gpus": [_dump(g) for g in get_gpus()],
            }
            await websocket.send_json(payload)
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("WebSocket closed: %s", e)


def run(host: str = "127.0.0.1", port: int = 8600):
    import uvicorn

    logger.info("Starting Worker Agent API on http://%s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level="warning")


def run_in_thread(host: str = "127.0.0.1", port: int = 8600) -> threading.Thread:
    thread = threading.Thread(
        target=run, args=(host, port), name="api-server", daemon=True
    )
    thread.start()
    return thread


if __name__ == "__main__":
    run()
