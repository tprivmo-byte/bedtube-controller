import ctypes
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import threading
import time
import webbrowser

from paths import APP_VERSION, LOG_PATH, get_autostart_enabled, set_autostart_enabled

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        RotatingFileHandler(LOG_PATH, maxBytes=1_500_000, backupCount=2, encoding="utf-8"),
    ],
    force=True,
)
logger = logging.getLogger("bedtube.main")


def _log_unhandled_exception(exc_type, exc_value, exc_tb):
    if issubclass(exc_type, KeyboardInterrupt):
        return
    logger.critical("Exceção não tratada no BedTube:", exc_info=(exc_type, exc_value, exc_tb))


sys.excepthook = _log_unhandled_exception

try:
    from waitress import serve as _waitress_serve
except ImportError:
    _waitress_serve = None

import pystray
from pystray import Menu, MenuItem

from actions import build_status_payload, execute_bedtube_action
from audio_manager import switch_audio_by_match
from cloud_bridge import CloudBridge
from config_store import load_config
from icon_gen import create_bedtube_icon, get_lan_ip
from tv_manager import TVSessionManager
from web_app import create_web_app

# Nitidez DPI no menu da bandeja em telas 125% / 150% / 4K
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

ERROR_ALREADY_EXISTS = 183


def acquire_single_instance_mutex():
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, "Local\\BedTubeController_Singleton_v2")
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        return None
    return handle


def main():
    is_background_boot = "--background" in sys.argv
    force_show_qr = "--show-qr" in sys.argv

    cfg = load_config()
    port = int(cfg.get("port", 5050))

    mutex = acquire_single_instance_mutex()
    if not mutex:
        logger.info("BedTube já está em execução.")
        if not is_background_boot:
            # Se o usuário clicou no atalho da Área de Trabalho / Menu Iniciar, abre o Hub!
            webbrowser.open(f"http://127.0.0.1:{port}/qr")
        sys.exit(0)

    tray_ref = {"icon": None}

    def on_tv_state_change(is_active: bool):
        icon = tray_ref.get("icon")
        if icon:
            try:
                icon.icon = create_bedtube_icon(64, active=is_active)
                st = "Modo TV Ativo" if is_active else "Modo PC"
                icon.title = f"BedTube v{APP_VERSION} ({st})"
            except Exception:
                pass
        if cloud_ref.get("bridge"):
            cloud_ref["bridge"].publish_state()

    tv_mgr = TVSessionManager(on_state_change=on_tv_state_change)
    cloud_ref = {"bridge": None}

    def on_tunnel_ready(url: str):
        icon = tray_ref.get("icon")
        if icon:
            try:
                icon.title = f"BedTube v{APP_VERSION} • Cloud Online"
            except Exception:
                pass

    bridge = CloudBridge(
        local_port=port,
        execute_action_cb=lambda cmd, params: execute_bedtube_action(tv_mgr, cmd, params),
        status_builder_cb=lambda: build_status_payload(tv_mgr),
        on_tunnel_ready=on_tunnel_ready,
    )
    cloud_ref["bridge"] = bridge
    bridge.start()

    def graceful_shutdown():
        logger.info("Encerrando BedTube Controller graciosamente...")
        bridge.shutdown()
        tv_mgr.shutdown()
        icon = tray_ref.get("icon")
        if icon:
            try:
                icon.stop()
            except Exception:
                pass
        time.sleep(0.25)
        os._exit(0)

    app = create_web_app(tv_mgr, cloud_bridge=bridge, shutdown_cb=graceful_shutdown)

    def run_server():
        logger.info("Servidor BedTube v%s ouvindo em 0.0.0.0:%d", APP_VERSION, port)
        if _waitress_serve is not None:
            _waitress_serve(app, host="0.0.0.0", port=port, threads=8)
        else:
            app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)

    threading.Thread(target=run_server, name="BedTubeWeb", daemon=True).start()

    def action_open_qr(icon=None, item=None):
        webbrowser.open(f"http://127.0.0.1:{port}/qr")

    def action_copy_url(icon=None, item=None):
        c = load_config()
        lan_url = f"http://{get_lan_ip()}:{port}"
        full_url = bridge.get_pairing_url(lan_url)
        try:
            import subprocess
            subprocess.run(
                ["clip"],
                input=full_url.encode("utf-16le"),
                creationflags=0x08000000,
                check=False,
            )
            if icon:
                icon.notify(full_url, "Link de Pareamento Copiado!")
        except Exception:
            pass

    def action_open_tv(icon=None, item=None):
        threading.Thread(target=tv_mgr.open_tv, daemon=True).start()

    def action_close_tv(icon=None, item=None):
        threading.Thread(target=tv_mgr.close_tv, daemon=True).start()

    def action_soundbar(icon=None, item=None):
        c = load_config()
        sb = (c.get("soundbar_match") or "").strip()
        if sb:
            switch_audio_by_match(sb, volume=int(c.get("night_volume", 25)), unmute=True)
        else:
            action_open_qr()

    def action_headset(icon=None, item=None):
        c = load_config()
        hs = (c.get("headset_match") or "").strip()
        if hs:
            switch_audio_by_match(hs)
        else:
            action_open_qr()

    def action_toggle_autostart(icon=None, item=None):
        set_autostart_enabled(not get_autostart_enabled())

    def action_quit(icon=None, item=None):
        threading.Thread(target=graceful_shutdown, daemon=True).start()

    menu = Menu(
        MenuItem("📱 Parear Celular & Configurar (QR Code)", action_open_qr, default=True),
        MenuItem("🔗 Copiar Link Seguro (Cloud HTTPS)", action_copy_url),
        Menu.SEPARATOR,
        MenuItem("📺 Abrir YouTube TV + Soundbar", action_open_tv),
        MenuItem("⏹ Fechar TV + Voltar p/ Fone", action_close_tv),
        Menu.SEPARATOR,
        MenuItem("🔊 Forçar Áudio: Soundbar / TV", action_soundbar),
        MenuItem("🎧 Forçar Áudio: Fone / PC", action_headset),
        Menu.SEPARATOR,
        MenuItem(
            "🚀 Iniciar com o Windows",
            action_toggle_autostart,
            checked=lambda item: get_autostart_enabled(),
        ),
        MenuItem("❌ Sair do BedTube", action_quit),
    )

    icon = pystray.Icon(
        "BedTubeController",
        create_bedtube_icon(64, active=False),
        f"BedTube Controller v{APP_VERSION}",
        menu,
    )
    tray_ref["icon"] = icon

    # Abre o Hub automaticamente na primeira execução ou quando o usuário abre manualmente (fora do boot silencioso)
    should_open_hub = force_show_qr or (not is_background_boot and not cfg.get("first_run_completed", False))
    if should_open_hub:
        def _delayed_qr():
            for _ in range(16):
                time.sleep(0.35)
                if bridge.tunnel_url:
                    break
            action_open_qr()
        threading.Thread(target=_delayed_qr, daemon=True).start()

    icon.run()


if __name__ == "__main__":
    main()
