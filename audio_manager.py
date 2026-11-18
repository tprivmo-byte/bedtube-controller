import functools
import logging
import threading
import time
import warnings
from ctypes.wintypes import DWORD, LPCWSTR

import comtypes
import comtypes.client

from paths import COMTYPES_CACHE_DIR

# Crucial para builds PyInstaller (.exe): evita gravar wrappers COM em pasta somente-leitura
comtypes.client.gen_dir = COMTYPES_CACHE_DIR

from comtypes import COMMETHOD, GUID, HRESULT  # noqa: E402
from pycaw.pycaw import AudioUtilities  # noqa: E402

warnings.filterwarnings("ignore", category=UserWarning, module="pycaw")

logger = logging.getLogger("bedtube.audio")
_audio_lock = threading.RLock()

TV_AUDIO_HINTS = (
    "soundbar",
    "tv",
    "hdmi",
    "nvidia high definition",
    "amd high definition",
    "intel(r) tela",
    "display audio",
    "lg ",
    "samsung",
    "sony",
    "tcl",
    "philips",
    "hisense",
    "roku",
    "home theater",
    "receiver",
    "digital output",
    "spdif",
    "caixa",
)

HEADSET_AUDIO_HINTS = (
    "headset",
    "headphone",
    "fone",
    "earphone",
    "hyperx",
    "logitech",
    "razer",
    "corsair",
    "steelseries",
    "astro",
    "sennheiser",
    "jbl",
    "edifier",
    "airpods",
    "buds",
    "fifine",
)


class IPolicyConfig(comtypes.IUnknown):
    """Interface COM nativa do Windows (CoreAudio) para alternar saída padrão em <5ms."""

    _case_insensitive_ = True
    _iid_ = GUID("{f8679f50-850a-41cf-9c72-430f290290c8}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetMixFormat"),
        COMMETHOD([], HRESULT, "GetDeviceFormat"),
        COMMETHOD([], HRESULT, "ResetDeviceFormat"),
        COMMETHOD([], HRESULT, "SetDeviceFormat"),
        COMMETHOD([], HRESULT, "GetProcessingPeriod"),
        COMMETHOD([], HRESULT, "SetProcessingPeriod"),
        COMMETHOD([], HRESULT, "GetShareMode"),
        COMMETHOD([], HRESULT, "SetShareMode"),
        COMMETHOD([], HRESULT, "GetPropertyValue"),
        COMMETHOD([], HRESULT, "SetPropertyValue"),
        COMMETHOD(
            [],
            HRESULT,
            "SetDefaultEndpoint",
            (["in"], LPCWSTR, "wszDeviceId"),
            (["in"], DWORD, "eRole"),
        ),
        COMMETHOD([], HRESULT, "SetEndpointVisibility"),
    ]


CLSID_PolicyConfigClient = GUID("{870af99c-171d-4f9e-af0d-e63df40c2bc9}")


def with_com(func):
    """Garante CoInitialize seguro em qualquer thread (Flask, MQTT, Watchdog ou System Tray)."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _audio_lock:
            comtypes.CoInitialize()
            try:
                return func(*args, **kwargs)
            finally:
                try:
                    comtypes.CoUninitialize()
                except Exception:
                    pass

    return wrapper


@with_com
def list_playback_devices() -> list[dict]:
    """Lista todos os dispositivos de reprodução (Playback) ativos no Windows."""
    default_id = None
    try:
        spk = AudioUtilities.GetSpeakers()
        default_id = getattr(spk, "id", None)
    except Exception as exc:
        logger.debug("Aviso ao obter speaker padrão: %s", exc)

    devices: list[dict] = []
    try:
        all_devs = AudioUtilities.GetAllDevices()
    except Exception as exc:
        logger.error("Erro ao enumerar dispositivos de áudio: %s", exc)
        return devices

    for dev in all_devs:
        dev_id = getattr(dev, "id", None)
        state = str(getattr(dev, "state", ""))
        if dev_id and dev_id.startswith("{0.0.0.00000000}.") and "Active" in state:
            name = getattr(dev, "FriendlyName", None) or "Saída de Áudio"
            devices.append(
                {
                    "id": dev_id,
                    "name": name,
                    "is_default": dev_id == default_id,
                }
            )
    return devices


def suggest_audio_roles(devices: list[dict] | None = None) -> dict[str, str]:
    """Sugere automaticamente qual saída parece ser TV/Soundbar e qual parece ser Fone/PC."""
    devs = devices if devices is not None else list_playback_devices()
    if not devs:
        return {"soundbar_match": "", "headset_match": ""}

    tv_pick = ""
    hs_pick = ""

    for d in devs:
        low = d["name"].lower()
        if not tv_pick and any(k in low for k in TV_AUDIO_HINTS):
            tv_pick = d["name"]
        if not hs_pick and any(k in low for k in HEADSET_AUDIO_HINTS):
            hs_pick = d["name"]

    default_dev = next((d["name"] for d in devs if d.get("is_default")), devs[0]["name"])
    if not hs_pick:
        hs_pick = default_dev

    if not tv_pick:
        other = next((d["name"] for d in devs if d["name"] != hs_pick), "")
        tv_pick = other or hs_pick

    return {"soundbar_match": tv_pick, "headset_match": hs_pick}


@with_com
def get_audio_state() -> dict:
    """Retorna dispositivo atual, volume (%) e estado de mudo sem falhar se não houver saída ativa."""
    try:
        spk = AudioUtilities.GetSpeakers()
        if not spk:
            raise RuntimeError("Nenhuma saída de áudio ativa")
        name = getattr(spk, "FriendlyName", "Desconhecido")
        dev_id = getattr(spk, "id", "")
        ev = spk.EndpointVolume
        volume = int(round(ev.GetMasterVolumeLevelScalar() * 100))
        muted = bool(ev.GetMute())
        return {
            "ok": True,
            "device_id": dev_id,
            "device_name": name,
            "volume": volume,
            "muted": muted,
        }
    except Exception as exc:
        logger.debug("Estado de áudio indisponível: %s", exc)
        return {
            "ok": False,
            "device_id": "",
            "device_name": "Sem saída ativa",
            "volume": 0,
            "muted": False,
        }


def _set_default_endpoint_id(device_id: str) -> None:
    policy_config = comtypes.CoCreateInstance(
        CLSID_PolicyConfigClient,
        IPolicyConfig,
        comtypes.CLSCTX_ALL,
    )
    for role in (0, 1, 2):
        policy_config.SetDefaultEndpoint(device_id, role)


@with_com
def switch_audio_by_match(
    query: str,
    volume: int | None = None,
    unmute: bool = False,
    retries: int = 1,
) -> tuple[bool, str]:
    """Procura dispositivo ativo por trecho do nome ou ID exato (com retry p/ monitores HDMI acordando)."""
    if not query or not query.strip():
        cur = get_audio_state()
        if volume is not None:
            set_volume(volume)
        return True, cur.get("device_name", "Saída Atual")

    q_lower = query.lower().strip()
    target = None

    attempts = max(1, int(retries))
    for attempt in range(attempts):
        for dev in list_playback_devices():
            if dev["id"].lower() == q_lower or q_lower in dev["name"].lower():
                target = dev
                break
        if target or attempt == attempts - 1:
            break
        time.sleep(0.45)

    if not target:
        logger.warning("Dispositivo não encontrado para filtro: %s", query)
        return False, f"Saída '{query}' não conectada"

    try:
        _set_default_endpoint_id(target["id"])
        if volume is not None or unmute:
            spk = AudioUtilities.GetSpeakers()
            ev = spk.EndpointVolume
            if unmute:
                ev.SetMute(0, None)
            if volume is not None:
                clamped = max(0, min(100, int(volume)))
                ev.SetMasterVolumeLevelScalar(clamped / 100.0, None)
        logger.info("Áudio alterado para: %s (vol=%s)", target["name"], volume)
        return True, target["name"]
    except Exception as exc:
        logger.error("Erro ao trocar áudio para %s: %s", target["name"], exc)
        return False, f"Erro ao ativar {target['name']}: {exc}"


@with_com
def set_volume(target_volume: int) -> int:
    """Define o volume mestre exato (0 a 100) do dispositivo padrão atual."""
    clamped = max(0, min(100, int(target_volume)))
    try:
        spk = AudioUtilities.GetSpeakers()
        ev = spk.EndpointVolume
        if clamped > 0 and bool(ev.GetMute()):
            ev.SetMute(0, None)
        ev.SetMasterVolumeLevelScalar(clamped / 100.0, None)
    except Exception as exc:
        logger.warning("Aviso ao ajustar volume: %s", exc)
    return clamped


@with_com
def change_volume(delta: int) -> int:
    """Ajusta o volume atual somando delta (ex: +5 ou -5)."""
    try:
        spk = AudioUtilities.GetSpeakers()
        ev = spk.EndpointVolume
        current = int(round(ev.GetMasterVolumeLevelScalar() * 100))
        new_vol = max(0, min(100, current + int(delta)))
        if delta > 0 and bool(ev.GetMute()):
            ev.SetMute(0, None)
        ev.SetMasterVolumeLevelScalar(new_vol / 100.0, None)
        return new_vol
    except Exception as exc:
        logger.warning("Aviso ao alterar delta de volume: %s", exc)
        return 0


@with_com
def toggle_mute() -> bool:
    """Alterna o estado de Mudo e retorna o novo estado (True = mutado)."""
    try:
        spk = AudioUtilities.GetSpeakers()
        ev = spk.EndpointVolume
        new_mute = not bool(ev.GetMute())
        ev.SetMute(1 if new_mute else 0, None)
        return new_mute
    except Exception as exc:
        logger.warning("Aviso ao alternar mudo: %s", exc)
        return False
