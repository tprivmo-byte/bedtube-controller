<div align="center">

<img src="assets/banner.svg" alt="BedTube Controller — YouTube TV 1080p60/4K60 on Windows PC with Smartphone Remote & Automatic Audio Switcher" width="100%" />

<br/>

[![Download Setup EXE](https://img.shields.io/badge/%E2%AC%87%20Download%20Installer-BedTube--Setup--0.0.1.exe-e11d48?style=for-the-badge)](https://github.com/tprivmo-byte/bedtube-controller/releases/download/v0.0.1/BedTube-Setup-0.0.1.exe)
[![Download Portable ZIP](https://img.shields.io/badge/%F0%9F%93%A6%20Portable%20ZIP-v0.0.1%20(Win64)-2563eb?style=for-the-badge)](https://github.com/tprivmo-byte/bedtube-controller/releases/download/v0.0.1/BedTube-Portable-0.0.1-win64.zip)
[![License: MIT](https://img.shields.io/badge/License-MIT-10b981?style=for-the-badge)](LICENSE)

**Turn your Windows PC into a 1080p60 / 4K60 Smart TV controlled from bed with your smartphone — with automatic Headset ↔ Soundbar/TV audio switching.**

**Transforme seu PC Windows em uma Smart TV 1080p60 / 4K60 controlada da cama pelo celular — com troca automática de áudio entre Fone e Soundbar/TV.**

**Convierte tu PC Windows en un Smart TV 1080p60 / 4K60 controlado desde la cama con tu móvil — con cambio automático de audio entre Auriculares y Barra de Sonido/TV.**

[**🇺🇸 English**](#-english) • [**🇧🇷 Português**](#-português) • [**🇪🇸 Español**](#-español) • [**🛠️ Build from Source**](#%EF%B8%8F-build-from-source-developers)

</div>

---

## 🇺🇸 English

### Why BedTube Controller?
If you use **headphones/headset** at your desk during the day, watching YouTube on your PC monitor or HDMI TV **from bed or the couch** is annoying: you have to get up to switch Windows audio outputs, lower loud desktop volume, grab a mouse, and deal with YouTube's tiny desktop UI (or the 720p cap when opening `youtube.com/tv` in Chrome/Edge).

**BedTube Controller** solves the entire bedroom/couch HTPC workflow in **one tap from your smartphone**:
1. **Wakes your PC screen** and opens **YouTube TV (Leanback Smart TV interface)** in an isolated fullscreen engine — **zero browser extension popups, zero desktop redirects, and unlocked 1080p60 / 1440p60 / 4K60 HDR-ready playback**.
2. **Automatically switches Windows default audio (`<5ms`)** from your **Headset/Headphones** to your **Soundbar / Speakers / HDMI TV** at a safe **Night Volume** (e.g., `25%`).
3. Gives your smartphone an installable **OLED Touch Remote (PWA)** with directional D-Pad, instant text or YouTube URL search, live `1080p / 4K / 720p / Auto` quality switcher, volume slider, **Turn Off PC Monitor**, and **Bedtime Sleep Timer** (`30 / 60 / 90 min`).
4. **Watchdog Auto-Restore:** The instant YouTube TV closes (from your phone, `Alt+F4`, gamepad, or Sleep Timer), Windows audio **automatically reverts to your Headset** so audio never blasts into your room the next morning.

### ✨ Highlights (`v0.0.1`)
- **🎬 Unlocked 1080p60 & 4K60 YouTube TV on Windows:** Isolated Electron Smart TV shell with Cobalt 27 / Starboard MSE profile and GPU hardware video decoding.
- **🔊 Instant Native CoreAudio Switcher (`<5ms`):** Direct Windows `IPolicyConfig` COM + `pycaw` endpoint switching across Multimedia, Console, and Communications roles — zero PowerShell popups or virtual audio drivers.
- **☁️ Works When PC is on Wired Ethernet & Phone is on Wi-Fi:** Built-in **Cloudflare HTTPS Quick Tunnel** + **HMAC-SHA256 Encrypted MQTT Relay**. Automatically bypasses router AP/LAN isolation and Android 15/16/17 Local Network blocks with zero port-forwarding.
- **📲 Reboot-Proof Mobile PWA:** Add to your iPhone or Android Home Screen once — stays connected via encrypted MQTT discovery even after PC reboots.
- **🎮 Full Gamepad Navigation (60fps):** Works out of the box with Xbox, DualSense/DualShock, and XInput controllers (`D-Pad`/Stick, `A` = Select, `B` = Back, Hold `B` = Exit TV, `X` = Search).
- **🪄 Custom Dark-Mode Installer (`BedTube-Setup-0.0.1.exe`):** Fast native WPF installer & uninstaller in English, Portuguese, and Spanish — installs cleanly in user space with no Admin/UAC prompts required.

### 🚀 Quick Start (Windows 10 / 11 x64)
1. Download **[`BedTube-Setup-0.0.1.exe`](https://github.com/tprivmo-byte/bedtube-controller/releases/download/v0.0.1/BedTube-Setup-0.0.1.exe)** (or the portable `.zip` from [Releases](https://github.com/tprivmo-byte/bedtube-controller/releases/tag/v0.0.1)).
2. Run the setup wizard and click **Install**.
3. In the **BedTube Pairing Hub** that opens automatically, choose your **TV / Soundbar** audio device and your **Headset / Desk** audio device, then scan the **QR Code** with your phone camera!

---

## 🇧🇷 Português

### Por que usar o BedTube Controller?
Quem usa **fone de ouvido (headset)** no computador sabe o incômodo que é deitar na cama para assistir YouTube no monitor ou na TV HDMI: levantar para trocar a saída de som do Windows, abaixar o volume, usar mouse de longe ou sofrer com o limite de **720p** e janelas de extensões ao abrir `youtube.com/tv` no navegador comum.

O **BedTube Controller** resolve tudo com **1 toque no celular**:
1. **Acorda o monitor** e abre o **YouTube TV (interface oficial de Smart TV)** em tela cheia totalmente isolada — **sem abrir abas de extensões e com qualidade destravada em 1080p60 / 1440p60 / 4K60**.
2. **Troca automaticamente o som do Windows em `<5ms`** do seu **Fone/Headset** para a **Soundbar / Caixa de Som / TV HDMI** já ajustada no **Volume Noturno** ideal (ex: `25%`).
3. Transforma seu celular em um **Controle Remoto OLED instalável (PWA)** com D-Pad direcional, busca por texto ou link do YouTube, seletor de qualidade (`1080p`, `Máx/4K`, `720p`, `Auto`), controle de volume, botão **Apagar Tela do PC** e **Sleep Timer para dormir** (`30 / 60 / 90 min`).
4. **Watchdog Inteligente:** Fechou a TV pelo celular, pelo joystick, no Sleep Timer ou no `Alt+F4`? O áudio do Windows **volta sozinho para o Fone de Ouvido** na hora.

### ✨ Diferenciais da Versão `v0.0.1`
- **☁️ Funciona com PC no Cabo de Rede + Celular no Wi-Fi (Zero Configuração):** Acompanha **Túnel HTTPS Seguro (`cloudflared`) + Relay MQTT Criptografado (HMAC-SHA256)**, contornando isolamento de roteador e bloqueios de rede local do Android 15/16/17 sem precisar abrir portas.
- **📲 Web App (PWA) à Prova de Reinicialização:** Adicione à tela inicial do Android ou iPhone uma única vez e continue controlando o PC mesmo após reiniciar o Windows.
- **🎮 Suporte Nativo a Controle (Xbox / PlayStation / Genérico):** Navegue deitado também pelo joystick a 60fps (`A` = OK, `B` = Voltar, segurar `B` = Sair, `X` = Busca).
- **🪄 Instalador Simples com Interface Personalizada (`BedTube-Setup-0.0.1.exe`):** Instalador e desinstalador nativos em modo escuro (Português, Inglês e Espanhol), sem exigir permissão de Administrador.

### 🚀 Instalação Rápida (Windows 10 / 11 x64)
1. Baixe o **[`BedTube-Setup-0.0.1.exe`](https://github.com/tprivmo-byte/bedtube-controller/releases/download/v0.0.1/BedTube-Setup-0.0.1.exe)** (ou a versão portátil `.zip` em [Releases](https://github.com/tprivmo-byte/bedtube-controller/releases/tag/v0.0.1)).
2. Execute o instalador e clique em **Instalar BedTube Controller**.
3. Na tela de pareamento que abrirá automaticamente, escolha sua **Soundbar / Caixa de Som / TV** e seu **Fone de Ouvido**, salve e escaneie o **QR Code** com a câmera do celular!

---

## 🇪🇸 Español

### ¿Por qué usar BedTube Controller?
Si usas **auriculares (headset)** en el escritorio, ver YouTube en el monitor de tu PC o TV HDMI **desde la cama o el sofá** suele ser incómodo: levantarse para cambiar la salida de audio de Windows, bajar el volumen, usar el ratón a distancia o quedarse limitado a **720p** en `youtube.com/tv` desde el navegador.

**BedTube Controller** automatiza todo con **un solo toque desde tu móvil**:
1. **Enciende el monitor** y lanza **YouTube TV (interfaz Leanback de Smart TV)** a pantalla completa en un motor aislado — **sin ventanas emergentes de extensiones y con resolución desbloqueada en 1080p60 / 1440p60 / 4K60**.
2. **Cambia automáticamente la salida de audio de Windows en `<5ms`** de tus **Auriculares/Headset** a tu **Barra de Sonido / Altavoces / TV HDMI** con un **Volumen Nocturno** cómodo (ej. `25%`).
3. Convierte tu smartphone en un **Control Remoto OLED instalable (PWA)** con cruceta direccional (D-Pad), búsqueda rápida por texto o enlace de YouTube, selector de calidad (`1080p / 4K`), control de volumen, botón para **Apagar la Pantalla del PC** y **Temporizador de Sueño** (`30 / 60 / 90 min`).
4. **Restauración Automática (Watchdog):** En cuanto cierras YouTube TV, el audio de Windows **vuelve automáticamente a tus auriculares**.

### ✨ Características Principales (`v0.0.1`)
- **☁️ Funciona con PC por Cable Ethernet + Móvil por Wi-Fi:** Incluye **Túnel HTTPS Seguro + Relay MQTT cifrado (HMAC-SHA256)** para evitar bloqueos de aislamiento del router o de red local en Android sin abrir puertos.
- **📲 App Móvil PWA Persistente:** Agrégala a la pantalla de inicio en Android o iOS una sola vez; sigue funcionando incluso tras reiniciar tu PC.
- **🎮 Soporte Completo para Mandos (Xbox / PlayStation):** Navegación fluida a 60fps desde la cama con joystick.
- **🪄 Instalador Personalizado (`BedTube-Setup-0.0.1.exe`):** Interfaz oscura moderna en Español, Inglés y Portugués, con desinstalador integrado y sin requerir permisos de Administrador.

### 🚀 Instalación Rápida (Windows 10 / 11 x64)
1. Descarga **[`BedTube-Setup-0.0.1.exe`](https://github.com/tprivmo-byte/bedtube-controller/releases/download/v0.0.1/BedTube-Setup-0.0.1.exe)** (o el archivo portátil `.zip` en [Releases](https://github.com/tprivmo-byte/bedtube-controller/releases/tag/v0.0.1)).
2. Abre el instalador y haz clic en **Instalar**.
3. Selecciona tu dispositivo de **Barra de Sonido / Altavoces / TV** y tus **Auriculares**, ¡y escanea el **Código QR** con la cámara de tu celular!

---

## 🛠️ Build from Source (Developers)

```powershell
# 1. Install Python dependencies (Windows 10/11, Python 3.10+)
pip install -r requirements.txt

# 2. Run directly from source and open the Pairing Hub
python bedtube.pyw --show-qr

# 3. Build standalone BedTube.exe + Portable ZIP + Custom WPF Installer (BedTube-Setup-0.0.1.exe)
#    (Place cloudflared.exe in .\bin\cloudflared.exe and Electron runtime in .\runtime\electron\)
python build_release.py
```

### Architecture Overview
| Module | Responsibility |
| :--- | :--- |
| `bedtube.pyw` | System Tray daemon (`pystray`), single-instance mutex, High-DPI awareness & waitress HTTP host |
| `audio_manager.py` | Native Windows CoreAudio `IPolicyConfig` COM endpoint switching (`<5ms`) + `pycaw` volume/mute |
| `tv_manager.py` | Electron Smart TV lifecycle, Watchdog audio restoration, Win32 monitor wake/off & Sleep Timer |
| `tv_shell/` | Isolated YouTube TV Leanback shell with Cobalt 27 `1080p60/4K60` MSE unlocker & 60fps Gamepad engine |
| `cloud_bridge.py` | Zero-config `cloudflared` HTTPS tunnel + Win32 Job Object + HMAC-SHA256 MQTT TLS Relay |
| `web_app.py` | Trilingual (`EN/PT/ES`) Mobile OLED PWA Remote + Desktop Pairing & Audio Configuration Hub |
| `installer/` | Native C# WPF Custom-UI Self-Extracting Installer (`Setup.cs`) & Uninstaller (`Uninstaller.cs`) |

---

## 🔎 Search Keywords / Palavras-chave / Palabras Clave

- **English:** youtube tv on pc, youtube leanback windows 11, youtube tv 1080p 60fps 4k desktop, unlock 1080p youtube tv browser, control pc youtube from phone, smartphone remote control for windows pc, watch youtube from bed on pc, couch htpc remote pwa, automatic audio output switcher windows, switch headphones to speakers automatically, headset to soundbar switcher, turn off pc monitor from phone, windows sleep timer remote app, youtube tv gamepad controller windows.
- **Português:** youtube tv no pc, youtube de smart tv no windows 10 11, youtube tv 1080p 60fps 4k pc, destravar 1080p youtube tv navegador, controlar youtube do pc pelo celular, controle remoto para pc pelo celular wifi, assistir youtube deitado na cama no pc, transformar pc em smart tv, trocar saida de audio automaticamente windows, alternar fone de ouvido e caixa de som sozinho, trocar headset para soundbar automatico, apagar desligar tela do monitor pelo celular, sleep timer desligar tela pc, youtube tv controle xbox playstation pc.
- **Español:** youtube tv en pc, youtube de smart tv en windows 10 11, youtube tv 1080p 60fps 4k escritorio, desbloquear 1080p youtube tv pc, controlar youtube de pc desde el celular movil, control remoto mando a distancia para pc desde el telefono, ver youtube desde la cama en pc, convertir pc en smart tv, cambiar salida de audio automaticamente windows, alternar auriculares y altavoces automaticamente, cambiar headset a barra de sonido, apagar pantalla del monitor desde el movil, temporizador de apagado sueño pc, mando xbox playstation youtube tv pc.

## 📄 License
Distributed under the [MIT License](LICENSE).
