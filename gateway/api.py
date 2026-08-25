import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from session_client import session_client
from ssh_key_manager import ssh_key_manager

logger = logging.getLogger("api")

router = APIRouter()


class KeyCreateRequest(BaseModel):
    session_id: str


class KeyResponse(BaseModel):
    session_id: str
    public_key: str


class KeyDeleteResponse(BaseModel):
    session_id: str
    status: str


class ConnectRequest(BaseModel):
    session_id: str
    target_ip: str
    command: str


class ConnectResponse(BaseModel):
    session_id: str
    target_ip: str
    output: str


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/keys", response_model=KeyResponse)
def create_key(body: KeyCreateRequest):
    if ssh_key_manager.get_private_key_path(body.session_id):
        existing = ssh_key_manager.get_public_key(body.session_id)
        if existing:
            return {"session_id": body.session_id, "public_key": existing}
        raise HTTPException(status_code=500, detail="Keypair exists but is unreadable")

    _, public_key = ssh_key_manager.generate_keypair(body.session_id)
    return {"session_id": body.session_id, "public_key": public_key}


@router.delete("/keys/{session_id}", response_model=KeyDeleteResponse)
def delete_key(session_id: str):
    deleted = ssh_key_manager.delete_keypair(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="No keypair for this session")
    return {"session_id": session_id, "status": "deleted"}


@router.post("/connect", response_model=ConnectResponse)
def connect(body: ConnectRequest):
    client = None
    try:
        client = session_client.connect(body.session_id, body.target_ip)
        output = session_client.execute_command(client, body.command)
        return {
            "session_id": body.session_id,
            "target_ip": body.target_ip,
            "output": output,
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("SSH command failed for session %s -> %s: %s",
                     body.session_id, body.target_ip, e)
        raise HTTPException(status_code=502, detail=f"SSH command failed: {e}")
    finally:
        if client is not None:
            session_client.close(client)
