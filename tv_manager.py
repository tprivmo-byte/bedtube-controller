import ctypes
from ctypes import wintypes
import json
import logging
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

from audio_manager import get_audio_state, set_volume, switch_audio_by_match
from config_store import load_config, save_config
from paths import LOCALAPPDATA_ROOT, find_resource

logger = logging.getLogger("bedtube.tv")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

HWND_BROADCAST = 0xFFFF
WM_SYSCOMMAND = 0x0112
SC_MONITORPOWER = 0xF170
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

CREATE_NO_WINDOW = 0x08000000

VALID_QUALITIES = {"1080", "max", "720", "auto"}

KEY_MAP = {
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "enter": "Enter",
    "back": "Escape",
    "space": "MediaPlayPause",
    "playpause": "MediaPlayPause",
    "search": "Search",
    "home": "Home",
    "ff": "FastForward",
    "rw": "Rewind",
}

ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", _INPUT_UNION),
    ]


def _send_mouse_jiggle():
    """Envia evento real de entrada (SendInput) exigido pelo Windows 11 Modern Standby para religar o monitor."""
    for dx in (1, -1):
        inp = INPUT()
        inp.type = INPUT_MOUSE
        inp.u.mi.dx = dx
        inp.u.mi.dy = 0
        inp.u.mi.mouseData = 0
        inp.u.mi.dwFlags = MOUSEEVENTF_MOVE
        inp.u.mi.time = 0
        inp.u.mi.dwExtraInfo = 0
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(0.035)


def _find_free_port(preferred: int = 5051) -> int:
    for candidate in [preferred, preferred + 1, preferred + 2, 0]:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", candidate))
                return s.getsockname()[1]
        except OSError:
            continue
    return preferred


class TVSessionManager:
    """
    Gerencia o motor dedicado BedTube TV Shell (Electron + Cobalt 27 1080p60/4K60),
    isolamento persistente sem extensões, Watchdog de restauração de áudio,
    controle confiável de energia do monitor (Apagar / Ligar Tela) e Desligamento Seguro do PC.
    """

    def __init__(self, on_state_change=None):
        self._lock = threading.RLock()
        self.proc: subprocess.Popen | None = None
        self.tv_active: bool = False
        self.screen_off: bool = False
        self._active_control_port: int = int(load_config().get("tv_control_port", 5051))
        self._pre_tv_device_id: str = ""
        self._pre_tv_device_name: str = ""
        self.sleep_deadline: float | None = None
        self.pc_shutdown_deadline: float | None = None
        self._pc_shutdown_triggered: bool = False
        self.on_state_change = on_state_change
        self._stop_event = threading.Event()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="BedTubeWatchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    def _notify(self):
        if callable(self.on_state_change):
            try:
                self.on_state_change(self.is_tv_running())
            except Exception:
                pass

    def _get_control_base(self) -> str:
        return f"http://127.0.0.1:{self._active_control_port}"

    def _tv_http(self, path: str, payload: dict | None = None, timeout: float = 0.85) -> dict | None:
        url = f"{self._get_control_base()}{path}"
        try:
            if payload is None:
                req = urllib.request.Request(url, method="GET")
            else:
                raw = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=raw,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return json.loads(body)
        except Exception:
            return None

    def _resolve_electron_exe(self, cfg: dict) -> str | None:
        candidates = [
            cfg.get("electron_path") or "",
            find_resource("runtime", "electron", "electron.exe"),
            os.path.join(LOCALAPPDATA_ROOT, "BedTube_Runtime", "electron", "electron.exe"),
        ]
        for path in candidates:
            if path and os.path.exists(path):
                return path
        return None

    def _resolve_shell_dir(self) -> str:
        return find_resource("tv_shell")

    def wake_monitor(self):
        """Religa imediatamente o monitor do PC (compatível com Windows 10/11 Modern Standby)."""
        try:
            self.screen_off = False
            kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
            )
            user32.PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, -1)
            _send_mouse_jiggle()
            user32.mouse_event(MOUSEEVENTF_MOVE, 1, 0, 0, 0)
            time.sleep(0.02)
            user32.mouse_event(MOUSEEVENTF_MOVE, -1, 0, 0, 0)
            if self.is_tv_running():
                self._tv_http("/cmd", {"action": "focus"}, timeout=0.45)
        except Exception as exc:
            logger.warning("Aviso ao acordar monitor: %s", exc)
        self._notify()

    def _allow_monitor_sleep_again(self):
        try:
            kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception:
            pass

    def turn_off_monitor(self):
        """Coloca o monitor em standby liberando bloqueio de exibição antes do comando."""
        self.screen_off = True
        self._allow_monitor_sleep_again()

        def _delayed_off():
            time.sleep(0.42)
            try:
                user32.PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)
            except Exception as exc:
                logger.warning("Aviso ao desligar monitor: %s", exc)

        threading.Thread(target=_delayed_off, daemon=True).start()
        self._notify()

    def is_tv_running(self) -> bool:
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                return True
        health = self._tv_http("/health", timeout=0.35)
        return bool(health and health.get("ok"))

    def get_sleep_remaining_min(self) -> int | None:
        if not self.sleep_deadline:
            return None
        rem = self.sleep_deadline - time.time()
        if rem <= 0:
            return None
        return max(1, int(round(rem / 60.0)))

    def set_sleep_timer(self, minutes: int) -> int | None:
        with self._lock:
            if minutes <= 0:
                self.sleep_deadline = None
                logger.info("Sleep timer cancelado.")
                return None
            self.sleep_deadline = time.time() + (minutes * 60)
            logger.info("Sleep timer agendado para %d minutos.", minutes)
            return minutes

    def get_pc_shutdown_remaining_sec(self) -> int | None:
        if not self.pc_shutdown_deadline:
            return None
        rem = self.pc_shutdown_deadline - time.time()
        if rem <= 0:
            return 0 if self._pc_shutdown_triggered else None
        return max(1, int(round(rem)))

    def schedule_pc_shutdown(self, mode: str = "timer", minutes: int = 0) -> dict:
        """
        Agenda ou cancela o desligamento real do Windows:
        - mode='cancel' ou minutes<=0 (quando mode!='now'): cancela desligamento agendado
        - mode='now': inicia contagem regressiva de segurança de 8s antes de desligar
        - mode='timer' com minutes>0: agenda desligamento em N minutos
        """
        clean_mode = (mode or "timer").strip().lower()
        with self._lock:
            if clean_mode == "cancel" or (clean_mode != "now" and int(minutes) <= 0):
                self.pc_shutdown_deadline = None
                self._pc_shutdown_triggered = False
                try:
                    subprocess.run(
                        ["shutdown.exe", "/a"],
                        creationflags=CREATE_NO_WINDOW,
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except Exception:
                    pass
                logger.info("Desligamento do PC cancelado pelo usuário.")
                self._notify()
                return {
                    "ok": True,
                    "pc_shutdown_remaining_sec": None,
                    "message": "Desligamento do PC cancelado",
                }

            if clean_mode == "now":
                delay_sec = 8
                self._pc_shutdown_triggered = False
                self.pc_shutdown_deadline = time.time() + delay_sec
                logger.info("Desligamento imediato do PC solicitado (contagem de segurança: %ds).", delay_sec)
                self._notify()
                return {
                    "ok": True,
                    "pc_shutdown_remaining_sec": delay_sec,
                    "message": f"Desligando PC em {delay_sec}s (toque em Cancelar p/ abortar)",
                }

            mins = max(1, min(720, int(minutes)))
            self._pc_shutdown_triggered = False
            self.pc_shutdown_deadline = time.time() + (mins * 60)
            logger.info("Desligamento do PC agendado para %d minutos.", mins)
            self._notify()
            return {
                "ok": True,
                "pc_shutdown_remaining_sec": mins * 60,
                "message": f"PC será desligado em {mins} min",
            }

    def _execute_pc_shutdown(self):
        """Fecha o Modo TV, restaura o áudio padrão (Fone/PC) p/ o próximo boot e desliga o Windows."""
        logger.warning("Executando desligamento do PC agendado pelo BedTube...")
        try:
            if self.tv_active or self.is_tv_running():
                self.close_tv(turn_off_screen=False)
            else:
                cfg = load_config()
                self._restore_pc_audio(cfg)
        except Exception as exc:
            logger.warning("Aviso ao preparar áudio antes de desligar PC: %s", exc)

        try:
            subprocess.Popen(
                [
                    "shutdown.exe",
                    "/s",
                    "/f",
                    "/t",
                    "3",
                    "/c",
                    "BedTube Controller: Desligando o PC. Boa noite!",
                ],
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception as exc:
            logger.error("Erro ao disparar shutdown.exe: %s", exc)

    def set_tv_quality(self, pref: str) -> dict:
        clean = (pref or "1080").strip().lower()
        if clean in ("1080p", "fhd", "fullhd"):
            clean = "1080"
        elif clean in ("4k", "2160", "2160p", "1440", "1440p", "best", "maxima"):
            clean = "max"
        elif clean in ("720p", "hd"):
            clean = "720"
        if clean not in VALID_QUALITIES:
            clean = "1080"

        save_config({"preferred_quality": clean})

        applied_live = False
        stats = None
        if self.is_tv_running():
            res = self._tv_http(
                "/cmd",
                {"action": "set_quality", "pref": clean, "quality": clean},
                timeout=1.5,
            )
            if res and res.get("ok"):
                applied_live = True
                stats = res.get("result")

        return {
            "ok": True,
            "preferred_quality": clean,
            "applied_live": applied_live,
            "stats": stats,
        }

    def get_tv_player_stats(self) -> dict:
        cfg = load_config()
        pref = cfg.get("preferred_quality", "1080")
        if not self.is_tv_running():
            return {"ok": False, "tv_open": False, "preferred_quality": pref}
        res = self._tv_http("/cmd", {"action": "player_stats"}, timeout=1.2)
        if res and res.get("ok"):
            return {
                "ok": True,
                "tv_open": True,
                "preferred_quality": pref,
                "stats": res.get("stats"),
            }
        return {"ok": False, "tv_open": True, "preferred_quality": pref}

    def open_tv(self) -> dict:
        with self._lock:
            cfg = load_config()
            self.wake_monitor()

            if not self.tv_active:
                cur_audio = get_audio_state()
                if cur_audio.get("ok") and cur_audio.get("device_id"):
                    self._pre_tv_device_id = cur_audio["device_id"]
                    self._pre_tv_device_name = cur_audio.get("device_name", "")

            sb_match = (cfg.get("soundbar_match") or "").strip()
            night_vol = int(cfg.get("night_volume", 25))
            if sb_match:
                ok_audio, dev_msg = switch_audio_by_match(
                    sb_match,
                    volume=night_vol,
                    unmute=True,
                    retries=3,
                )
            else:
                set_volume(night_vol)
                cur_audio = get_audio_state()
                ok_audio = True
                dev_msg = cur_audio.get("device_name", "Saída Atual")

            if self.is_tv_running():
                self._tv_http("/cmd", {"action": "focus"})
                self.tv_active = True
                self._notify()
                return {
                    "ok": True,
                    "already_open": True,
                    "audio_switched": ok_audio,
                    "device": dev_msg,
                    "message": f"YouTube TV em foco • {dev_msg}",
                }

            electron_exe = self._resolve_electron_exe(cfg)
            if not electron_exe:
                return {
                    "ok": False,
                    "message": "Motor BedTube TV (Electron) não encontrado na instalação.",
                }

            shell_dir = self._resolve_shell_dir()
            if not os.path.exists(os.path.join(shell_dir, "main.js")):
                return {
                    "ok": False,
                    "message": f"Pasta tv_shell incompleta em: {shell_dir}",
                }

            preferred_port = int(cfg.get("tv_control_port", 5051))
            self._active_control_port = _find_free_port(preferred_port)

            tv_data_dir = cfg.get("tv_data_dir") or os.path.join(LOCALAPPDATA_ROOT, "BedTube_TV_Data")
            os.makedirs(tv_data_dir, exist_ok=True)

            pref_quality = str(cfg.get("preferred_quality", "1080"))
            env = os.environ.copy()
            env.pop("ELECTRON_RUN_AS_NODE", None)
            env["BEDTUBE_CONTROL_PORT"] = str(self._active_control_port)
            env["BEDTUBE_TV_DATA_DIR"] = tv_data_dir
            env["BEDTUBE_TV_URL"] = cfg.get("tv_url", "https://www.youtube.com/tv")
            env["BEDTUBE_PREF_QUALITY"] = pref_quality
            env["BEDTUBE_PREFERRED_QUALITY"] = pref_quality

            try:
                self.proc = subprocess.Popen(
                    [electron_exe, shell_dir],
                    cwd=shell_dir,
                    env=env,
                    creationflags=CREATE_NO_WINDOW,
                )
            except Exception as exc:
                logger.error("Erro ao iniciar BedTube TV Shell: %s", exc)
                return {"ok": False, "message": f"Erro ao abrir TV: {exc}"}

            ready = False
            for _ in range(32):
                time.sleep(0.25)
                if self.proc.poll() is not None:
                    break
                h = self._tv_http("/health", timeout=0.35)
                if h and h.get("ok"):
                    ready = True
                    break

            self.tv_active = True
            logger.info("BedTube TV Shell iniciado (PID=%s, porta=%s, ready=%s)", self.proc.pid, self._active_control_port, ready)

        self._notify()
        return {
            "ok": True,
            "already_open": False,
            "audio_switched": ok_audio,
            "device": dev_msg,
            "message": f"Modo TV Ativado ({dev_msg} a {night_vol}%)",
        }

    def _restore_pc_audio(self, cfg: dict) -> tuple[bool, str]:
        hs_match = (cfg.get("headset_match") or "").strip()
        if hs_match:
            return switch_audio_by_match(hs_match, volume=None, unmute=False, retries=2)
        if self._pre_tv_device_id:
            return switch_audio_by_match(self._pre_tv_device_id, volume=None, unmute=False, retries=2)
        cur = get_audio_state()
        return True, cur.get("device_name", "Saída Padrão")

    def close_tv(self, turn_off_screen: bool = False) -> dict:
        with self._lock:
            cfg = load_config()
            self.sleep_deadline = None

            self._tv_http("/cmd", {"action": "quit"}, timeout=0.6)

            proc = self.proc
            if proc is not None:
                try:
                    proc.wait(timeout=2.5)
                except subprocess.TimeoutExpired:
                    try:
                        proc.terminate()
                        proc.wait(timeout=1.5)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
            self.proc = None
            self.tv_active = False
            self._allow_monitor_sleep_again()

            ok_audio, dev_msg = self._restore_pc_audio(cfg)

            if turn_off_screen:
                self.turn_off_monitor()

            logger.info("Modo TV encerrado. Áudio restaurado: %s", dev_msg)

        self._notify()
        return {
            "ok": True,
            "audio_switched": ok_audio,
            "device": dev_msg,
            "message": f"TV fechada • Áudio devolvido para {dev_msg}",
        }

    def send_tv_key(self, action_name: str) -> bool:
        action = action_name.lower().strip()
        mapped = KEY_MAP.get(action)
        if not mapped:
            return False

        if not self.is_tv_running():
            return False

        res = self._tv_http("/cmd", {"action": "key", "key": mapped}, timeout=0.9)
        return bool(res and res.get("ok"))

    def search_tv_text(self, query: str) -> bool:
        clean = (query or "").strip()
        if not clean or not self.is_tv_running():
            return False
        res = self._tv_http("/cmd", {"action": "type_text", "text": clean}, timeout=3.5)
        return bool(res and res.get("ok"))

    def _watchdog_loop(self):
        while not self._stop_event.is_set():
            try:
                now = time.time()

                # Verifica temporizador de Desligamento do PC
                trigger_pc_shutdown = False
                with self._lock:
                    if (
                        self.pc_shutdown_deadline
                        and not self._pc_shutdown_triggered
                        and now >= self.pc_shutdown_deadline
                    ):
                        self._pc_shutdown_triggered = True
                        self.pc_shutdown_deadline = None
                        trigger_pc_shutdown = True

                if trigger_pc_shutdown:
                    self._execute_pc_shutdown()
                    continue

                if self.sleep_deadline and now >= self.sleep_deadline:
                    logger.info("Sleep Timer atingido! Encerrando TV e apagando tela...")
                    self.sleep_deadline = None
                    self.close_tv(turn_off_screen=True)
                    continue

                should_revert = False
                with self._lock:
                    if self.tv_active:
                        alive = False
                        if self.proc is not None and self.proc.poll() is None:
                            alive = True
                        else:
                            h = self._tv_http("/health", timeout=0.35)
                            alive = bool(h and h.get("ok"))

                        if not alive:
                            self.proc = None
                            self.tv_active = False
                            self.sleep_deadline = None
                            should_revert = True

                if should_revert:
                    self._allow_monitor_sleep_again()
                    cfg = load_config()
                    ok_audio, dev_msg = self._restore_pc_audio(cfg)
                    logger.info(
                        "Watchdog detectou fechamento da TV. Áudio revertido (%s): %s",
                        ok_audio,
                        dev_msg,
                    )
                    self._notify()
            except Exception as exc:
                logger.error("Erro no Watchdog: %s", exc)

            self._stop_event.wait(1.0)

    def shutdown(self):
        self._stop_event.set()
        if self.tv_active or self.is_tv_running():
            try:
                self.close_tv(turn_off_screen=False)
            except Exception:
                pass
