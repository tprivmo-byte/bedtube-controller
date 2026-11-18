import io
import ipaddress
import socket

import psutil
import qrcode
from PIL import Image, ImageDraw

VPN_KEYWORDS = (
    "radmin",
    "hamachi",
    "tailscale",
    "zerotier",
    "tun",
    "tap",
    "vpn",
    "loopback",
    "vethernet",
    "wsl",
    "hyper-v",
    "virtualbox",
    "vmware",
    "docker",
)


def get_lan_ip() -> str:
    """Descobre o IP doméstico real (192.168.x.x / 10.x.x.x / 172.x.x.x), ignorando VPNs e adaptadores virtuais."""
    candidates: list[tuple[int, str]] = []
    try:
        stats = psutil.net_if_stats()
        for iface, addrs in psutil.net_if_addrs().items():
            iface_low = iface.lower()
            if any(k in iface_low for k in VPN_KEYWORDS):
                continue
            st = stats.get(iface)
            if st and not st.isup:
                continue
            for snic in addrs:
                if snic.family == socket.AF_INET:
                    ip_str = snic.address
                    try:
                        ip_obj = ipaddress.ip_address(ip_str)
                    except ValueError:
                        continue
                    if ip_obj.is_loopback or ip_obj.is_link_local:
                        continue
                    if ip_str.startswith("192.168."):
                        candidates.append((1, ip_str))
                    elif ip_obj.is_private:
                        candidates.append((2, ip_str))
                    else:
                        candidates.append((3, ip_str))
        if candidates:
            candidates.sort(key=lambda x: x[0])
            return candidates[0][1]
    except Exception:
        pass

    try:
        # Consulta universal da tabela de rotas UDP (não envia pacotes para a rede)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def create_bedtube_icon(size: int = 256, active: bool = False) -> Image.Image:
    """Gera o ícone vetorial oficial do BedTube (TV OLED + botão Play + Soundbar) em memória."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = max(1, int(size * 0.06))
    radius = max(4, int(size * 0.24))
    bg_color = (14, 14, 21, 255)
    border_color = (16, 185, 129, 255) if active else (99, 102, 241, 255)
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=radius,
        fill=bg_color,
        outline=border_color,
        width=max(2, int(size * 0.045)),
    )

    tv_x1 = int(size * 0.18)
    tv_y1 = int(size * 0.23)
    tv_x2 = int(size * 0.82)
    tv_y2 = int(size * 0.67)
    tv_color = (91, 91, 240, 255)
    draw.rounded_rectangle(
        [tv_x1, tv_y1, tv_x2, tv_y2],
        radius=max(3, int(size * 0.085)),
        fill=tv_color,
    )

    cx = (tv_x1 + tv_x2) // 2
    cy = (tv_y1 + tv_y2) // 2
    tri_r = max(3, int(size * 0.105))
    draw.polygon(
        [
            (cx - int(tri_r * 0.62), cy - tri_r),
            (cx - int(tri_r * 0.62), cy + tri_r),
            (cx + tri_r, cy),
        ],
        fill=(250, 250, 252, 255),
    )

    sb_x1 = int(size * 0.24)
    sb_x2 = int(size * 0.76)
    sb_y1 = int(size * 0.75)
    sb_y2 = int(size * 0.82)
    sb_color = (52, 211, 153, 255) if active else (161, 161, 175, 255)
    draw.rounded_rectangle(
        [sb_x1, sb_y1, sb_x2, sb_y2],
        radius=max(2, int(size * 0.03)),
        fill=sb_color,
    )

    return img


def save_multi_size_ico(path: str) -> None:
    base = create_bedtube_icon(size=256, active=False)
    base.save(
        path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


def get_icon_png_bytes(size: int = 192) -> bytes:
    img = create_bedtube_icon(size=size, active=False)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def get_qr_png_bytes(url: str) -> bytes:
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#f4f4f8", back_color="#12121a")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
