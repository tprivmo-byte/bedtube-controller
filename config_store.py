import json
import logging
import os
import secrets
import shutil
import threading

from paths import APP_VERSION, CONFIG_PATH, INSTALL_DIR, IS_FROZEN, LOCALAPPDATA_ROOT

_lock = threading.RLock()
logger = logging.getLogger("bedtube.config")

DEFAULT_CONFIG = {
    "version": APP_VERSION,
    "soundbar_match": "",
    "headset_match": "",
    "night_volume": 25,
    "port": 5050,
    "tv_control_port": 5051,
    "enable_cloud_tunnel": True,
    "enable_mqtt_relay": True,
    "access_token": "",
    "relay_topic_id": "",
    "electron_path": "",
    "tv_data_dir": os.path.join(LOCALAPPDATA_ROOT, "BedTube_TV_Data"),
    "tv_url": "https://www.youtube.com/tv",
    "preferred_quality": "1080",
    "language": "auto",
    "first_run_completed": False,
}


def _migrate_legacy_config_if_needed() -> None:
    if os.path.exists(CONFIG_PATH) or IS_FROZEN:
        return
    legacy_path = os.path.join(INSTALL_DIR, "config.json")
    if os.path.exists(legacy_path):
        try:
            shutil.copy2(legacy_path, CONFIG_PATH)
        except Exception as exc:
            logger.warning("Aviso ao migrar config legado: %s", exc)


def _atomic_write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


def load_config() -> dict:
    with _lock:
        _migrate_legacy_config_if_needed()
        cfg = dict(DEFAULT_CONFIG)
        needs_save = False

        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    cfg.update(loaded)
            except Exception as exc:
                logger.error("Erro ao ler config.json: %s", exc)
        else:
            needs_save = True

        if cfg.get("version") != APP_VERSION:
            cfg["version"] = APP_VERSION
            needs_save = True

        if not cfg.get("access_token"):
            cfg["access_token"] = secrets.token_urlsafe(24)
            needs_save = True

        if not cfg.get("relay_topic_id"):
            cfg["relay_topic_id"] = "bt_" + secrets.token_hex(14)
            needs_save = True

        # Limpa caminhos legados inexistentes do Electron em outros PCs
        ep = cfg.get("electron_path") or ""
        if ep and not os.path.exists(ep):
            cfg["electron_path"] = ""
            needs_save = True

        if needs_save:
            try:
                _atomic_write_json(CONFIG_PATH, cfg)
            except Exception as exc:
                logger.error("Erro ao salvar config.json: %s", exc)

        return cfg


def save_config(updates: dict) -> dict:
    with _lock:
        cfg = load_config()
        cfg.update(updates)
        try:
            _atomic_write_json(CONFIG_PATH, cfg)
        except Exception as exc:
            logger.error("Erro ao gravar atualizações no config.json: %s", exc)
        return cfg


def regenerate_pairing_credentials() -> dict:
    return save_config(
        {
            "access_token": secrets.token_urlsafe(24),
            "relay_topic_id": "bt_" + secrets.token_hex(14),
        }
    )
