import socket

from audio_manager import (
    change_volume,
    get_audio_state,
    list_playback_devices,
    set_volume,
    suggest_audio_roles,
    switch_audio_by_match,
    toggle_mute,
)
from config_store import load_config, save_config
from paths import APP_VERSION, get_autostart_enabled, set_autostart_enabled


def build_status_payload(tv_mgr, include_devices: bool = False) -> dict:
    cfg = load_config()
    audio = get_audio_state()
    sb_match = (cfg.get("soundbar_match") or "").strip().lower()
    hs_match = (cfg.get("headset_match") or "").strip().lower()
    dev_name_low = audio.get("device_name", "").lower()
    dev_id_low = audio.get("device_id", "").lower()

    if sb_match and (sb_match == dev_id_low or sb_match in dev_name_low):
        active_profile = "soundbar"
    elif hs_match and (hs_match == dev_id_low or hs_match in dev_name_low):
        active_profile = "headset"
    else:
        active_profile = "other"

    payload = {
        "ok": True,
        "version": APP_VERSION,
        "hostname": socket.gethostname(),
        "tv_open": tv_mgr.is_tv_running(),
        "screen_off": bool(getattr(tv_mgr, "screen_off", False)),
        "preferred_quality": cfg.get("preferred_quality", "1080"),
        "device_name": audio.get("device_name", "Desconhecido"),
        "volume": audio.get("volume", 0),
        "muted": audio.get("muted", False),
        "active_profile": active_profile,
        "sleep_remaining_min": tv_mgr.get_sleep_remaining_min(),
        "pc_shutdown_remaining_sec": tv_mgr.get_pc_shutdown_remaining_sec(),
        "soundbar_match": cfg.get("soundbar_match", ""),
        "headset_match": cfg.get("headset_match", ""),
        "night_volume": int(cfg.get("night_volume", 25)),
        "language": cfg.get("language", "auto"),
        "audio_configured": bool(sb_match or hs_match),
        "autostart": get_autostart_enabled(),
    }
    if include_devices:
        devs = list_playback_devices()
        payload["devices"] = devs
        payload["suggested_roles"] = suggest_audio_roles(devs)
    return payload


def execute_bedtube_action(tv_mgr, cmd: str, params: dict | None = None) -> dict:
    params = params or {}
    cfg = load_config()
    cmd = (cmd or "").strip().lower()

    if cmd == "iniciar":
        res = tv_mgr.open_tv()
    elif cmd == "fechar":
        off_screen = bool(params.get("off", False))
        res = tv_mgr.close_tv(turn_off_screen=off_screen)
    elif cmd == "fechar_apagar":
        res = tv_mgr.close_tv(turn_off_screen=True)
    elif cmd.startswith("tecla:"):
        acao = cmd.split(":", 1)[1]
        ok = tv_mgr.send_tv_key(acao)
        res = {
            "ok": ok,
            "action": acao,
            "message": "Comando enviado" if ok else "TV fechada ou tecla inválida",
        }
    elif cmd == "tv_search":
        query = str(params.get("query", "")).strip()
        ok = tv_mgr.search_tv_text(query)
        res = {
            "ok": ok,
            "message": f"Busca enviada: {query}" if ok else "Abra a TV primeiro para buscar",
        }
    elif cmd == "vol_up":
        change_volume(+5)
        res = {"ok": True}
    elif cmd == "vol_down":
        change_volume(-5)
        res = {"ok": True}
    elif cmd == "vol_set":
        nivel = int(params.get("nivel", 25))
        set_volume(nivel)
        res = {"ok": True}
    elif cmd == "mute":
        toggle_mute()
        res = {"ok": True}
    elif cmd == "audio_soundbar":
        sb = (cfg.get("soundbar_match") or "").strip()
        if not sb:
            res = {"ok": False, "message": "Configure a saída da TV/Soundbar nas Configurações ⚙️"}
        else:
            ok, msg = switch_audio_by_match(sb, volume=None, unmute=True, retries=2)
            res = {"ok": ok, "message": f"Áudio: {msg}"}
    elif cmd == "audio_headset":
        hs = (cfg.get("headset_match") or "").strip()
        if not hs:
            res = {"ok": False, "message": "Configure a saída do Fone/PC nas Configurações ⚙️"}
        else:
            ok, msg = switch_audio_by_match(hs, volume=None, unmute=False, retries=2)
            res = {"ok": ok, "message": f"Áudio: {msg}"}
    elif cmd == "monitor_wake":
        tv_mgr.wake_monitor()
        res = {"ok": True, "message": "Tela do PC ligada ☀️"}
    elif cmd == "monitor_off":
        tv_mgr.turn_off_monitor()
        res = {"ok": True, "message": "Apagando tela do PC..."}
    elif cmd == "monitor_toggle":
        if getattr(tv_mgr, "screen_off", False):
            tv_mgr.wake_monitor()
            res = {"ok": True, "message": "Tela do PC ligada ☀️"}
        else:
            tv_mgr.turn_off_monitor()
            res = {"ok": True, "message": "Apagando tela do PC..."}
    elif cmd == "sleep":
        minutos = int(params.get("minutos", 0))
        rem = tv_mgr.set_sleep_timer(minutos)
        msg = f"Sleep Timer: {rem} min" if rem else "Sleep Timer desativado"
        res = {"ok": True, "sleep_remaining_min": rem, "message": msg}
    elif cmd == "pc_shutdown":
        mode = str(params.get("mode", "timer")).strip().lower()
        minutes = int(params.get("minutes", params.get("minutos", 0)) or 0)
        res = tv_mgr.schedule_pc_shutdown(mode=mode, minutes=minutes)
    elif cmd == "pc_shutdown_cancel":
        res = tv_mgr.schedule_pc_shutdown(mode="cancel", minutes=0)
    elif cmd.startswith("qualidade:"):
        pref = cmd.split(":", 1)[1]
        qres = tv_mgr.set_tv_quality(pref)
        label_map = {
            "1080": "1080p Full HD",
            "max": "Máxima (4K / 1440p / 1080p)",
            "720": "720p HD",
            "auto": "Automática",
        }
        lbl = label_map.get(qres["preferred_quality"], qres["preferred_quality"])
        msg = (
            f"Qualidade travada em {lbl}"
            if qres.get("applied_live")
            else f"Qualidade padrão salva: {lbl}"
        )
        res = {**qres, "message": msg}
    elif cmd == "list_devices":
        return build_status_payload(tv_mgr, include_devices=True)
    elif cmd == "save_settings":
        updates = {}
        if "soundbar_match" in params:
            updates["soundbar_match"] = str(params["soundbar_match"]).strip()
        if "headset_match" in params:
            updates["headset_match"] = str(params["headset_match"]).strip()
        if "night_volume" in params:
            updates["night_volume"] = max(0, min(100, int(params["night_volume"])))
        if "preferred_quality" in params:
            updates["preferred_quality"] = str(params["preferred_quality"]).strip()
        if "language" in params:
            updates["language"] = str(params["language"]).strip()
        updates["first_run_completed"] = True
        save_config(updates)
        if "autostart" in params:
            set_autostart_enabled(bool(params["autostart"]))
        res = {"ok": True, "message": "Configurações salvas!"}
    else:
        return {"ok": False, "message": f"Comando desconhecido: {cmd}"}

    status = build_status_payload(tv_mgr)
    status.update(res)
    return status
