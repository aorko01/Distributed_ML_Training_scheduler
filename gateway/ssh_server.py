import logging
import os
import select
import socket
import threading
import time
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
            verify_url = f"{config.SCHEDULER_API_URL}/interactive/sessions/verify-ephemeral"
            resp = requests.post(
                verify_url,
                json={"username": username, "password": password},
                timeout=5,
            )
            if resp.status_code != 200:
                logger.warning(f"Authentication failed for user {username}")
                return paramiko.AUTH_FAILED

            data = resp.json()
            self.session_id = data.get("session_id")
            self.headscale_ip = data.get("headscale_ip")
            if not self.session_id or not self.headscale_ip:
                logger.warning("Session data incomplete")
                return paramiko.AUTH_FAILED

            logger.info(f"User {username} authenticated successfully for session {self.session_id}")
            return paramiko.AUTH_SUCCESSFUL
        except Exception as e:
            logger.error(f"Error during authentication: {e}")
            return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        """Advertise both password and publickey auth so VS Code Remote SSH
        knows password auth is available."""
        return "password,publickey"

    def check_auth_publickey(self, username, key):
        """Reject publickey auth — sessions use ephemeral passwords only."""
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_shell_request(self, channel):
        # Just signal that the client asked for a shell; the connection
        # handler thread will launch the proxy once it claims the channel.
        channel.gateway_request = ("shell", None)
        self.event.set()
        return True

    def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes):
        channel.gateway_pty = (width, height)
        return True

    def check_channel_window_change_request(self, channel, width, height, pixelwidth, pixelheight):
        # We need a way to pass this to the client channel.
        # Store it on the server channel object temporarily so proxy loop can pick it up.
        channel.gateway_window_change = (width, height)
        return True

    def check_channel_exec_request(self, channel, command):
        """Accept exec requests — VS Code uses these for OS detection and setup
        before opening a shell."""
        channel.gateway_request = ("exec", command)
        self.event.set()
        return True

    def check_channel_subsystem_request(self, channel, name):
        """Accept subsystem requests (e.g. SFTP) for VS Code file sync."""
        channel.gateway_request = ("subsystem", name)
        self.event.set()
        return True

    def _proxy_loop(self, server_chan, client_chan):
        """Bidirectional byte-level proxy between two channels."""
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

    def _proxy_shell(self, server_chan):
        client = None
        try:
            logger.info(f"Connecting to container {self.headscale_ip} for session {self.session_id}")
            client = session_client.connect(self.session_id, self.headscale_ip)
            client_chan = client.invoke_shell()

            pty_info = getattr(server_chan, "gateway_pty", None)
            if pty_info:
                client_chan.resize_pty(*pty_info)

            self._proxy_loop(server_chan, client_chan)
        except Exception as e:
            logger.error(f"Proxy error: {e}")
            traceback.print_exc()
        finally:
            if client:
                session_client.close(client)
            server_chan.close()

    def _proxy_exec(self, server_chan, command):
        """Proxy an exec channel: run `command` on the container and stream output."""
        client = None
        try:
            logger.info(f"Connecting to container {self.headscale_ip} for session {self.session_id} (exec: {command!r})")
            client = session_client.connect(self.session_id, self.headscale_ip)
            client_chan = client.get_transport().open_session()
            client_chan.exec_command(command)

            self._proxy_loop(server_chan, client_chan)

            # Propagate exit status back to the client.
            try:
                deadline = time.time() + 10
                while not client_chan.exit_status_ready() and time.time() < deadline:
                    time.sleep(0.1)
                if client_chan.exit_status_ready():
                    server_chan.send_exit_status(client_chan.recv_exit_status())
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Exec proxy error: {e}")
            traceback.print_exc()
        finally:
            if client:
                session_client.close(client)
            server_chan.close()

    def _proxy_subsystem(self, server_chan, name):
        """Proxy a subsystem channel (e.g. SFTP) between client and container."""
        client = None
        try:
            logger.info(f"Connecting to container {self.headscale_ip} for session {self.session_id} (subsystem: {name})")
            client = session_client.connect(self.session_id, self.headscale_ip)
            client_chan = client.get_transport().open_session()
            client_chan.invoke_subsystem(name)

            self._proxy_loop(server_chan, client_chan)
        except Exception as e:
            logger.error(f"Subsystem proxy error ({name}): {e}")
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

    def _handle_channel(server, chan, addr):
        """Handle a single channel after auth and accept."""
        try:
            if not server.event.wait(timeout=15):
                logger.warning("No request from %s on channel; closing", addr)
                chan.close()
                return

            if not server.session_id or not server.headscale_ip:
                logger.warning("No active session for %s; closing", addr)
                chan.close()
                return

            # Wait briefly for per-channel request type to be set (may have been
            # set by a *different* channel's request that fired the shared event).
            req = getattr(chan, "gateway_request", None)
            deadline = time.time() + 5
            while req is None and time.time() < deadline:
                time.sleep(0.1)
                req = getattr(chan, "gateway_request", None)

            if req is None:
                logger.warning("No channel request type determined for %s; closing", addr)
                chan.close()
                return

            kind, payload = req
            if kind == "exec":
                server._proxy_exec(chan, payload)
            elif kind == "subsystem":
                server._proxy_subsystem(chan, payload)
            else:  # "shell" or fallback
                server._proxy_shell(chan)
        except Exception as e:
            logger.error("Channel handling error from %s: %s", addr, e)
            try:
                chan.close()
            except Exception:
                pass

    def _handle_connection(client_sock, addr):
        try:
            t = paramiko.Transport(client_sock)
            t.add_server_key(host_key)
            server = GatewayServer()
            try:
                t.start_server(server=server)
            except paramiko.SSHException:
                logger.warning("SSH negotiation failed from %s", addr)
                return

            # Accept channels in a loop — VS Code multiplexes exec, SFTP,
            # and shell channels over a single transport.
            while t.is_active():
                try:
                    chan = t.accept(30)
                except Exception:
                    break
                if chan is None:
                    # Timeout — check if transport is still alive.
                    if not t.is_active():
                        break
                    continue

                logger.info(f"Channel opened from {addr}: {chan.chanid}")
                threading.Thread(
                    target=_handle_channel,
                    args=(server, chan, addr),
                    daemon=True,
                ).start()
        except Exception as e:
            logger.error("Connection handling error from %s: %s", addr, e)
        finally:
            try:
                t.close()
            except Exception:
                pass

    def accept_loop():
        while True:
            try:
                client_sock, addr = sock.accept()
                logger.info(f"Accepted connection from {addr}")
                threading.Thread(
                    target=_handle_connection, args=(client_sock, addr), daemon=True
                ).start()
            except Exception as e:
                if sock.fileno() == -1:
                    break  # closed
                logger.error(f"Accept loop error: {e}")

    t = threading.Thread(target=accept_loop, daemon=True)
    t.start()
    return sock

def stop_ssh_server(sock):
    if sock:
        sock.close()
