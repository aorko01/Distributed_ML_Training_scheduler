import logging
import os
import select
import socket
import threading
import traceback
import requests
import paramiko

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import config
from session_client import session_client

logger = logging.getLogger("ssh_server")

def load_or_generate_host_key(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        try:
            return paramiko.Ed25519Key(filename=path)
        except Exception as e:
            logger.warning(f"Failed to load existing host key at {path}: {e}")
    
    logger.info(f"Generating new Ed25519 host key at {path}")
    private_key = Ed25519PrivateKey.generate()
    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with open(path, "wb") as f:
        f.write(priv_pem)
    os.chmod(path, 0o600)
    return paramiko.Ed25519Key(filename=path)

class GatewayServer(paramiko.ServerInterface):
    def __init__(self):
        self.session_id = None
        self.headscale_ip = None
        self.event = threading.Event()

    def check_auth_password(self, username, password):
        try:
            login_url = f"{config.SCHEDULER_API_URL}/auth/login"
            resp = requests.post(login_url, json={"username": username, "password": password}, timeout=5)
            if resp.status_code != 200:
                logger.warning(f"Authentication failed for user {username}")
                return paramiko.AUTH_FAILED
            
            token = resp.json().get("access_token")
            if not token:
                logger.warning("No access token returned from login")
                return paramiko.AUTH_FAILED

            active_sessions_url = f"{config.SCHEDULER_API_URL}/interactive/sessions/active"
            headers = {"Authorization": f"Bearer {token}"}
            sess_resp = requests.get(active_sessions_url, headers=headers, timeout=5)
            if sess_resp.status_code != 200:
                logger.warning(f"No active session for user {username}")
                return paramiko.AUTH_FAILED
            
            sess_data = sess_resp.json()
            self.session_id = sess_data.get("session_id")
            self.headscale_ip = sess_data.get("headscale_ip")
            if not self.session_id or not self.headscale_ip:
                logger.warning("Session data incomplete")
                return paramiko.AUTH_FAILED
            
            logger.info(f"User {username} authenticated successfully for session {self.session_id}")
            return paramiko.AUTH_SUCCESSFUL
        except Exception as e:
            logger.error(f"Error during authentication: {e}")
            return paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_shell_request(self, channel):
        self.event.set()
        threading.Thread(target=self._proxy_shell, args=(channel,)).start()
        return True

    def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes):
        self.pty_info = (width, height)
        return True

    def check_channel_window_change_request(self, channel, width, height, pixelwidth, pixelheight):
        # We need a way to pass this to the client channel.
        # Store it on the server channel object temporarily so proxy loop can pick it up.
        channel.gateway_window_change = (width, height)
        return True

    def _proxy_shell(self, server_chan):
        client = None
        try:
            logger.info(f"Connecting to container {self.headscale_ip} for session {self.session_id}")
            client = session_client.connect(self.session_id, self.headscale_ip)
            client_chan = client.invoke_shell()

            if hasattr(self, "pty_info"):
                client_chan.resize_pty(*self.pty_info)

            server_chan.setblocking(0)
            client_chan.setblocking(0)

            while True:
                r, _, _ = select.select([server_chan, client_chan], [], [], 1.0)

                if hasattr(server_chan, "gateway_window_change") and server_chan.gateway_window_change:
                    w, h = server_chan.gateway_window_change
                    server_chan.gateway_window_change = None
                    try:
                        client_chan.resize_pty(width=w, height=h)
                    except Exception as e:
                        logger.warning(f"Error resizing PTY: {e}")

                if server_chan in r:
                    try:
                        data = server_chan.recv(4096)
                        if len(data) == 0:
                            break
                        client_chan.sendall(data)
                    except socket.timeout:
                        pass
                
                if client_chan in r:
                    try:
                        data = client_chan.recv(4096)
                        if len(data) == 0:
                            break
                        server_chan.sendall(data)
                    except socket.timeout:
                        pass
                
                if server_chan.closed or client_chan.closed:
                    break

        except Exception as e:
            logger.error(f"Proxy error: {e}")
            traceback.print_exc()
        finally:
            if client:
                session_client.close(client)
            server_chan.close()

def start_ssh_server():
    host_key = load_or_generate_host_key(config.GATEWAY_HOST_KEY_PATH)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", config.GATEWAY_SSH_PORT))
    sock.listen(100)
    logger.info(f"SSH Gateway listening on port {config.GATEWAY_SSH_PORT}")

    def accept_loop():
        while True:
            try:
                client_sock, addr = sock.accept()
                logger.info(f"Accepted connection from {addr}")
                t = paramiko.Transport(client_sock)
                t.add_server_key(host_key)
                server = GatewayServer()
                try:
                    t.start_server(server=server)
                except paramiko.SSHException:
                    logger.warning("SSH negotiation failed")
                    continue
                
                # accept channels in the background so accept_loop isn't blocked
                def wait_for_channel(t, server):
                    chan = t.accept(20)
                    if chan is None:
                        logger.warning("No channel opened")
                        t.close()
                threading.Thread(target=wait_for_channel, args=(t, server)).start()
                
            except Exception as e:
                if sock.fileno() == -1:
                    break # closed
                logger.error(f"Accept loop error: {e}")

    t = threading.Thread(target=accept_loop, daemon=True)
    t.start()
    return sock

def stop_ssh_server(sock):
    if sock:
        sock.close()
