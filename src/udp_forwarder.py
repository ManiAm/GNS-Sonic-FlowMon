
import sys
import os
import time
import getpass
import socket
import threading
import base64
import logging
import paramiko
from paramiko.config import SSHConfig

log = logging.getLogger(__name__)


class SSHHop:

    def __init__(self, hostname, username, key_filename=None):

        self.hostname = hostname
        self.username = username
        self.key_filename = key_filename

        self.client = None
        self.transport = None


    def connect(self, sock=None):

        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        self.client.connect(
            hostname=self.hostname,
            username=self.username,
            key_filename=self.key_filename,
            sock=sock,
        )

        self.transport = self.client.get_transport()
        log.info("[+] Connected to %s", self.hostname)
        return self


    def open_tunnel(self, target_host, target_port=22):

        return self.transport.open_channel(
            kind="direct-tcpip",
            dest_addr=(target_host, target_port),
            src_addr=("localhost", 0)
        )


    def exec_command(self, command, get_pty=False):
        return self.client.exec_command(command, get_pty=get_pty)


    def close(self):
        if self.client:
            self.client.close()


    def has_socat(self):

        _, stdout, _ = self.exec_command("command -v socat")
        output = stdout.read().decode().strip()
        return bool(output)


    def upload_file(self, local_path, remote_path):

        try:

            sftp = self.client.open_sftp()
            sftp.put(local_path, remote_path)
            sftp.chmod(remote_path, 0o755)
            sftp.close()

        except:

            self.upload_base64(local_path, remote_path)


    def upload_base64(self, local_path, remote_path):

        with open(local_path, 'rb') as f:
            b64_content = base64.b64encode(f.read()).decode()

        command = f"base64 -d > {remote_path} && chmod +x {remote_path}"
        shell = self.client.invoke_shell()
        shell.send(f"echo '{b64_content}' | {command}\n")
        time.sleep(2)
        shell.close()


class UDPForwarder:

    def __init__(self,
                 run_socat_on,
                 key_filename=None,
                 ssh_config_file="~/.ssh/config",
                 target_port=6343,
                 listen_port=6343,
                 reconnect_interval=5):

        self.run_socat_on = run_socat_on
        self.key_filename = key_filename
        self.ssh_config_file = os.path.expanduser(ssh_config_file)
        self.target_port = target_port
        self.listen_port = listen_port
        self.reconnect_interval = reconnect_interval

        self.username = getpass.getuser()
        self.ssh_config = self._load_ssh_config()
        self.ssh_chain = self._build_ssh_chain()

        self.target_host = self._infer_local_ip(self._get_socat_host_ip())

        self.running = False
        self.socat_thread = None
        self._started_event = threading.Event()

        script_file = os.path.realpath(__file__)
        script_file = os.path.abspath(script_file)
        script_dir = os.path.dirname(script_file)

        self.local_socat_path = os.path.join(script_dir, "socat", "socat-1.7.4.4-x86_64")
        self.remote_socat_path = "/tmp/socat"


    def _load_ssh_config(self):

        config = SSHConfig()
        with open(self.ssh_config_file) as f:
            config.parse(f)
        return config


    def _build_ssh_chain(self):

        chain = []
        current = self.run_socat_on
        jump_stack = []

        # Walk up the proxy chain from run_socat_on
        while current:
            jump_stack.insert(0, current)
            entry = self.ssh_config.lookup(current)
            current = entry.get("proxyjump")

        for alias in jump_stack:
            chain.append(self._build_hop(alias))

        return chain


    def _build_hop(self, host_alias):

        entry = self.ssh_config.lookup(host_alias)

        hostname = entry.get("hostname", host_alias)
        username = entry.get("user", self.username)
        identityfiles = entry.get("identityfile")
        key_filename = os.path.expanduser(identityfiles[0]) if identityfiles else self.key_filename

        return SSHHop(
            hostname=hostname,
            username=username,
            key_filename=key_filename
        )


    def _get_socat_host_ip(self):

        entry = self.ssh_config.lookup(self.run_socat_on)
        return entry.get("hostname", self.run_socat_on)


    def _infer_local_ip(self, remote_host):

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((remote_host, 80))
                ip = s.getsockname()[0]
                log.info("[+] Auto-detected local IP: %s", ip)
                return ip
        except Exception as e:
            log.error("[!] Could not determine local IP: %s", str(e))
            return "127.0.0.1"


    def _run_socat(self, try_max=3):

        try_count = 0

        while self.running:

            try_count += 1

            try:

                self.build_chain()

                final_hop = self.ssh_chain[-1]

                if not final_hop.has_socat():

                    log.warning("[!] socat not found on %s, uploading static binary...", final_hop.hostname)

                    try:
                        final_hop.upload_file(self.local_socat_path, self.remote_socat_path)
                        log.info("[+] Uploaded socat to %s", self.remote_socat_path)
                    except Exception as e:
                        log.error("[!] Failed to upload socat: %s", str(e))
                        sys.exit(1)

                    socat_cmd = self.remote_socat_path
                else:
                    socat_cmd = "socat"

                cmd = f"{socat_cmd} UDP-LISTEN:{self.listen_port},fork UDP:{self.target_host}:{self.target_port}"
                log.info("[+] Running on %s:%s", final_hop.hostname, cmd)

                _, stdout, stderr = final_hop.exec_command(cmd, get_pty=True)

                # Wait a few seconds in case socat exits quickly
                time.sleep(2)

                if stdout.channel.exit_status_ready():

                    exit_code = stdout.channel.recv_exit_status()
                    log.error("[!] socat exited with code %s", exit_code)

                    stderr_output = stderr.read().decode().strip()
                    if stderr_output:
                        log.error("[stderr] %s", stderr_output)

                    if try_count >= try_max:
                        log.error("[!] socat not running. Maximum retry reached...\n")
                        sys.exit(1)

                    log.info("[!] socat not running. Will retry...\n")
                    time.sleep(self.reconnect_interval)
                    continue

                else:

                    log.info("[+] socat started successfully.")

                    self._started_event.set()

                    for line in iter(stdout.readline, ""):
                        if not self.running:
                            break
                        log.info("[socat] %s", line.strip())

            except Exception as e:
                log.info("[!] Error: %s. Retrying in %ss...\n", str(e), self.reconnect_interval)
                time.sleep(self.reconnect_interval)

            finally:
                for hop in reversed(self.ssh_chain):
                    hop.close()


    def build_chain(self):

        sock = None
        for i, hop in enumerate(self.ssh_chain[:-1]):
            hop.connect(sock=sock)
            next_host = self.ssh_chain[i + 1].hostname
            sock = hop.open_tunnel(next_host)
        self.ssh_chain[-1].connect(sock=sock)


    def start(self):

        self.running = True
        self.socat_thread = threading.Thread(target=self._run_socat, daemon=True)
        self.socat_thread.name = f"socat_{self.run_socat_on}"
        self.socat_thread.start()

        self._started_event.wait()
        log.info("[+] Forwarder is running.")


    def stop(self):

        self.running = False
        log.info("[-] Stopping forwarder...")
