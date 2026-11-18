import hmac
import json
import logging
import os
import socket
import threading

from flask import Flask, Response, jsonify, make_response, request, send_from_directory

from actions import build_status_payload, execute_bedtube_action
from audio_manager import list_playback_devices, switch_audio_by_match
from config_store import load_config, regenerate_pairing_credentials
from icon_gen import get_icon_png_bytes, get_lan_ip, get_qr_png_bytes
from paths import APP_VERSION, find_resource

logger = logging.getLogger("bedtube.web")

STATIC_DIR = find_resource("static")

SERVICE_WORKER_JS = r"""
const CACHE_NAME = 'bedtube-pwa-v5';
const CORE_ASSETS = [
  '/',
  '/manifest.json',
  '/static/mqtt.min.js',
  '/icon-192.png',
  '/icon-512.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(CORE_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  if (req.mode === 'navigate' || url.pathname === '/') {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(CACHE_NAME).then((c) => c.put('/', copy));
          }
          return res;
        })
        .catch(() => caches.match('/'))
    );
    return;
  }

  if (CORE_ASSETS.includes(url.pathname)) {
    event.respondWith(
      caches.match(url.pathname).then((cached) => {
        const net = fetch(req).then((res) => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(CACHE_NAME).then((c) => c.put(url.pathname, copy));
          }
          return res;
        }).catch(() => cached);
        return cached || net;
      })
    );
  }
});
"""

HTML_CONTROLE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<meta name="theme-color" content="#09090d">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="manifest" href="/manifest.json">
<link rel="icon" type="image/png" href="/icon-192.png">
<link rel="apple-touch-icon" href="/icon-192.png">
<script src="/static/mqtt.min.js"></script>
<title>BedTube Remote</title>
<style>
  :root {
    --bg: #09090d;
    --card: #13131b;
    --card-hi: #1c1c28;
    --border: rgba(255,255,255,0.08);
    --text: #f4f4f6;
    --muted: #8e8ea0;
    --accent: #e11d48;
    --green: #10b981;
    --amber: #f59e0b;
    --blue: #3b82f6;
  }
  * {
    box-sizing: border-box;
    -webkit-tap-highlight-color: transparent;
    user-select: none;
  }
  html, body {
    margin: 0;
    padding: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, system-ui, sans-serif;
    min-height: 100dvh;
    overscroll-behavior: none;
  }
  .wrap {
    max-width: 430px;
    margin: 0 auto;
    padding: calc(12px + env(safe-area-inset-top)) 14px calc(22px + env(safe-area-inset-bottom));
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .statusbar {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 18px;
    padding: 11px 13px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .status-left {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
  }
  .dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: #52525b;
    flex-shrink: 0;
    transition: all .25s ease;
  }
  .dot.on {
    background: var(--green);
    box-shadow: 0 0 12px var(--green);
  }
  .dot.standby {
    background: var(--amber);
    box-shadow: 0 0 8px rgba(245,158,11,0.45);
  }
  .status-titles { min-width: 0; }
  .status-main {
    font-size: 14px;
    font-weight: 700;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .status-sub {
    font-size: 11.5px;
    color: var(--muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-top: 2px;
  }
  .top-actions {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }
  .conn-badge {
    font-size: 10.5px;
    font-weight: 700;
    padding: 4px 8px;
    border-radius: 999px;
    background: rgba(59, 130, 246, 0.14);
    color: #93c5fd;
    border: 1px solid rgba(59, 130, 246, 0.3);
  }
  .conn-badge.mqtt {
    background: rgba(16, 185, 129, 0.14);
    color: #6ee7b7;
    border-color: rgba(16, 185, 129, 0.3);
  }
  .conn-badge.offline {
    background: rgba(239, 68, 68, 0.14);
    color: #fca5a5;
    border-color: rgba(239, 68, 68, 0.3);
  }
  .icon-btn {
    border: 1px solid var(--border);
    background: var(--card-hi);
    color: var(--text);
    border-radius: 10px;
    width: 34px;
    height: 34px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 15px;
    cursor: pointer;
  }

  .pwa-banner {
    display: none;
    background: linear-gradient(135deg, rgba(59,130,246,0.18), rgba(16,185,129,0.14));
    border: 1px solid rgba(59,130,246,0.35);
    border-radius: 14px;
    padding: 10px 12px;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    font-size: 12px;
  }
  .pwa-banner.show { display: flex; }
  .pwa-banner button {
    background: var(--blue);
    color: #fff;
    border: none;
    border-radius: 10px;
    padding: 7px 12px;
    font-weight: 700;
    font-size: 12px;
    flex-shrink: 0;
  }

  .hero-row {
    display: grid;
    grid-template-columns: 1.35fr 1fr;
    gap: 10px;
  }
  button {
    appearance: none;
    border: 1px solid var(--border);
    background: var(--card);
    color: var(--text);
    border-radius: 16px;
    font-family: inherit;
    font-weight: 600;
    font-size: 14px;
    cursor: pointer;
    transition: transform .08s ease, background .15s ease, border-color .15s ease;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 7px;
  }
  button:active {
    transform: scale(0.95);
    background: var(--card-hi);
  }
  .btn-open {
    background: linear-gradient(135deg, #e11d48, #be123c);
    border-color: rgba(255,255,255,0.16);
    height: 58px;
    font-size: 15px;
    font-weight: 700;
    box-shadow: 0 8px 22px rgba(225, 29, 72, 0.28);
  }
  .btn-close {
    background: #1b1b26;
    height: 58px;
    color: #fda4af;
    border-color: rgba(225, 29, 72, 0.28);
  }

  .dpad-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 22px;
    padding: 14px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .nav-top {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 8px;
  }
  .nav-top button {
    height: 44px;
    background: #191924;
    font-size: 13px;
  }
  .search-bar {
    display: flex;
    gap: 8px;
  }
  .search-bar input {
    flex: 1;
    background: #101018;
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 0 12px;
    height: 40px;
    color: var(--text);
    font-size: 13.5px;
    outline: none;
    user-select: text;
  }
  .search-bar input:focus { border-color: var(--accent); }
  .search-bar button {
    height: 40px;
    padding: 0 14px;
    border-radius: 12px;
    background: #232334;
    font-size: 13px;
    flex-shrink: 0;
  }
  .dpad {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    grid-template-rows: repeat(3, 64px);
    gap: 9px;
    margin: 2px 0;
  }
  .dpad button {
    background: #1c1c29;
    font-size: 22px;
    border-radius: 18px;
  }
  .dpad .ok-btn {
    background: linear-gradient(145deg, #2b2b3d, #20202f);
    border-color: rgba(255,255,255,0.16);
    font-size: 16px;
    font-weight: 800;
    letter-spacing: 0.5px;
  }
  .transport {
    display: grid;
    grid-template-columns: 1fr 1.4fr 1fr;
    gap: 8px;
  }
  .transport button {
    height: 46px;
    background: #191924;
    font-size: 14px;
  }

  .panel {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 13px 14px;
    display: flex;
    flex-direction: column;
    gap: 11px;
  }
  .panel-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 12.5px;
    color: var(--muted);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.6px;
  }
  .vol-badge {
    color: var(--text);
    font-size: 14px;
    font-weight: 800;
  }
  .vol-row {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 8px;
  }
  .vol-row button {
    height: 48px;
    background: #191924;
    font-size: 15px;
  }
  .vol-row button.muted-on {
    background: rgba(225, 29, 72, 0.22);
    border-color: var(--accent);
    color: #fecdd3;
  }
  input[type="range"] {
    width: 100%;
    accent-color: var(--accent);
    height: 6px;
    cursor: pointer;
  }
  .out-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  .out-row button {
    height: 44px;
    font-size: 13px;
    background: #181822;
  }
  .out-row button.active-out {
    background: rgba(16, 185, 129, 0.16);
    border-color: rgba(16, 185, 129, 0.55);
    color: #6ee7b7;
  }

  .util-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  .util-grid button {
    height: 44px;
    font-size: 12.5px;
    background: #181822;
  }
  .sleep-row {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 7px;
  }
  .sleep-row button {
    height: 38px;
    font-size: 12px;
    background: #181822;
    border-radius: 12px;
  }
  .sleep-row button.active-sleep,
  .sleep-row button.active-qual {
    background: rgba(245, 158, 11, 0.18);
    border-color: rgba(245, 158, 11, 0.55);
    color: #fde68a;
  }

  /* Modal de Configurações & Idioma */
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.72);
    backdrop-filter: blur(6px);
    display: none;
    align-items: center;
    justify-content: center;
    padding: 16px;
    z-index: 120;
  }
  .modal-backdrop.open { display: flex; }
  .modal-card {
    width: 100%;
    max-width: 400px;
    background: #15151f;
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 22px;
    padding: 18px;
    display: flex;
    flex-direction: column;
    gap: 13px;
  }
  .modal-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 16px;
    font-weight: 800;
  }
  .field-group {
    display: flex;
    flex-direction: column;
    gap: 5px;
  }
  .field-group label {
    font-size: 12px;
    color: var(--muted);
    font-weight: 600;
  }
  .field-group select, .field-group input[type="number"] {
    width: 100%;
    height: 42px;
    border-radius: 12px;
    border: 1px solid var(--border);
    background: #0e0e15;
    color: var(--text);
    padding: 0 10px;
    font-size: 13px;
  }
  .lang-pills {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 7px;
  }
  .lang-pills button {
    height: 36px;
    font-size: 12px;
    border-radius: 10px;
    background: #1b1b28;
  }
  .lang-pills button.active-lang {
    background: rgba(225, 29, 72, 0.22);
    border-color: var(--accent);
    color: #fecdd3;
  }
  .btn-save {
    height: 46px;
    background: linear-gradient(135deg, #10b981, #059669);
    border: none;
    color: #fff;
    font-weight: 700;
  }

  .toast {
    position: fixed;
    bottom: 18px;
    left: 50%;
    transform: translateX(-50%) translateY(20px);
    background: rgba(24, 24, 34, 0.96);
    border: 1px solid rgba(255,255,255,0.12);
    color: #fff;
    padding: 9px 16px;
    border-radius: 999px;
    font-size: 12.5px;
    font-weight: 600;
    opacity: 0;
    pointer-events: none;
    transition: all .2s ease;
    z-index: 200;
    max-width: 90vw;
    text-align: center;
  }
  .toast.show {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }
</style>
</head>
<body>
  <div class="wrap">
    <div class="statusbar">
      <div class="status-left">
        <div id="dot" class="dot"></div>
        <div class="status-titles">
          <div id="stMain" class="status-main">Conectando ao PC...</div>
          <div id="stSub" class="status-sub">Sincronizando estado...</div>
        </div>
      </div>
      <div class="top-actions">
        <div id="connBadge" class="conn-badge">...</div>
        <button class="icon-btn" onclick="openSettings()" title="Settings">⚙️</button>
      </div>
    </div>

    <div id="pwaBanner" class="pwa-banner">
      <span id="lblPwaBanner">📲 Instale o controle na tela inicial (funciona mesmo reiniciando o PC)</span>
      <button id="btnInstallPwa">Instalar</button>
    </div>

    <div class="hero-row">
      <button class="btn-open" onclick="openTV()">📺 <span id="lblOpenTv">Abrir TV</span></button>
      <button class="btn-close" onclick="closeTV()">⏹ <span id="lblCloseTv">Fechar</span></button>
    </div>

    <div class="dpad-card">
      <div class="nav-top">
        <button onclick="sendKey('back')">↩ <span id="lblBack">Voltar</span></button>
        <button onclick="sendKey('search')">🔍 <span id="lblSearchBtn">Busca</span></button>
        <button onclick="sendKey('home')">🏠 <span id="lblHome">Início</span></button>
      </div>

      <form class="search-bar" onsubmit="submitSearch(event)">
        <input id="searchInput" type="text" placeholder="Buscar vídeo ou colar link..." autocomplete="off">
        <button type="submit">Ir</button>
      </form>

      <div class="dpad">
        <div></div>
        <button onclick="sendKey('up')" aria-label="Up">▲</button>
        <div></div>
        <button onclick="sendKey('left')" aria-label="Left">◀</button>
        <button class="ok-btn" onclick="sendKey('enter')">OK</button>
        <button onclick="sendKey('right')" aria-label="Right">▶</button>
        <div></div>
        <button onclick="sendKey('down')" aria-label="Down">▼</button>
        <div></div>
      </div>

      <div class="transport">
        <button onclick="sendKey('rw')">⏪ -10s</button>
        <button onclick="sendKey('space')">⏯ Play / Pause</button>
        <button onclick="sendKey('ff')">+10s ⏩</button>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head">
        <span id="lblAudioHead">Áudio & Volume</span>
        <span id="volBadge" class="vol-badge">--%</span>
      </div>
      <input id="volSlider" type="range" min="0" max="100" step="1" value="25">
      <div class="vol-row">
        <button onclick="volStep(-5)">🔉 -5%</button>
        <button id="muteBtn" onclick="toggleMute()">🔇 <span id="lblMute">Mudo</span></button>
        <button onclick="volStep(5)">🔊 +5%</button>
      </div>
      <div class="out-row">
        <button id="btnSoundbar" onclick="switchAudio('soundbar')">🔊 Soundbar / TV</button>
        <button id="btnHeadset" onclick="switchAudio('headset')">🎧 <span id="lblHeadset">Fone / PC</span></button>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head">
        <span id="lblQualityHead">Qualidade do Vídeo (YouTube TV)</span>
        <span id="qualBadge" class="vol-badge" style="font-size:12px;color:#fde68a">1080p</span>
      </div>
      <div class="sleep-row">
        <button id="q1080" onclick="setQuality('1080')">🎬 1080p</button>
        <button id="qmax" onclick="setQuality('max')">✨ Máx/4K</button>
        <button id="q720" onclick="setQuality('720')">📺 720p</button>
        <button id="qauto" onclick="setQuality('auto')">⚡ Auto</button>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head">
        <span id="lblBedModeHead">Modo Cama & Sleep Timer</span>
        <span id="sleepBadge" style="color:#fde68a;font-weight:700">Off</span>
      </div>
      <div class="util-grid">
        <button onclick="monitorOff()">🌙 <span id="lblScreenOff">Apagar Tela</span></button>
        <button onclick="closeAndOff()">😴 <span id="lblSleepNow">Fechar + Apagar</span></button>
      </div>
      <div class="sleep-row">
        <button id="sl30" onclick="setSleep(30)">30m</button>
        <button id="sl60" onclick="setSleep(60)">60m</button>
        <button id="sl90" onclick="setSleep(90)">90m</button>
        <button onclick="setSleep(0)">Off</button>
      </div>
    </div>
  </div>

  <div id="settingsModal" class="modal-backdrop" onclick="if(event.target===this)closeSettings()">
    <div class="modal-card">
      <div class="modal-head">
        <span id="lblSettingsTitle">⚙️ Configurações</span>
        <button class="icon-btn" onclick="closeSettings()">✕</button>
      </div>

      <div class="field-group">
        <label id="lblLangSelect">Idioma / Language / Idioma</label>
        <div class="lang-pills">
          <button id="langPt" onclick="setLang('pt')">🇧🇷 PT</button>
          <button id="langEn" onclick="setLang('en')">🇺🇸 EN</button>
          <button id="langEs" onclick="setLang('es')">🇪🇸 ES</button>
        </div>
      </div>

      <div class="field-group">
        <label id="lblTvDevice">Saída de Áudio da TV / Soundbar</label>
        <select id="selSoundbar"></select>
      </div>

      <div class="field-group">
        <label id="lblPcDevice">Saída de Áudio Padrão (Fone / PC)</label>
        <select id="selHeadset"></select>
      </div>

      <div class="field-group">
        <label id="lblNightVol">Volume Inicial Noturno (%)</label>
        <input id="inpNightVol" type="number" min="0" max="100" value="25">
      </div>

      <button class="btn-save" onclick="saveSettingsModal()" id="lblSaveBtn">Salvar Configurações</button>
    </div>
  </div>

  <div id="toast" class="toast"></div>

<script>
const BOOT = __BEDTUBE_BOOT_JSON__;

const I18N = {
  pt: {
    connecting: 'Conectando ao PC...',
    syncing: 'Sincronizando estado...',
    tvOn: 'YouTube TV Ativo',
    pcMode: 'Modo PC',
    muted: 'MUDO',
    offlineTitle: 'Aguardando conexão com o PC',
    offlineSub: 'Tentando Túnel HTTPS + Relay MQTT...',
    pwaBanner: '📲 Instale na tela inicial (funciona após reiniciar o PC)',
    install: 'Instalar',
    openTv: 'Abrir TV',
    closeTv: 'Fechar',
    back: 'Voltar',
    searchBtn: 'Busca',
    home: 'Início',
    searchPh: 'Buscar vídeo ou colar link...',
    audioHead: 'Áudio & Volume',
    mute: 'Mudo',
    headset: 'Fone / PC',
    qualityHead: 'Qualidade do Vídeo (YouTube TV)',
    bedModeHead: 'Modo Cama & Sleep Timer',
    screenOff: 'Apagar Tela',
    sleepNow: 'Fechar + Apagar',
    settingsTitle: '⚙️ Configurações',
    tvDevice: 'Saída da TV / Soundbar (ao abrir TV)',
    pcDevice: 'Saída do Fone / PC (ao fechar TV)',
    nightVol: 'Volume Inicial ao Abrir TV (%)',
    saveBtn: 'Salvar Configurações',
    keepCurrent: '— Manter saída atual —'
  },
  en: {
    connecting: 'Connecting to PC...',
    syncing: 'Syncing state...',
    tvOn: 'YouTube TV Active',
    pcMode: 'PC Mode',
    muted: 'MUTED',
    offlineTitle: 'Waiting for PC connection',
    offlineSub: 'Trying HTTPS Tunnel + MQTT Relay...',
    pwaBanner: '📲 Add to Home Screen (works even after PC reboot)',
    install: 'Install',
    openTv: 'Open TV',
    closeTv: 'Close',
    back: 'Back',
    searchBtn: 'Search',
    home: 'Home',
    searchPh: 'Search video or paste YouTube URL...',
    audioHead: 'Audio & Volume',
    mute: 'Mute',
    headset: 'Headset / PC',
    qualityHead: 'Video Quality (YouTube TV)',
    bedModeHead: 'Bedtime & Sleep Timer',
    screenOff: 'Screen Off',
    sleepNow: 'Close + Screen Off',
    settingsTitle: '⚙️ Settings',
    tvDevice: 'TV / Soundbar Output (when TV opens)',
    pcDevice: 'Headset / PC Output (when TV closes)',
    nightVol: 'Initial Night Volume (%)',
    saveBtn: 'Save Settings',
    keepCurrent: '— Keep current output —'
  },
  es: {
    connecting: 'Conectando a la PC...',
    syncing: 'Sincronizando estado...',
    tvOn: 'YouTube TV Activo',
    pcMode: 'Modo PC',
    muted: 'SILENCIO',
    offlineTitle: 'Esperando conexión con la PC',
    offlineSub: 'Probando Túnel HTTPS + Relay MQTT...',
    pwaBanner: '📲 Instalar en pantalla de inicio (funciona tras reiniciar)',
    install: 'Instalar',
    openTv: 'Abrir TV',
    closeTv: 'Cerrar',
    back: 'Atrás',
    searchBtn: 'Buscar',
    home: 'Inicio',
    searchPh: 'Buscar video o pegar enlace...',
    audioHead: 'Audio y Volumen',
    mute: 'Silencio',
    headset: 'Auriculares / PC',
    qualityHead: 'Calidad de Video (YouTube TV)',
    bedModeHead: 'Modo Cama y Temporizador',
    screenOff: 'Apagar Pantalla',
    sleepNow: 'Cerrar + Apagar',
    settingsTitle: '⚙️ Ajustes',
    tvDevice: 'Salida de TV / Barra de Sonido',
    pcDevice: 'Salida de Auriculares / PC',
    nightVol: 'Volumen Nocturno Inicial (%)',
    saveBtn: 'Guardar Ajustes',
    keepCurrent: '— Mantener salida actual —'
  }
};

function detectLang() {
  const saved = localStorage.getItem('bedtube_lang');
  if (saved && I18N[saved]) return saved;
  const nav = (navigator.language || 'pt').toLowerCase();
  if (nav.startsWith('es')) return 'es';
  if (nav.startsWith('en')) return 'en';
  return 'pt';
}
let curLang = detectLang();
function t(key) { return (I18N[curLang] && I18N[curLang][key]) || I18N.pt[key] || key; }

function applyLangUI() {
  document.getElementById('lblPwaBanner').textContent = t('pwaBanner');
  document.getElementById('btnInstallPwa').textContent = t('install');
  document.getElementById('lblOpenTv').textContent = t('openTv');
  document.getElementById('lblCloseTv').textContent = t('closeTv');
  document.getElementById('lblBack').textContent = t('back');
  document.getElementById('lblSearchBtn').textContent = t('searchBtn');
  document.getElementById('lblHome').textContent = t('home');
  document.getElementById('searchInput').placeholder = t('searchPh');
  document.getElementById('lblAudioHead').textContent = t('audioHead');
  document.getElementById('lblMute').textContent = t('mute');
  document.getElementById('lblHeadset').textContent = t('headset');
  document.getElementById('lblQualityHead').textContent = t('qualityHead');
  document.getElementById('lblBedModeHead').textContent = t('bedModeHead');
  document.getElementById('lblScreenOff').textContent = t('screenOff');
  document.getElementById('lblSleepNow').textContent = t('sleepNow');
  document.getElementById('lblSettingsTitle').textContent = t('settingsTitle');
  document.getElementById('lblTvDevice').textContent = t('tvDevice');
  document.getElementById('lblPcDevice').textContent = t('pcDevice');
  document.getElementById('lblNightVol').textContent = t('nightVol');
  document.getElementById('lblSaveBtn').textContent = t('saveBtn');
  ['pt', 'en', 'es'].forEach(l => {
    const btn = document.getElementById('lang' + l.charAt(0).toUpperCase() + l.slice(1));
    if (btn) btn.classList.toggle('active-lang', l === curLang);
  });
}

function setLang(l) {
  if (!I18N[l]) return;
  curLang = l;
  localStorage.setItem('bedtube_lang', l);
  applyLangUI();
  if (lastState) renderState(lastState);
}

const urlParams = new URLSearchParams(location.search);
if (urlParams.get('t')) {
  localStorage.setItem('bedtube_token', urlParams.get('t'));
  history.replaceState({}, '', '/');
}
if (BOOT.token) localStorage.setItem('bedtube_token', BOOT.token);
if (BOOT.topic_id) localStorage.setItem('bedtube_topic', BOOT.topic_id);
if (BOOT.tunnel_url) localStorage.setItem('bedtube_api_base', BOOT.tunnel_url);
else if (location.origin.includes('trycloudflare.com')) localStorage.setItem('bedtube_api_base', location.origin);

const TOKEN = localStorage.getItem('bedtube_token') || '';
const TOPIC_ID = localStorage.getItem('bedtube_topic') || BOOT.topic_id || '';
let apiBase = localStorage.getItem('bedtube_api_base') || location.origin;
let httpAlive = false;
let mqttReady = false;
let mqttClient = null;
const pendingAcks = new Map();
let lastState = null;

let toastTimer = null;
const slider = document.getElementById('volSlider');
let draggingSlider = false;

applyLangUI();

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

let deferredPrompt = null;
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredPrompt = e;
  document.getElementById('pwaBanner').classList.add('show');
});
document.getElementById('btnInstallPwa').addEventListener('click', async () => {
  if (!deferredPrompt) return;
  deferredPrompt.prompt();
  await deferredPrompt.userChoice;
  deferredPrompt = null;
  document.getElementById('pwaBanner').classList.remove('show');
});

function buzz(ms = 22) {
  if (navigator.vibrate) navigator.vibrate(ms);
}

function showToast(msg) {
  if (!msg) return;
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2100);
}

function updateConnBadge() {
  const badge = document.getElementById('connBadge');
  if (httpAlive) {
    badge.textContent = apiBase.includes('trycloudflare') ? 'HTTPS Cloud' : 'Wi-Fi';
    badge.className = 'conn-badge';
  } else if (mqttReady) {
    badge.textContent = 'Relay MQTT';
    badge.className = 'conn-badge mqtt';
  } else {
    badge.textContent = 'Offline';
    badge.className = 'conn-badge offline';
  }
}

async function hmacSha256Hex(secret, message) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
  );
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(message));
  return Array.from(new Uint8Array(sig)).map(b => b.toString(16).padStart(2, '0')).join('');
}

async function tryHttp(path, method = 'POST', bodyObj = null) {
  const headers = { 'X-BedTube-Token': TOKEN };
  const opts = { method, headers, mode: 'cors' };
  if (bodyObj) {
    headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(bodyObj);
  }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 2400);
  opts.signal = ctrl.signal;
  try {
    const r = await fetch(apiBase + path, opts);
    clearTimeout(timer);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    httpAlive = true;
    updateConnBadge();
    return data;
  } catch (err) {
    clearTimeout(timer);
    httpAlive = false;
    updateConnBadge();
    throw err;
  }
}

async function sendViaMqtt(cmd, params = {}) {
  if (!mqttClient || !mqttReady || !TOPIC_ID || !TOKEN) {
    throw new Error('Sem conexão MQTT');
  }
  const ts = Date.now();
  const nonce = Math.random().toString(36).slice(2) + ts.toString(36);
  const sig = await hmacSha256Hex(TOKEN, `${ts}:${nonce}:${cmd}`);
  const payload = JSON.stringify({ ts, nonce, req_id: nonce, cmd, params, sig });

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pendingAcks.delete(nonce);
      reject(new Error('Timeout'));
    }, 4500);
    pendingAcks.set(nonce, (ackData) => {
      clearTimeout(timer);
      resolve(ackData);
    });
    mqttClient.publish(`bedtube/v1/${TOPIC_ID}/cmd`, payload, { qos: 1 });
  });
}

async function dispatch(httpPath, mqttCmd, params = {}, toastMsg = null) {
  buzz(22);
  try {
    let data;
    try {
      const body = Object.keys(params).length ? params : null;
      const method = httpPath === '/status' ? 'GET' : 'POST';
      data = await tryHttp(httpPath, method, body);
    } catch (_) {
      data = await sendViaMqtt(mqttCmd, params);
    }
    if (data) renderState(data);
    if (toastMsg || (data && data.message && mqttCmd !== 'ping')) {
      showToast(toastMsg || data.message);
    }
    return data;
  } catch (e) {
    showToast('Reconectando ao PC...');
    return null;
  }
}

function openTV() { dispatch('/iniciar', 'iniciar'); }
function closeTV() { dispatch('/fechar', 'fechar'); }
function closeAndOff() { dispatch('/fechar?off=1', 'fechar_apagar'); }
function sendKey(k) { dispatch('/tecla/' + k, 'tecla:' + k); }
function submitSearch(e) {
  e.preventDefault();
  const inp = document.getElementById('searchInput');
  const q = (inp.value || '').trim();
  if (!q) { sendKey('search'); return; }
  dispatch('/tv/buscar', 'tv_search', { query: q });
  inp.value = '';
  inp.blur();
}
function volStep(d) { dispatch(d > 0 ? '/volume/up' : '/volume/down', d > 0 ? 'vol_up' : 'vol_down'); }
function toggleMute() { dispatch('/volume/mute', 'mute'); }
function switchAudio(t) { dispatch('/audio/' + t, 'audio_' + t); }
function monitorOff() { dispatch('/monitor/off', 'monitor_off'); }
function setSleep(m) { dispatch('/sleep/' + m, 'sleep', { minutos: m }); }
function setQuality(q) { dispatch('/qualidade/' + q, 'qualidade:' + q); }

slider.addEventListener('pointerdown', () => { draggingSlider = true; });
slider.addEventListener('input', () => {
  document.getElementById('volBadge').textContent = slider.value + '%';
});
slider.addEventListener('change', () => {
  draggingSlider = false;
  const val = parseInt(slider.value, 10);
  dispatch('/volume/set/' + val, 'vol_set', { nivel: val });
});

async function openSettings() {
  document.getElementById('settingsModal').classList.add('open');
  let info = null;
  try {
    info = await tryHttp('/api/devices', 'GET');
  } catch (_) {
    try { info = await sendViaMqtt('list_devices', {}); } catch (__) {}
  }
  if (!info) return;
  const devs = info.devices || [];
  const suggested = info.suggested_roles || {};
  const curSb = info.soundbar_match || suggested.soundbar_match || '';
  const curHs = info.headset_match || suggested.headset_match || '';

  const fillSelect = (selId, selectedVal) => {
    const sel = document.getElementById(selId);
    sel.innerHTML = '';
    const emptyOpt = document.createElement('option');
    emptyOpt.value = '';
    emptyOpt.textContent = t('keepCurrent');
    sel.appendChild(emptyOpt);
    devs.forEach(d => {
      const opt = document.createElement('option');
      opt.value = d.name;
      opt.textContent = d.name;
      if (selectedVal && d.name.toLowerCase().includes(selectedVal.toLowerCase())) {
        opt.selected = true;
      }
      sel.appendChild(opt);
    });
  };
  fillSelect('selSoundbar', curSb);
  fillSelect('selHeadset', curHs);
  document.getElementById('inpNightVol').value = info.night_volume ?? 25;
}

function closeSettings() {
  document.getElementById('settingsModal').classList.remove('open');
}

async function saveSettingsModal() {
  const payload = {
    soundbar_match: document.getElementById('selSoundbar').value,
    headset_match: document.getElementById('selHeadset').value,
    night_volume: parseInt(document.getElementById('inpNightVol').value || '25', 10),
    language: curLang
  };
  await dispatch('/api/settings', 'save_settings', payload);
  closeSettings();
}

function renderState(s) {
  if (!s || !s.ok) return;
  lastState = s;

  if (s.tunnel_url && s.tunnel_url !== apiBase) {
    apiBase = s.tunnel_url;
    localStorage.setItem('bedtube_api_base', apiBase);
  }

  const dot = document.getElementById('dot');
  const stMain = document.getElementById('stMain');
  const stSub = document.getElementById('stSub');

  if (s.tv_open) {
    dot.className = 'dot on';
    stMain.textContent = `${t('tvOn')} • ${s.hostname || 'PC'}`;
  } else {
    dot.className = 'dot standby';
    stMain.textContent = `${t('pcMode')} • ${s.hostname || 'PC'}`;
  }
  stSub.textContent = `${s.device_name || ''} (${s.muted ? t('muted') : s.volume + '%'})`;

  if (!draggingSlider && typeof s.volume === 'number') {
    slider.value = s.volume;
    document.getElementById('volBadge').textContent = s.muted ? t('muted') : (s.volume + '%');
  }

  document.getElementById('muteBtn').classList.toggle('muted-on', !!s.muted);
  document.getElementById('btnSoundbar').classList.toggle('active-out', s.active_profile === 'soundbar');
  document.getElementById('btnHeadset').classList.toggle('active-out', s.active_profile === 'headset');

  const pq = s.preferred_quality || '1080';
  const qLabels = { '1080': '1080p Full HD', 'max': 'Máx / 4K', '720': '720p HD', 'auto': 'Auto' };
  document.getElementById('qualBadge').textContent = qLabels[pq] || pq;
  ['1080', 'max', '720', 'auto'].forEach(q => {
    const el = document.getElementById('q' + q);
    if (el) el.classList.toggle('active-qual', pq === q);
  });

  const rem = s.sleep_remaining_min;
  document.getElementById('sleepBadge').textContent = rem ? `⏱ ${rem} min` : 'Off';
  [30, 60, 90].forEach(m => {
    const btn = document.getElementById('sl' + m);
    btn.classList.toggle('active-sleep', rem && Math.abs(rem - m) <= 15);
  });
}

function initMqttRelay() {
  if (!window.mqtt || !TOPIC_ID) return;
  const brokers = [
    'wss://broker.emqx.io:8084/mqtt',
    'wss://broker.hivemq.com:8884/mqtt'
  ];
  let idx = 0;
  function connectNext() {
    const url = brokers[idx % brokers.length];
    idx++;
    const cli = window.mqtt.connect(url, {
      clientId: 'bt-mob-' + Math.random().toString(16).slice(2, 10),
      clean: true,
      connectTimeout: 5000,
      reconnectPeriod: 4000
    });
    mqttClient = cli;
    cli.on('connect', () => {
      mqttReady = true;
      updateConnBadge();
      cli.subscribe(`bedtube/v1/${TOPIC_ID}/state`, { qos: 1 });
      cli.subscribe(`bedtube/v1/${TOPIC_ID}/ack`, { qos: 1 });
      sendViaMqtt('ping').then(renderState).catch(() => {});
    });
    cli.on('close', () => {
      mqttReady = false;
      updateConnBadge();
    });
    cli.on('message', (topic, payload) => {
      try {
        const data = JSON.parse(new TextDecoder().decode(payload));
        if (topic.endsWith('/ack') && data.req_id && pendingAcks.has(data.req_id)) {
          const fn = pendingAcks.get(data.req_id);
          pendingAcks.delete(data.req_id);
          fn(data);
        }
        renderState(data);
      } catch (_) {}
    });
  }
  connectNext();
}

async function pollLoop() {
  try {
    const data = await tryHttp('/status', 'GET');
    renderState(data);
  } catch (_) {
    if (!mqttReady) {
      document.getElementById('dot').className = 'dot';
      document.getElementById('stMain').textContent = t('offlineTitle');
      document.getElementById('stSub').textContent = t('offlineSub');
    }
  }
}

initMqttRelay();
pollLoop();
setInterval(pollLoop, 3500);
</script>
</body>
</html>
"""

HTML_DESKTOP_HUB = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>BedTube Controller v__VERSION__ — Hub & Pareamento</title>
<link rel="icon" type="image/png" href="/icon-192.png">
<style>
  :root {
    --bg: #09090d;
    --card: #13131c;
    --card-hi: #1b1b28;
    --border: rgba(255,255,255,0.09);
    --text: #f4f4f6;
    --muted: #9ca3af;
    --accent: #e11d48;
    --green: #10b981;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    background: radial-gradient(circle at top right, rgba(225,29,72,0.12), transparent 45%), var(--bg);
    color: var(--text);
    font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
  }
  .hub {
    max-width: 880px;
    width: 100%;
    display: grid;
    grid-template-columns: 340px 1fr;
    gap: 20px;
  }
  @media (max-width: 780px) {
    .hub { grid-template-columns: 1fr; }
  }
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 24px;
    padding: 24px;
    box-shadow: 0 24px 60px rgba(0,0,0,0.55);
  }
  .qr-col {
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
  }
  .brand-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .brand img { width: 42px; height: 42px; border-radius: 11px; }
  .brand h1 { margin: 0; font-size: 20px; }
  .ver-pill {
    font-size: 11px;
    background: rgba(225,29,72,0.18);
    color: #fda4af;
    border: 1px solid rgba(225,29,72,0.35);
    padding: 3px 9px;
    border-radius: 999px;
    font-weight: 700;
  }
  .lang-switch {
    display: flex;
    gap: 5px;
  }
  .lang-switch button {
    background: var(--card-hi);
    color: var(--muted);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 5px 9px;
    font-size: 11.5px;
    font-weight: 700;
    cursor: pointer;
  }
  .lang-switch button.active {
    color: #fff;
    border-color: var(--accent);
    background: rgba(225,29,72,0.22);
  }
  .badge {
    display: inline-block;
    padding: 5px 12px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 700;
    background: rgba(16, 185, 129, 0.16);
    color: #6ee7b7;
    border: 1px solid rgba(16, 185, 129, 0.35);
    margin-bottom: 12px;
  }
  .qr-frame {
    background: #101015;
    padding: 12px;
    border-radius: 18px;
    border: 1px solid rgba(255,255,255,0.08);
    margin: 6px 0 12px;
  }
  .qr-frame img { width: 220px; height: 220px; display: block; }
  .url-box {
    width: 100%;
    background: #0b0b10;
    border: 1px solid var(--border);
    padding: 9px 11px;
    border-radius: 11px;
    font-family: Consolas, monospace;
    font-size: 11.5px;
    color: #6ee7b7;
    word-break: break-all;
    cursor: pointer;
  }
  .hint {
    font-size: 12.5px;
    color: var(--muted);
    line-height: 1.45;
    margin-top: 12px;
  }
  .form-grid {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .field label {
    display: block;
    font-size: 12.5px;
    font-weight: 600;
    color: var(--muted);
    margin-bottom: 6px;
  }
  .field select, .field input[type="number"] {
    width: 100%;
    height: 42px;
    background: #0d0d14;
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 11px;
    padding: 0 12px;
    font-size: 13.5px;
  }
  .row-2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
  }
  .check-row {
    display: flex;
    align-items: center;
    gap: 9px;
    font-size: 13.5px;
    cursor: pointer;
    padding: 4px 0;
  }
  .check-row input { width: 17px; height: 17px; accent-color: var(--accent); }
  .btn-row {
    display: grid;
    grid-template-columns: 1.2fr 1fr;
    gap: 10px;
    margin-top: 6px;
  }
  .btn {
    height: 46px;
    border-radius: 13px;
    border: 1px solid var(--border);
    font-weight: 700;
    font-size: 14px;
    cursor: pointer;
    color: #fff;
  }
  .btn-primary {
    background: linear-gradient(135deg, #10b981, #059669);
    border: none;
  }
  .btn-tv {
    background: linear-gradient(135deg, #e11d48, #be123c);
    border: none;
  }
  .status-pill {
    font-size: 12.5px;
    color: #6ee7b7;
    min-height: 18px;
    font-weight: 600;
  }
</style>
</head>
<body>
  <div class="hub">
    <div class="card qr-col">
      <div id="tunnelBadge" class="badge">⏳ Conectando Túnel HTTPS...</div>
      <div id="scanSub" style="font-size:13px;color:var(--muted)">Aponte a câmera do celular para parear</div>
      <div class="qr-frame">
        <img id="qrImg" src="/qr.png" alt="QR Code BedTube">
      </div>
      <div id="pairUrl" class="url-box" title="Clique para copiar" onclick="copyPairUrl()">Carregando...</div>
      <div id="qrHint" class="hint">
        Funciona com <b>PC no Cabo + Celular no Wi-Fi</b> sem configurar roteador.<br>
        No celular, toque em <b>Adicionar à Tela Inicial</b> para usar sempre!
      </div>
    </div>

    <div class="card">
      <div class="brand-row">
        <div class="brand">
          <img src="/icon-192.png" alt="BedTube">
          <div>
            <h1>BedTube Controller <span class="ver-pill">v__VERSION__</span></h1>
            <div id="subHeader" style="font-size:12.5px;color:var(--muted)">YouTube TV 1080p60/4K + Automação de Áudio</div>
          </div>
        </div>
        <div class="lang-switch">
          <button id="bPt" onclick="setHubLang('pt')">PT</button>
          <button id="bEn" onclick="setHubLang('en')">EN</button>
          <button id="bEs" onclick="setHubLang('es')">ES</button>
        </div>
      </div>

      <div class="form-grid">
        <div class="field">
          <label id="lblSb">🔊 Saída de Áudio do Modo TV (Soundbar / TV HDMI / Caixa)</label>
          <select id="selSb"></select>
        </div>

        <div class="field">
          <label id="lblHs">🎧 Saída de Áudio Padrão do PC (Fone / Headset ao fechar a TV)</label>
          <select id="selHs"></select>
        </div>

        <div class="row-2">
          <div class="field">
            <label id="lblVol">🌙 Volume Noturno Inicial (%)</label>
            <input id="inpVol" type="number" min="0" max="100" value="25">
          </div>
          <div class="field">
            <label id="lblQual">🎬 Qualidade Padrão do Vídeo</label>
            <select id="selQual">
              <option value="1080">1080p Full HD (60fps)</option>
              <option value="max">Máxima / 4K60 / 1440p</option>
              <option value="720">720p HD</option>
              <option value="auto">Automática</option>
            </select>
          </div>
        </div>

        <label class="check-row">
          <input id="chkAutostart" type="checkbox">
          <span id="lblAuto">Iniciar silenciosamente junto com o Windows (Bandeja do Sistema)</span>
        </label>

        <div id="saveMsg" class="status-pill"></div>

        <div class="btn-row">
          <button id="btnSave" class="btn btn-primary" onclick="saveHubSettings()">💾 Salvar Configurações</button>
          <button id="btnTestTv" class="btn btn-tv" onclick="testOpenTv()">📺 Testar Modo TV Agora</button>
        </div>
      </div>
    </div>
  </div>

<script>
const TXT = {
  pt: {
    cloudOk: '☁️ Túnel HTTPS Ativo (Cabo ↔ Wi-Fi Liberado)',
    cloudWait: '⏳ Iniciando Túnel HTTPS Seguro...',
    scanSub: 'Aponte a câmera do celular para parear',
    qrHint: 'Funciona com <b>PC no Cabo + Celular no Wi-Fi</b> sem configurar roteador.<br>No celular, toque em <b>Adicionar à Tela Inicial</b> para usar sempre!',
    subHeader: 'YouTube TV 1080p60/4K + Automação de Áudio',
    lblSb: '🔊 Saída de Áudio do Modo TV (Soundbar / TV HDMI / Caixa)',
    lblHs: '🎧 Saída de Áudio Padrão do PC (Fone / Headset ao fechar a TV)',
    lblVol: '🌙 Volume Noturno Inicial (%)',
    lblQual: '🎬 Qualidade Padrão do Vídeo',
    lblAuto: 'Iniciar silenciosamente junto com o Windows (Bandeja do Sistema)',
    btnSave: '💾 Salvar Configurações',
    btnTestTv: '📺 Testar Modo TV Agora',
    keepCur: '— Manter saída de áudio atual —',
    saved: '✓ Configurações salvas com sucesso!',
    copied: '✓ Link copiado para a área de transferência!'
  },
  en: {
    cloudOk: '☁️ HTTPS Cloud Tunnel Active (Ethernet ↔ Wi-Fi Ready)',
    cloudWait: '⏳ Starting Secure HTTPS Tunnel...',
    scanSub: 'Scan with your phone camera to pair',
    qrHint: 'Works across <b>Wired PC + Wi-Fi Phone</b> with zero router setup.<br>Tap <b>Add to Home Screen</b> on your phone for instant bedtime access!',
    subHeader: 'YouTube TV 1080p60/4K + Automatic Audio Switcher',
    lblSb: '🔊 TV Mode Audio Output (Soundbar / HDMI TV / Speakers)',
    lblHs: '🎧 Default PC Audio Output (Headset / Headphones when TV closes)',
    lblVol: '🌙 Initial Night Volume (%)',
    lblQual: '🎬 Default Video Quality',
    lblAuto: 'Start silently with Windows (System Tray)',
    btnSave: '💾 Save Settings',
    btnTestTv: '📺 Test TV Mode Now',
    keepCur: '— Keep current audio output —',
    saved: '✓ Settings saved successfully!',
    copied: '✓ Pairing link copied to clipboard!'
  },
  es: {
    cloudOk: '☁️ Túnel HTTPS Activo (Cable ↔ Wi-Fi Listo)',
    cloudWait: '⏳ Iniciando Túnel HTTPS Seguro...',
    scanSub: 'Escanea con la cámara de tu móvil para vincular',
    qrHint: 'Funciona con <b>PC por Cable + Móvil en Wi-Fi</b> sin configurar el router.<br>¡Toca <b>Añadir a pantalla de inicio</b> en tu celular!',
    subHeader: 'YouTube TV 1080p60/4K + Cambio Automático de Audio',
    lblSb: '🔊 Salida de Audio para Modo TV (Barra de sonido / TV HDMI)',
    lblHs: '🎧 Salida de Audio de PC (Auriculares al cerrar la TV)',
    lblVol: '🌙 Volumen Nocturno Inicial (%)',
    lblQual: '🎬 Calidad de Video Predeterminada',
    lblAuto: 'Iniciar silenciosamente con Windows (Bandeja del sistema)',
    btnSave: '💾 Guardar Ajustes',
    btnTestTv: '📺 Probar Modo TV Ahora',
    keepCur: '— Mantener salida actual —',
    saved: '✓ ¡Ajustes guardados correctamente!',
    copied: '✓ ¡Enlace copiado al portapapeles!'
  }
};

let lang = localStorage.getItem('bedtube_hub_lang') || ((navigator.language || 'pt').slice(0, 2));
if (!TXT[lang]) lang = 'pt';
let lastPairingUrl = '';
let initializedForm = false;

function applyHubLang() {
  const d = TXT[lang];
  document.getElementById('scanSub').textContent = d.scanSub;
  document.getElementById('qrHint').innerHTML = d.qrHint;
  document.getElementById('subHeader').textContent = d.subHeader;
  document.getElementById('lblSb').textContent = d.lblSb;
  document.getElementById('lblHs').textContent = d.lblHs;
  document.getElementById('lblVol').textContent = d.lblVol;
  document.getElementById('lblQual').textContent = d.lblQual;
  document.getElementById('lblAuto').textContent = d.lblAuto;
  document.getElementById('btnSave').textContent = d.btnSave;
  document.getElementById('btnTestTv').textContent = d.btnTestTv;
  ['pt', 'en', 'es'].forEach(l => {
    document.getElementById('b' + l.charAt(0).toUpperCase() + l.slice(1)).classList.toggle('active', l === lang);
  });
}

function setHubLang(l) {
  lang = l;
  localStorage.setItem('bedtube_hub_lang', l);
  applyHubLang();
  refreshHubState(true);
}

async function refreshHubState(forceRebuildSelects = false) {
  try {
    const r = await fetch('/api/local/hub-state');
    const s = await r.json();
    const d = TXT[lang];
    document.getElementById('tunnelBadge').textContent = s.tunnel_online ? d.cloudOk : d.cloudWait;

    if (s.pairing_url && s.pairing_url !== lastPairingUrl) {
      lastPairingUrl = s.pairing_url;
      document.getElementById('pairUrl').textContent = s.pairing_url;
      document.getElementById('qrImg').src = '/qr.png?ts=' + Date.now();
    }

    if (!initializedForm || forceRebuildSelects) {
      initializedForm = true;
      const devs = s.devices || [];
      const sug = s.suggested_roles || {};
      const sbVal = s.soundbar_match || sug.soundbar_match || '';
      const hsVal = s.headset_match || sug.headset_match || '';

      const populate = (id, selected) => {
        const sel = document.getElementById(id);
        sel.innerHTML = '';
        const empty = document.createElement('option');
        empty.value = '';
        empty.textContent = d.keepCur;
        sel.appendChild(empty);
        devs.forEach(dev => {
          const opt = document.createElement('option');
          opt.value = dev.name;
          opt.textContent = dev.name + (dev.is_default ? ' ★' : '');
          if (selected && dev.name.toLowerCase().includes(selected.toLowerCase())) {
            opt.selected = true;
          }
          sel.appendChild(opt);
        });
      };
      populate('selSb', sbVal);
      populate('selHs', hsVal);
      document.getElementById('inpVol').value = s.night_volume ?? 25;
      document.getElementById('selQual').value = s.preferred_quality || '1080';
      document.getElementById('chkAutostart').checked = !!s.autostart;
    }
  } catch (_) {}
}

async function saveHubSettings() {
  const body = {
    soundbar_match: document.getElementById('selSb').value,
    headset_match: document.getElementById('selHs').value,
    night_volume: parseInt(document.getElementById('inpVol').value || '25', 10),
    preferred_quality: document.getElementById('selQual').value,
    autostart: document.getElementById('chkAutostart').checked,
    language: lang
  };
  const r = await fetch('/api/local/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (r.ok) {
    const msg = document.getElementById('saveMsg');
    msg.textContent = TXT[lang].saved;
    setTimeout(() => { msg.textContent = ''; }, 3000);
  }
}

async function testOpenTv() {
  await saveHubSettings();
  await fetch('/api/local/test-tv', { method: 'POST' });
}

function copyPairUrl() {
  if (!lastPairingUrl) return;
  navigator.clipboard.writeText(lastPairingUrl);
  const msg = document.getElementById('saveMsg');
  msg.textContent = TXT[lang].copied;
  setTimeout(() => { msg.textContent = ''; }, 2500);
}

applyHubLang();
refreshHubState(true);
setInterval(() => refreshHubState(false), 2000);
</script>
</body>
</html>
"""


def _is_strictly_local_pc() -> bool:
    is_loopback = request.remote_addr in ("127.0.0.1", "::1")
    via_cloudflare = bool(request.headers.get("CF-Connecting-IP"))
    return is_loopback and not via_cloudflare


def create_web_app(tv_mgr, cloud_bridge=None, shutdown_cb=None) -> Flask:
    app = Flask(__name__, static_folder=STATIC_DIR)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    PUBLIC_PATHS = {
        "/sw.js",
        "/manifest.json",
        "/icon-192.png",
        "/icon-512.png",
    }

    def _is_authorized() -> bool:
        cfg = load_config()
        expected = cfg.get("access_token", "")
        if not expected:
            return False
        provided = (
            request.args.get("t")
            or request.headers.get("X-BedTube-Token")
            or request.cookies.get("bedtube_auth")
            or ""
        )
        return hmac.compare_digest(provided, expected)

    @app.after_request
    def _add_cors(resp):
        origin = request.headers.get("Origin")
        if origin:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-BedTube-Token"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Credentials"] = "true"
        return resp

    @app.before_request
    def _security_gate():
        if request.method == "OPTIONS":
            return Response("", status=204)

        path = request.path
        if path in PUBLIC_PATHS or path.startswith("/static/"):
            return None

        if path in ("/qr", "/qr.png") or path.startswith("/api/local/"):
            if not _is_strictly_local_pc():
                return Response("Acesso restrito ao próprio computador.", status=403)
            return None

        if not _is_authorized():
            if path == "/":
                return Response(
                    "<h3>🔒 BedTube Protegido</h3><p>Abra o ícone do BedTube na bandeja do PC e escaneie o QR Code para parear seu celular.</p>",
                    status=401,
                    mimetype="text/html",
                )
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        return None

    @app.route("/")
    def index():
        cfg = load_config()
        boot = {
            "token": cfg["access_token"],
            "topic_id": cfg["relay_topic_id"],
            "tunnel_url": cloud_bridge.tunnel_url if cloud_bridge else None,
        }
        rendered = HTML_CONTROLE.replace("__BEDTUBE_BOOT_JSON__", json.dumps(boot))
        resp = make_response(rendered)
        resp.headers["Content-Type"] = "text/html; charset=utf-8"
        is_https = request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
        resp.set_cookie(
            "bedtube_auth",
            cfg["access_token"],
            max_age=365 * 24 * 3600,
            httponly=True,
            secure=is_https,
            samesite="Lax",
        )
        return resp

    @app.route("/sw.js")
    def sw_js():
        resp = make_response(SERVICE_WORKER_JS)
        resp.headers["Content-Type"] = "application/javascript; charset=utf-8"
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.route("/static/<path:filename>")
    def static_files(filename: str):
        return send_from_directory(STATIC_DIR, filename)

    @app.route("/manifest.json")
    def manifest():
        return jsonify(
            {
                "name": "BedTube Remote",
                "short_name": "BedTube",
                "description": "Controle Remoto de Cama p/ YouTube TV e Soundbar",
                "start_url": "/",
                "display": "standalone",
                "background_color": "#09090d",
                "theme_color": "#09090d",
                "orientation": "portrait",
                "icons": [
                    {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                    {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
                ],
            }
        )

    @app.route("/icon-192.png")
    def icon_192():
        return Response(get_icon_png_bytes(192), mimetype="image/png")

    @app.route("/icon-512.png")
    def icon_512():
        return Response(get_icon_png_bytes(512), mimetype="image/png")

    @app.route("/qr")
    def qr_page():
        html = HTML_DESKTOP_HUB.replace("__VERSION__", APP_VERSION)
        return Response(html, mimetype="text/html; charset=utf-8")

    @app.route("/qr.png")
    def qr_png():
        cfg = load_config()
        lan_url = f"http://{get_lan_ip()}:{cfg['port']}"
        pairing_url = cloud_bridge.get_pairing_url(lan_url) if cloud_bridge else f"{lan_url}/?t={cfg['access_token']}"
        resp = Response(get_qr_png_bytes(pairing_url), mimetype="image/png")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/api/local/hub-state")
    def local_hub_state():
        cfg = load_config()
        lan_url = f"http://{get_lan_ip()}:{cfg['port']}"
        pairing_url = cloud_bridge.get_pairing_url(lan_url) if cloud_bridge else f"{lan_url}/?t={cfg['access_token']}"
        st = build_status_payload(tv_mgr, include_devices=True)
        st["pairing_url"] = pairing_url
        st["tunnel_online"] = bool(cloud_bridge and cloud_bridge.tunnel_url)
        st["mqtt_connected"] = bool(cloud_bridge and cloud_bridge.mqtt_connected)
        return jsonify(st)

    @app.route("/api/local/settings", methods=["POST"])
    def local_save_settings():
        body = request.get_json(silent=True) or {}
        res = execute_bedtube_action(tv_mgr, "save_settings", body)
        if cloud_bridge:
            cloud_bridge.publish_state()
        return jsonify(res)

    @app.route("/api/local/test-tv", methods=["POST"])
    def local_test_tv():
        res = execute_bedtube_action(tv_mgr, "iniciar")
        if cloud_bridge:
            cloud_bridge.publish_state()
        return jsonify(res)

    @app.route("/api/local/reset-pairing", methods=["POST"])
    def local_reset_pairing():
        regenerate_pairing_credentials()
        return jsonify({"ok": True})

    @app.route("/api/local/shutdown", methods=["POST"])
    def local_shutdown():
        if callable(shutdown_cb):
            threading.Thread(target=shutdown_cb, daemon=True).start()
        return jsonify({"ok": True, "shutting_down": True})

    @app.route("/status")
    def status():
        payload = build_status_payload(tv_mgr)
        if cloud_bridge:
            payload["tunnel_url"] = cloud_bridge.tunnel_url
        return jsonify(payload)

    @app.route("/api/devices")
    def api_devices():
        return jsonify(build_status_payload(tv_mgr, include_devices=True))

    @app.route("/api/settings", methods=["POST"])
    def api_settings():
        body = request.get_json(silent=True) or {}
        res = execute_bedtube_action(tv_mgr, "save_settings", body)
        if cloud_bridge:
            cloud_bridge.publish_state()
        return jsonify(res)

    @app.route("/iniciar", methods=["POST"])
    def iniciar():
        res = execute_bedtube_action(tv_mgr, "iniciar")
        if cloud_bridge:
            cloud_bridge.publish_state()
        return jsonify(res)

    @app.route("/fechar", methods=["POST"])
    def fechar():
        off_screen = request.args.get("off") == "1"
        res = execute_bedtube_action(tv_mgr, "fechar", {"off": off_screen})
        if cloud_bridge:
            cloud_bridge.publish_state()
        return jsonify(res)

    @app.route("/tecla/<acao>", methods=["POST"])
    def tecla(acao: str):
        return jsonify(execute_bedtube_action(tv_mgr, f"tecla:{acao}"))

    @app.route("/tv/buscar", methods=["POST"])
    def tv_buscar():
        body = request.get_json(silent=True) or {}
        query = str(body.get("query") or request.args.get("q") or "").strip()
        return jsonify(execute_bedtube_action(tv_mgr, "tv_search", {"query": query}))

    @app.route("/volume/up", methods=["POST"])
    def vol_up():
        res = execute_bedtube_action(tv_mgr, "vol_up")
        if cloud_bridge:
            cloud_bridge.publish_state()
        return jsonify(res)

    @app.route("/volume/down", methods=["POST"])
    def vol_down():
        res = execute_bedtube_action(tv_mgr, "vol_down")
        if cloud_bridge:
            cloud_bridge.publish_state()
        return jsonify(res)

    @app.route("/volume/set/<int:nivel>", methods=["POST"])
    def vol_set(nivel: int):
        res = execute_bedtube_action(tv_mgr, "vol_set", {"nivel": nivel})
        if cloud_bridge:
            cloud_bridge.publish_state()
        return jsonify(res)

    @app.route("/volume/mute", methods=["POST"])
    def vol_mute():
        res = execute_bedtube_action(tv_mgr, "mute")
        if cloud_bridge:
            cloud_bridge.publish_state()
        return jsonify(res)

    @app.route("/audio/<alvo>", methods=["POST"])
    def trocar_audio(alvo: str):
        alvo_low = alvo.lower()
        if alvo_low in ("soundbar", "headset"):
            res = execute_bedtube_action(tv_mgr, f"audio_{alvo_low}")
        else:
            ok, msg = switch_audio_by_match(alvo, volume=None, unmute=False)
            res = build_status_payload(tv_mgr)
            res.update({"ok": ok, "message": f"Áudio: {msg}"})
        if cloud_bridge:
            cloud_bridge.publish_state()
        return jsonify(res)

    @app.route("/monitor/off", methods=["POST"])
    def monitor_off():
        return jsonify(execute_bedtube_action(tv_mgr, "monitor_off"))

    @app.route("/monitor/wake", methods=["POST"])
    def monitor_wake():
        return jsonify(execute_bedtube_action(tv_mgr, "monitor_wake"))

    @app.route("/sleep/<int:minutos>", methods=["POST"])
    def sleep_timer(minutos: int):
        res = execute_bedtube_action(tv_mgr, "sleep", {"minutos": minutos})
        if cloud_bridge:
            cloud_bridge.publish_state()
        return jsonify(res)

    @app.route("/qualidade/<modo>", methods=["POST"])
    def set_qualidade(modo: str):
        res = execute_bedtube_action(tv_mgr, f"qualidade:{modo}")
        if cloud_bridge:
            cloud_bridge.publish_state()
        return jsonify(res)

    @app.route("/tv/stats")
    def tv_stats():
        return jsonify(tv_mgr.get_tv_player_stats())

    return app
