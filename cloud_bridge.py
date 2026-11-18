import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
import logging
import os
import re
import ssl
import subprocess
import threading
import time

import paho.mqtt.client as mqtt

from config_store import load_config
from paths import find_resource

logger = logging.getLogger("bedtube.cloud")

CREATE_NO_WINDOW = 0x08000000
TRYCLOUDFLARE_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)

MQTT_BROKERS = [
    ("broker.emqx.io", 8883),
    ("broker.hivemq.com", 8884),
]

# Win32 Job Object para garantir que cloudflared.exe nunca fique órfão se o app fechar abruptamente
kernel32 = ctypes.windll.kernel32
JobObjectExtendedLimitInformation = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _create_kill_on_close_job():
    try:
        hjob = kernel32.CreateJobObjectW(None, None)
        if not hjob:
            return None
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            hjob,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        return hjob if ok else None
    except Exception:
        return None


def _assign_pid_to_job(hjob, pid: int) -> None:
    if not hjob or not pid:
        return
    try:
        PROCESS_SET_QUOTA = 0x0100
        PROCESS_TERMINATE = 0x0001
        hproc = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, int(pid))
        if hproc:
            try:
                kernel32.AssignProcessToJobObject(hjob, hproc)
            finally:
                kernel32.CloseHandle(hproc)
    except Exception:
        pass


class CloudBridge:
    """
    Ponte dupla Zero-Config para contornar isolamento Cabo <-> Wi-Fi e bloqueios de IP local:
    1) Cloudflare Quick Tunnel (HTTPS público temporário protegido por token criptográfico).
    2) Relay MQTT sobre TLS (mantém o PWA instalado funcionando mesmo após reiniciar o PC).
    """

    def __init__(self, local_port: int, execute_action_cb, status_builder_cb, on_tunnel_ready=None):
        self.local_port = local_port
        self.execute_action_cb = execute_action_cb
        self.status_builder_cb = status_builder_cb
        self.on_tunnel_ready = on_tunnel_ready

        self.tunnel_url: str | None = None
        self.tunnel_status: str = "iniciando"
        self.mqtt_connected: bool = False
        self.mqtt_broker_active: str = ""

        self._stop_event = threading.Event()
        self._cf_proc: subprocess.Popen | None = None
        self._job_handle = _create_kill_on_close_job()
        self._mqtt_client: mqtt.Client | None = None
        self._seen_nonces: dict[str, float] = {}
        self._lock = threading.RLock()

    def get_cloudflared_path(self) -> str:
        return find_resource("bin", "cloudflared.exe")

    def get_pairing_url(self, lan_fallback_url: str) -> str:
        cfg = load_config()
        token = cfg["access_token"]
        if self.tunnel_url:
            return f"{self.tunnel_url}/?t={token}"
        return f"{lan_fallback_url}/?t={token}"

    def start(self):
        cfg = load_config()
        if cfg.get("enable_cloud_tunnel", True):
            threading.Thread(
                target=self._tunnel_supervisor_loop,
                name="CloudflaredSupervisor",
                daemon=True,
            ).start()
        else:
            self.tunnel_status = "desativado"

        if cfg.get("enable_mqtt_relay", True):
            threading.Thread(
                target=self._mqtt_supervisor_loop,
                name="MQTTRelaySupervisor",
                daemon=True,
            ).start()

    def _tunnel_supervisor_loop(self):
        cf_exe = self.get_cloudflared_path()
        if not os.path.exists(cf_exe):
            self.tunnel_status = "cloudflared_ausente"
            logger.warning("cloudflared.exe não encontrado em %s", cf_exe)
            return

        while not self._stop_event.is_set():
            self.tunnel_status = "conectando"
            cmd = [
                cf_exe,
                "tunnel",
                "--url",
                f"http://127.0.0.1:{self.local_port}",
                "--no-autoupdate",
                "--edge-ip-version",
                "auto",
            ]
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=CREATE_NO_WINDOW,
                )
                self._cf_proc = proc
                _assign_pid_to_job(self._job_handle, proc.pid)
                logger.info("cloudflared iniciado (PID=%s)", proc.pid)

                if proc.stdout:
                    for line in proc.stdout:
                        if self._stop_event.is_set():
                            break
                        match = TRYCLOUDFLARE_RE.search(line)
                        if match:
                            found_url = match.group(0).rstrip("/")
                            if found_url != self.tunnel_url:
                                self.tunnel_url = found_url
                                self.tunnel_status = "online"
                                logger.info("Túnel HTTPS ativo: %s", self.tunnel_url)
                                self.publish_state()
                                if callable(self.on_tunnel_ready):
                                    try:
                                        self.on_tunnel_ready(self.tunnel_url)
                                    except Exception:
                                        pass

                proc.wait(timeout=3)
            except Exception as exc:
                logger.error("Erro no cloudflared: %s", exc)
                self.tunnel_status = "reconectando"
            finally:
                self._cf_proc = None

            if not self._stop_event.is_set():
                self.tunnel_status = "reconectando"
                self._stop_event.wait(4.0)

    def _sign_hmac(self, token: str, ts: int, nonce: str, cmd_str: str) -> str:
        msg = f"{ts}:{nonce}:{cmd_str}".encode("utf-8")
        return hmac.new(token.encode("utf-8"), msg, hashlib.sha256).hexdigest()

    def _verify_mqtt_envelope(self, data: dict) -> bool:
        cfg = load_config()
        token = cfg.get("access_token", "")
        try:
            ts = int(data.get("ts", 0))
            nonce = str(data.get("nonce", ""))
            cmd_str = str(data.get("cmd", ""))
            sig = str(data.get("sig", ""))
        except Exception:
            return False

        if not nonce or not cmd_str or not sig:
            return False

        now_ms = int(time.time() * 1000)
        if abs(now_ms - ts) > 180_000:
            logger.warning("Comando MQTT rejeitado por timestamp fora de janela.")
            return False

        with self._lock:
            cutoff = time.time() - 240
            self._seen_nonces = {k: v for k, v in self._seen_nonces.items() if v > cutoff}
            if nonce in self._seen_nonces:
                return False
            expected = self._sign_hmac(token, ts, nonce, cmd_str)
            if not hmac.compare_digest(expected, sig):
                logger.warning("Comando MQTT rejeitado por assinatura HMAC inválida.")
                return False
            self._seen_nonces[nonce] = time.time()
            return True

    def _mqtt_topics(self) -> tuple[str, str, str]:
        topic_id = load_config()["relay_topic_id"]
        base = f"bedtube/v1/{topic_id}"
        return f"{base}/cmd", f"{base}/state", f"{base}/ack"

    def publish_state(self, extra: dict | None = None):
        client = self._mqtt_client
        if not client or not self.mqtt_connected:
            return
        try:
            _, state_topic, _ = self._mqtt_topics()
            payload = self.status_builder_cb()
            payload["tunnel_url"] = self.tunnel_url
            payload["ts"] = int(time.time() * 1000)
            if extra:
                payload.update(extra)
            client.publish(state_topic, json.dumps(payload), qos=1, retain=True)
        except Exception as exc:
            logger.debug("Aviso ao publicar estado MQTT: %s", exc)

    def _mqtt_supervisor_loop(self):
        broker_idx = 0
        while not self._stop_event.is_set():
            host, port = MQTT_BROKERS[broker_idx % len(MQTT_BROKERS)]
            broker_idx += 1
            try:
                client = mqtt.Client(
                    mqtt.CallbackAPIVersion.VERSION2,
                    client_id=f"bedtube-pc-{os.urandom(4).hex()}",
                    protocol=mqtt.MQTTv5,
                )
                client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

                def on_connect(cli, userdata, flags, reason_code, properties):
                    if reason_code == 0:
                        self.mqtt_connected = True
                        self.mqtt_broker_active = f"{host}:{port}"
                        cmd_topic, _, _ = self._mqtt_topics()
                        cli.subscribe(cmd_topic, qos=1)
                        logger.info("Relay MQTT conectado em %s (%s)", host, cmd_topic)
                        self.publish_state()
                    else:
                        logger.warning("Falha MQTT connect reason=%s", reason_code)

                def on_disconnect(cli, userdata, flags, reason_code, properties):
                    self.mqtt_connected = False

                def on_message(cli, userdata, msg):
                    try:
                        data = json.loads(msg.payload.decode("utf-8"))
                    except Exception:
                        return
                    if not self._verify_mqtt_envelope(data):
                        return

                    cmd_str = str(data.get("cmd", ""))
                    req_id = str(data.get("req_id", data.get("nonce", "")))
                    params = data.get("params") if isinstance(data.get("params"), dict) else {}

                    if cmd_str == "ping":
                        res = self.status_builder_cb()
                    else:
                        res = self.execute_action_cb(cmd_str, params)

                    res["req_id"] = req_id
                    res["tunnel_url"] = self.tunnel_url
                    res["ts"] = int(time.time() * 1000)

                    _, _, ack_topic = self._mqtt_topics()
                    cli.publish(ack_topic, json.dumps(res), qos=1, retain=False)
                    self.publish_state()

                client.on_connect = on_connect
                client.on_disconnect = on_disconnect
                client.on_message = on_message

                self._mqtt_client = client
                client.connect(host, port, keepalive=45)
                client.loop_forever()
            except Exception as exc:
                self.mqtt_connected = False
                logger.warning("Relay MQTT desconectado (%s:%s): %s", host, port, exc)

            if not self._stop_event.is_set():
                self._stop_event.wait(5.0)

    def shutdown(self):
        self._stop_event.set()
        if self._mqtt_client:
            try:
                self._mqtt_client.disconnect()
            except Exception:
                pass
        if self._cf_proc and self._cf_proc.poll() is None:
            try:
                self._cf_proc.terminate()
                self._cf_proc.wait(timeout=2)
            except Exception:
                try:
                    self._cf_proc.kill()
                except Exception:
                    pass
        if self._job_handle:
            try:
                kernel32.CloseHandle(self._job_handle)
            except Exception:
                pass
            self._job_handle = None
