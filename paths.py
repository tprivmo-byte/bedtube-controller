import os
import sys
import winreg

APP_NAME = "BedTube Controller"
APP_VERSION = "0.0.2"
RUN_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_REG_VALUE = "BedTubeController"

IS_FROZEN = bool(getattr(sys, "frozen", False))

if IS_FROZEN:
    INSTALL_DIR = os.path.dirname(os.path.abspath(sys.executable))
    BUNDLE_DIR = getattr(sys, "_MEIPASS", INSTALL_DIR)
else:
    INSTALL_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = INSTALL_DIR

APPDATA_ROOT = os.environ.get("APPDATA") or os.path.expanduser("~")
LOCALAPPDATA_ROOT = os.environ.get("LOCALAPPDATA") or APPDATA_ROOT

USER_CONFIG_DIR = os.path.join(APPDATA_ROOT, "BedTubeController")
LOCAL_DATA_DIR = os.path.join(LOCALAPPDATA_ROOT, "BedTubeController")
COMTYPES_CACHE_DIR = os.path.join(LOCAL_DATA_DIR, "comtypes_gen")

os.makedirs(USER_CONFIG_DIR, exist_ok=True)
os.makedirs(LOCAL_DATA_DIR, exist_ok=True)
os.makedirs(COMTYPES_CACHE_DIR, exist_ok=True)

CONFIG_PATH = os.path.join(USER_CONFIG_DIR, "config.json")
LOG_PATH = os.path.join(LOCAL_DATA_DIR, "bedtube.log")


def find_resource(*parts: str) -> str:
    """Procura arquivos empacotados (static/, tv_shell/, bin/, runtime/) tanto no .exe quanto no código-fonte."""
    candidates = [
        os.path.join(INSTALL_DIR, *parts),
        os.path.join(BUNDLE_DIR, *parts),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


def get_launch_command(background: bool = True) -> str:
    if IS_FROZEN:
        exe = os.path.abspath(sys.executable)
        return f'"{exe}" --background' if background else f'"{exe}"'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = sys.executable
    script = os.path.join(INSTALL_DIR, "bedtube.pyw")
    return f'"{pyw}" "{script}" --background' if background else f'"{pyw}" "{script}"'


def _cleanup_legacy_startup_lnk() -> None:
    startup_lnk = os.path.join(
        APPDATA_ROOT,
        "Microsoft",
        "Windows",
        "Start Menu",
        "Programs",
        "Startup",
        "BedTube Controller.lnk",
    )
    if os.path.exists(startup_lnk):
        try:
            os.remove(startup_lnk)
        except OSError:
            pass


def get_autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_PATH, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, RUN_REG_VALUE)
            return bool(val)
    except OSError:
        startup_lnk = os.path.join(
            APPDATA_ROOT,
            "Microsoft",
            "Windows",
            "Start Menu",
            "Programs",
            "Startup",
            "BedTube Controller.lnk",
        )
        return os.path.exists(startup_lnk)


def set_autostart_enabled(enabled: bool) -> bool:
    try:
        _cleanup_legacy_startup_lnk()
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_REG_PATH) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_REG_VALUE, 0, winreg.REG_SZ, get_launch_command(background=True))
            else:
                try:
                    winreg.DeleteValue(key, RUN_REG_VALUE)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
