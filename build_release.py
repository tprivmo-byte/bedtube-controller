import hashlib
import os
import shutil
import struct
import subprocess
import sys
import time
import zipfile

from icon_gen import save_multi_size_ico
from paths import APP_VERSION

ROOT = os.path.dirname(os.path.abspath(__file__))
CSC_EXE = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
WPF_DIR = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\WPF"


def run_checked(cmd: list[str], cwd: str = ROOT):
    print(">>", " ".join(cmd))
    res = subprocess.run(cmd, cwd=cwd)
    if res.returncode != 0:
        raise SystemExit(f"Falha ao executar comando (exit={res.returncode}): {cmd[0]}")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def build_all():
    t0 = time.time()
    assets_dir = os.path.join(ROOT, "assets")
    os.makedirs(assets_dir, exist_ok=True)
    ico_path = os.path.join(assets_dir, "bedtube.ico")
    print("[1/6] Gerando ícone multi-resolução bedtube.ico...")
    save_multi_size_ico(ico_path)

    inst_bin = os.path.join(ROOT, "installer", "bin")
    os.makedirs(inst_bin, exist_ok=True)
    uninstaller_exe = os.path.join(inst_bin, "Uninstall-BedTube.exe")
    setup_stub_exe = os.path.join(inst_bin, "SetupStub.exe")

    print("[2/6] Compilando Uninstaller & SetupStub nativos em C# WPF...")
    run_checked(
        [
            CSC_EXE,
            "/nologo",
            "/target:winexe",
            "/optimize+",
            f"/win32icon:{ico_path}",
            f"/r:{os.path.join(WPF_DIR, 'PresentationFramework.dll')}",
            f"/r:{os.path.join(WPF_DIR, 'PresentationCore.dll')}",
            f"/r:{os.path.join(WPF_DIR, 'WindowsBase.dll')}",
            "/r:System.Xaml.dll",
            f"/out:{uninstaller_exe}",
            os.path.join(ROOT, "installer", "Uninstaller.cs"),
        ]
    )
    run_checked(
        [
            CSC_EXE,
            "/nologo",
            "/target:winexe",
            "/optimize+",
            f"/win32icon:{ico_path}",
            f"/r:{os.path.join(WPF_DIR, 'PresentationFramework.dll')}",
            f"/r:{os.path.join(WPF_DIR, 'PresentationCore.dll')}",
            f"/r:{os.path.join(WPF_DIR, 'WindowsBase.dll')}",
            "/r:System.Xaml.dll",
            "/r:System.Windows.Forms.dll",
            "/r:System.IO.Compression.dll",
            f"/out:{setup_stub_exe}",
            os.path.join(ROOT, "installer", "Setup.cs"),
        ]
    )

    dist_root = os.path.join(ROOT, "dist")
    build_root = os.path.join(ROOT, "build")
    if os.path.exists(dist_root):
        shutil.rmtree(dist_root)
    if os.path.exists(build_root):
        shutil.rmtree(build_root)

    print("[3/6] Compilando BedTube.exe com PyInstaller...")
    run_checked(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--noconsole",
            "--name",
            "BedTube",
            "--icon",
            ico_path,
            "--contents-directory",
            "_internal",
            "--collect-submodules",
            "pystray",
            "--collect-submodules",
            "comtypes",
            "--collect-submodules",
            "pycaw",
            "--hidden-import",
            "paho.mqtt.client",
            "--hidden-import",
            "waitress",
            "--hidden-import",
            "flask",
            "--hidden-import",
            "qrcode",
            "--hidden-import",
            "PIL",
            "--exclude-module",
            "numpy",
            "--exclude-module",
            "comtypes.test",
            "--exclude-module",
            "tkinter",
            os.path.join(ROOT, "bedtube.pyw"),
        ]
    )

    app_dir = os.path.join(dist_root, "BedTube")
    print("[4/6] Montando pacote completo (tv_shell, static, cloudflared, Electron runtime, Uninstaller)...")
    shutil.copy2(uninstaller_exe, os.path.join(app_dir, "Uninstall-BedTube.exe"))
    shutil.copy2(os.path.join(ROOT, "LICENSE"), os.path.join(app_dir, "LICENSE"))
    shutil.copy2(os.path.join(ROOT, "README.md"), os.path.join(app_dir, "README.md"))
    shutil.copytree(os.path.join(ROOT, "static"), os.path.join(app_dir, "static"), dirs_exist_ok=True)
    shutil.copytree(os.path.join(ROOT, "tv_shell"), os.path.join(app_dir, "tv_shell"), dirs_exist_ok=True)

    cf_src = os.path.join(ROOT, "bin", "cloudflared.exe")
    if not os.path.exists(cf_src):
        raise SystemExit(f"cloudflared.exe não encontrado em {cf_src}")
    os.makedirs(os.path.join(app_dir, "bin"), exist_ok=True)
    shutil.copy2(cf_src, os.path.join(app_dir, "bin", "cloudflared.exe"))

    electron_src_candidates = [
        os.path.join(ROOT, "runtime", "electron"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "BedTube_Runtime", "electron"),
    ]
    electron_src = next((p for p in electron_src_candidates if os.path.exists(os.path.join(p, "electron.exe"))), None)
    if not electron_src:
        raise SystemExit("Runtime do Electron não encontrado!")
    shutil.copytree(electron_src, os.path.join(app_dir, "runtime", "electron"), dirs_exist_ok=True)

    # Auditoria de segurança: garante ausência de config.json ou .log no pacote distribuído
    for root_dir, _, files in os.walk(app_dir):
        for fn in files:
            low = fn.lower()
            if low == "config.json" or low.endswith(".log"):
                os.remove(os.path.join(root_dir, fn))

    release_dir = os.path.join(ROOT, "release_out")
    os.makedirs(release_dir, exist_ok=True)
    portable_zip = os.path.join(release_dir, f"BedTube-Portable-{APP_VERSION}-win64.zip")
    setup_exe = os.path.join(release_dir, f"BedTube-Setup-{APP_VERSION}.exe")

    print("[5/6] Compactando payload (ZIP Deflate)...")
    if os.path.exists(portable_zip):
        os.remove(portable_zip)
    with zipfile.ZipFile(
        portable_zip,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        strict_timestamps=False,
    ) as zf:
        for root_dir, _, files in os.walk(app_dir):
            for fn in files:
                abs_path = os.path.join(root_dir, fn)
                rel_path = os.path.relpath(abs_path, app_dir).replace("\\", "/")
                zf.write(abs_path, rel_path)

    print("[6/6] Gerando instalador auto-extraível BedTube-Setup-0.0.1.exe...")
    with open(setup_stub_exe, "rb") as f_stub:
        stub_bytes = f_stub.read()
    zip_offset = len(stub_bytes)

    with open(setup_exe, "wb") as f_out:
        f_out.write(stub_bytes)
        with open(portable_zip, "rb") as f_zip:
            shutil.copyfileobj(f_zip, f_out, length=1024 * 1024)
        footer = struct.pack("<Q8s", zip_offset, b"BTSETUP1")
        f_out.write(footer)

    sums_path = os.path.join(release_dir, "SHA256SUMS.txt")
    setup_hash = sha256_file(setup_exe)
    zip_hash = sha256_file(portable_zip)
    with open(sums_path, "w", encoding="utf-8") as f_sum:
        f_sum.write(f"{setup_hash}  {os.path.basename(setup_exe)}\n")
        f_sum.write(f"{zip_hash}  {os.path.basename(portable_zip)}\n")

    dt = time.time() - t0
    print(f"\n=== BUILD CONCLUÍDO EM {dt:.1f}s ===")
    print(f"Instalador: {setup_exe} ({os.path.getsize(setup_exe) / (1024*1024):.1f} MB)")
    print(f"Portátil  : {portable_zip} ({os.path.getsize(portable_zip) / (1024*1024):.1f} MB)")
    print(f"SHA256    : {sums_path}")


if __name__ == "__main__":
    build_all()
