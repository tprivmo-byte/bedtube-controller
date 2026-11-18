import hmac
import json
import logging
import os
import threading

from flask import Flask, Response, jsonify, make_response, request, send_from_directory

from actions import build_status_payload, execute_bedtube_action
from audio_manager import switch_audio_by_match
from config_store import load_config, regenerate_pairing_credentials
from icon_gen import get_icon_png_bytes, get_lan_ip, get_qr_png_bytes
from paths import APP_VERSION, find_resource

logger = logging.getLogger("bedtube.web")

STATIC_DIR = find_resource("static")

SERVICE_WORKER_JS = r"""
const CACHE_NAME = 'bedtube-pwa-v6';
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
<meta name="theme-color" content="#0b0b10">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="manifest" href="/manifest.json">
<link rel="icon" type="image/png" href="/icon-192.png">
<link rel="apple-touch-icon" href="/icon-192.png">
<script src="/static/mqtt.min.js"></script>
<title>BedTube Remote</title>
<style>
  :root {
    --bg: #0b0b10;
    --card: #16161e;
    --card-elev: #1c1c27;
    --pill: #23232f;
    --pill-hover: #2b2b3a;
    --track: #0d0d13;
    --seg-active: #272735;
    --border: rgba(255, 255, 255, 0.075);
    --border-hi: rgba(255, 255, 255, 0.14);
    --glow-border: rgba(124, 131, 255, 0.44);
    --text: #f4f4f8;
    --muted: #8b8ba0;
    --indigo-1: #3f37c9;
    --indigo-2: #5b5bf0;
    --indigo-3: #7c83ff;
    --indigo-glow: rgba(99, 102, 241, 0.38);
    --danger: #ef4444;
    --amber: #f59e0b;
    --emerald: #10b981;
    --spring: cubic-bezier(0.22, 1, 0.36, 1);
    --smooth: cubic-bezier(0.4, 0, 0.2, 1);
  }

  * {
    box-sizing: border-box;
    -webkit-tap-highlight-color: transparent;
    user-select: none;
  }

  html, body {
    margin: 0;
    padding: 0;
    background-color: var(--bg);
    background-image: radial-gradient(ellipse 80% 45% at 50% -10%, rgba(91, 91, 240, 0.11), transparent 70%);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Inter, Roboto, system-ui, sans-serif;
    min-height: 100dvh;
    overscroll-behavior: none;
    -webkit-font-smoothing: antialiased;
  }

  .wrap {
    max-width: 420px;
    margin: 0 auto;
    padding: calc(12px + env(safe-area-inset-top)) 14px calc(28px + env(safe-area-inset-bottom));
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  /* === 3. Staggered Entrance Cascade (60fps GPU) === */
  @keyframes btEntrance {
    0% {
      opacity: 0;
      transform: translate3d(0, 18px, 0) scale(0.985);
    }
    100% {
      opacity: 1;
      transform: translate3d(0, 0, 0) scale(1);
    }
  }

  .stagger {
    will-change: transform, opacity;
    animation: btEntrance 0.46s var(--spring) both;
    animation-delay: calc(var(--i, 0) * 55ms);
  }

  @media (prefers-reduced-motion: reduce) {
    .stagger { animation: none !important; }
  }

  /* === Base Card & Tactile Micro-interactions === */
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 22px;
    padding: 16px;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.035), 0 12px 32px rgba(0, 0, 0, 0.36);
    transform: translate3d(0, 0, 0);
    transition: transform 0.28s var(--spring), border-color 0.3s var(--smooth), box-shadow 0.3s var(--smooth);
  }

  @media (hover: hover) {
    .card:hover {
      border-color: rgba(255, 255, 255, 0.115);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.05), 0 16px 38px rgba(0, 0, 0, 0.48);
    }
  }

  button {
    appearance: none;
    border: 1px solid var(--border);
    background: var(--pill);
    color: var(--text);
    font-family: inherit;
    font-weight: 600;
    font-size: 14px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 7px;
    position: relative;
    overflow: hidden;
    will-change: transform;
    transform: translate3d(0, 0, 0) scale(1);
    transition:
      transform 0.22s var(--spring),
      background-color 0.3s var(--smooth),
      border-color 0.3s var(--smooth),
      color 0.3s var(--smooth),
      box-shadow 0.3s var(--smooth);
  }

  @media (hover: hover) {
    button:hover {
      background: var(--pill-hover);
      border-color: var(--glow-border);
      box-shadow: 0 6px 18px -4px rgba(91, 91, 240, 0.28);
      transform: translate3d(0, -1.5px, 0) scale(1);
    }
  }

  button:active, button.is-pressed {
    transform: translate3d(0, 0, 0) scale(0.955) !important;
    transition-duration: 0.08s;
  }

  svg {
    flex-shrink: 0;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  /* === 1. Header Status Pill (Matches Reference) === */
  .statusbar {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 18px;
    padding: 12px 14px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.035), 0 8px 24px rgba(0,0,0,0.32);
  }

  .status-left {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
  }

  .orb-wrap {
    width: 24px;
    height: 24px;
    border-radius: 50%;
    background: rgba(99, 102, 241, 0.14);
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    transition: background-color 0.35s var(--smooth), box-shadow 0.35s var(--smooth);
  }

  .orb {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: #6366f1;
    box-shadow: 0 0 12px 2px rgba(99, 102, 241, 0.85);
    transition: all 0.35s var(--smooth);
  }

  .orb-wrap.tv-active {
    background: rgba(124, 131, 255, 0.22);
    box-shadow: 0 0 18px rgba(99, 102, 241, 0.32);
  }
  .orb-wrap.tv-active .orb {
    background: #818cf8;
    box-shadow: 0 0 14px 3px rgba(129, 140, 248, 0.95);
  }
  .orb-wrap.offline {
    background: rgba(239, 68, 68, 0.12);
  }
  .orb-wrap.offline .orb {
    background: #71717a;
    box-shadow: none;
  }

  .status-titles { min-width: 0; }
  .status-main {
    font-size: 14.5px;
    font-weight: 700;
    letter-spacing: -0.01em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .status-main .host-part {
    font-weight: 500;
    color: #d4d4df;
  }
  .status-sub {
    font-size: 12px;
    color: var(--muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-top: 2px;
  }

  .status-right {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }

  .tv-quick-pill {
    height: 34px;
    padding: 0 12px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 700;
    background: rgba(91, 91, 240, 0.16);
    border-color: rgba(124, 131, 255, 0.36);
    color: #c7d2fe;
  }
  .tv-quick-pill.tv-is-open {
    background: linear-gradient(135deg, var(--indigo-1), var(--indigo-2));
    border-color: rgba(165, 180, 252, 0.5);
    color: #fff;
    box-shadow: 0 0 16px rgba(91, 91, 240, 0.42);
  }

  .gear-btn {
    width: 38px;
    height: 38px;
    border-radius: 12px;
    background: transparent;
    border-color: transparent;
    color: #a1a1b5;
    padding: 0;
  }
  .gear-btn:hover {
    background: rgba(255, 255, 255, 0.05);
    color: var(--text);
  }

  /* === Contextual Quick Banners (Wake Screen / Shutdown Countdown / PWA) === */
  .alert-pill {
    display: none;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    border-radius: 16px;
    padding: 10px 13px;
    font-size: 12.5px;
    font-weight: 600;
    animation: btEntrance 0.28s var(--spring) both;
  }
  .alert-pill.show { display: flex; }

  .wake-banner {
    background: linear-gradient(135deg, rgba(91, 91, 240, 0.2), rgba(245, 158, 11, 0.14));
    border: 1px solid rgba(251, 191, 36, 0.38);
    color: #fef3c7;
    cursor: pointer;
  }
  .wake-banner span.cta {
    background: rgba(245, 158, 11, 0.22);
    border: 1px solid rgba(251, 191, 36, 0.45);
    color: #fde68a;
    padding: 5px 11px;
    border-radius: 999px;
    font-weight: 700;
    font-size: 11.5px;
    white-space: nowrap;
  }

  .shutdown-banner {
    background: linear-gradient(135deg, rgba(239, 68, 68, 0.2), rgba(153, 27, 27, 0.22));
    border: 1px solid rgba(248, 113, 113, 0.42);
    color: #fecaca;
  }
  .shutdown-banner button {
    height: 30px;
    padding: 0 12px;
    border-radius: 999px;
    background: rgba(239, 68, 68, 0.28);
    border-color: rgba(252, 165, 165, 0.45);
    color: #fff;
    font-size: 11.5px;
    font-weight: 700;
    flex-shrink: 0;
  }

  .pwa-banner {
    background: linear-gradient(135deg, rgba(63, 55, 201, 0.22), rgba(91, 91, 240, 0.12));
    border: 1px solid rgba(124, 131, 255, 0.34);
    color: #e0e7ff;
  }
  .pwa-banner button {
    height: 32px;
    padding: 0 13px;
    border-radius: 999px;
    background: var(--indigo-2);
    border: none;
    color: #fff;
    font-size: 12px;
    font-weight: 700;
    flex-shrink: 0;
  }

  /* === 2. Navigation + Search Bar Row (Matches Reference 1:1) === */
  .nav-search-row {
    display: grid;
    grid-template-columns: 50px 1fr 50px;
    gap: 10px;
    align-items: center;
  }

  .sq-nav-btn {
    width: 50px;
    height: 50px;
    border-radius: 15px;
    background: var(--card);
    border: 1px solid var(--border);
    color: #e4e4ec;
    padding: 0;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.035);
  }

  .search-shell {
    height: 50px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 15px;
    display: flex;
    align-items: center;
    padding: 0 6px 0 15px;
    transition: border-color 0.3s var(--smooth), box-shadow 0.3s var(--smooth);
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
  }
  .search-shell:focus-within {
    border-color: var(--glow-border);
    box-shadow: 0 0 0 3px rgba(91, 91, 240, 0.18);
  }
  .search-shell input {
    flex: 1;
    width: 100%;
    min-width: 0;
    background: transparent;
    border: none;
    outline: none;
    color: var(--text);
    font-family: inherit;
    font-size: 14.5px;
    user-select: text;
  }
  .search-shell input::placeholder {
    color: #77778c;
  }
  .search-submit-btn {
    width: 38px;
    height: 38px;
    border-radius: 11px;
    background: transparent;
    border-color: transparent;
    color: #8b8ba0;
    padding: 0;
    flex-shrink: 0;
  }
  .search-shell:focus-within .search-submit-btn {
    color: #c7d2fe;
  }

  /* === 3. Squircle D-Pad + Transport Card (Matches Reference 1:1) === */
  .dpad-card {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 18px;
    padding: 22px 16px 18px;
  }

  .squircle-stage {
    perspective: 560px;
  }

  .dpad-squircle {
    width: 236px;
    height: 216px;
    border-radius: 66px;
    background: radial-gradient(circle at 50% 35%, #232330 0%, #191923 72%, #15151e 100%);
    border: 1px solid rgba(255, 255, 255, 0.085);
    box-shadow:
      inset 0 1px 1px rgba(255, 255, 255, 0.06),
      0 14px 34px rgba(0, 0, 0, 0.52);
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    will-change: transform;
    transform: translate3d(0, 0, 0) rotateX(0deg) rotateY(0deg);
    transition: transform 0.24s var(--spring), border-color 0.3s var(--smooth), box-shadow 0.3s var(--smooth);
  }

  .dpad-squircle.tilt-up    { transform: rotateX(7deg) scale(0.99); border-color: rgba(124,131,255,0.34); }
  .dpad-squircle.tilt-down  { transform: rotateX(-7deg) scale(0.99); border-color: rgba(124,131,255,0.34); }
  .dpad-squircle.tilt-left  { transform: rotateY(-7deg) scale(0.99); border-color: rgba(124,131,255,0.34); }
  .dpad-squircle.tilt-right { transform: rotateY(7deg) scale(0.99); border-color: rgba(124,131,255,0.34); }

  .dpad-dir {
    position: absolute;
    background: transparent;
    border: none;
    color: #d4d4e0;
    padding: 0;
    border-radius: 24px;
    box-shadow: none !important;
  }
  @media (hover: hover) {
    .dpad-dir:hover {
      background: rgba(255, 255, 255, 0.045);
      color: #fff;
      transform: scale(1.08);
    }
  }
  .dpad-dir:active {
    background: radial-gradient(circle, rgba(99, 102, 241, 0.26), transparent 72%);
    color: #fff;
    transform: scale(0.9) !important;
  }

  .dpad-up    { top: 8px; left: 50%; margin-left: -38px; width: 76px; height: 56px; }
  .dpad-down  { bottom: 8px; left: 50%; margin-left: -38px; width: 76px; height: 56px; }
  .dpad-left  { left: 8px; top: 50%; margin-top: -38px; width: 58px; height: 76px; }
  .dpad-right { right: 8px; top: 50%; margin-top: -38px; width: 58px; height: 76px; }

  .dpad-ok {
    width: 82px;
    height: 82px;
    border-radius: 50%;
    background: radial-gradient(circle at 50% 30%, #262636, #181822 80%);
    border: 2px solid rgba(99, 102, 241, 0.72);
    box-shadow:
      0 0 0 4px rgba(99, 102, 241, 0.11),
      0 0 22px rgba(91, 91, 240, 0.34),
      inset 0 1px 1px rgba(255, 255, 255, 0.12);
    font-size: 17px;
    font-weight: 700;
    letter-spacing: 0.03em;
    color: #fff;
    z-index: 2;
  }
  @media (hover: hover) {
    .dpad-ok:hover {
      border-color: #818cf8;
      box-shadow:
        0 0 0 6px rgba(99, 102, 241, 0.16),
        0 0 28px rgba(99, 102, 241, 0.52);
      transform: scale(1.03);
    }
  }
  .dpad-ok:active {
    transform: scale(0.91) !important;
    box-shadow:
      0 0 0 8px rgba(99, 102, 241, 0.24),
      0 0 32px rgba(124, 131, 255, 0.72) !important;
  }

  .pill-row-3 {
    width: 100%;
    display: grid;
    grid-template-columns: 1fr 1.32fr 1fr;
    gap: 10px;
  }

  .pill-btn {
    height: 44px;
    border-radius: 999px;
    background: var(--pill);
    border: 1px solid var(--border);
    font-size: 14px;
    font-weight: 600;
    color: #ededf4;
  }

  /* === 4. Audio & Volume Card (Thick Pill Slider + Sliding Segmented Toggle) === */
  .section-card {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .section-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .section-label {
    font-size: 11.5px;
    font-weight: 700;
    letter-spacing: 0.07em;
    text-transform: uppercase;
    color: var(--muted);
  }

  .section-value {
    font-size: 12.5px;
    font-weight: 700;
    color: #c7d2fe;
    font-variant-numeric: tabular-nums;
  }

  /* Thick Pill Volume Bar (Matches Reference + Gemini Code #2) */
  .vol-capsule {
    position: relative;
    width: 100%;
    height: 48px;
    border-radius: 999px;
    background: var(--track);
    border: 1px solid rgba(255, 255, 255, 0.065);
    padding: 4px;
    cursor: pointer;
    touch-action: none;
    overflow: hidden;
    box-shadow: inset 0 2px 6px rgba(0, 0, 0, 0.55);
    transition: border-color 0.25s var(--smooth), box-shadow 0.25s var(--smooth);
  }
  .vol-capsule.dragging {
    border-color: var(--glow-border);
    box-shadow: inset 0 2px 6px rgba(0, 0, 0, 0.55), 0 0 0 3px rgba(99, 102, 241, 0.2);
  }

  .vol-fill {
    position: relative;
    height: 100%;
    min-width: 40px;
    width: var(--vol-pct, 25%);
    border-radius: 999px;
    background: linear-gradient(90deg, var(--indigo-1) 0%, var(--indigo-2) 55%, var(--indigo-3) 100%);
    box-shadow: 0 2px 16px rgba(91, 91, 240, 0.48);
    will-change: width;
    transition: width 0.22s var(--spring), filter 0.3s var(--smooth);
    display: flex;
    align-items: center;
  }
  .vol-capsule.dragging .vol-fill {
    transition: none;
    filter: brightness(1.08);
  }
  .vol-capsule.is-muted .vol-fill {
    background: linear-gradient(90deg, #3f3f4e, #525266);
    box-shadow: none;
  }

  .vol-thumb-glow {
    position: absolute;
    right: 5px;
    top: 50%;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: rgba(255, 255, 255, 0.18);
    transform: translate3d(0, -50%, 0) scale(0.82);
    opacity: 0;
    transition: transform 0.22s var(--spring), opacity 0.22s var(--smooth);
    pointer-events: none;
  }
  .vol-capsule.dragging .vol-thumb-glow {
    opacity: 1;
    transform: translate3d(0, -50%, 0) scale(1.14);
    box-shadow: 0 0 16px rgba(255, 255, 255, 0.55);
  }

  .vol-icon-wrap {
    position: absolute;
    left: 16px;
    top: 50%;
    transform: translateY(-50%);
    color: #fff;
    z-index: 2;
    pointer-events: none;
    display: flex;
    align-items: center;
  }

  .pill-btn.muted-on {
    background: rgba(239, 68, 68, 0.18);
    border-color: rgba(248, 113, 113, 0.5);
    color: #fecaca;
    box-shadow: 0 0 18px rgba(239, 68, 68, 0.2);
  }

  /* === GPU-Accelerated Sliding Segmented Pill Controls === */
  .segmented {
    position: relative;
    display: grid;
    grid-template-columns: repeat(var(--cols, 2), 1fr);
    background: var(--track);
    border: 1px solid rgba(255, 255, 255, 0.065);
    border-radius: 999px;
    padding: 4px;
    overflow: hidden;
  }

  .seg-glider {
    position: absolute;
    top: 4px;
    bottom: 4px;
    left: 4px;
    width: calc((100% - 8px) / var(--cols, 2));
    border-radius: 999px;
    background: var(--seg-active);
    border: 1px solid rgba(255, 255, 255, 0.11);
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.45), inset 0 1px 0 rgba(255, 255, 255, 0.06);
    will-change: transform, opacity;
    transform: translate3d(calc(var(--idx, 0) * 100%), 0, 0);
    transition: transform 0.32s var(--spring), opacity 0.25s var(--smooth);
    pointer-events: none;
    z-index: 0;
  }
  .segmented.no-selection .seg-glider {
    opacity: 0;
  }

  .seg-btn {
    position: relative;
    z-index: 1;
    height: 38px;
    border-radius: 999px;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    font-size: 13.5px;
    font-weight: 600;
    color: var(--muted);
    padding: 0 6px;
    white-space: nowrap;
  }
  @media (hover: hover) {
    .seg-btn:hover {
      color: #d4d4e0;
      transform: none;
    }
  }
  .seg-btn.active {
    color: #fff;
    font-weight: 700;
  }

  /* === 6. Screen, TV & PC Power Card === */
  .dual-pills {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
  }

  .pill-btn.indigo-cta {
    background: linear-gradient(135deg, rgba(63, 55, 201, 0.42), rgba(91, 91, 240, 0.34));
    border-color: rgba(124, 131, 255, 0.44);
    color: #eef2ff;
  }
  .pill-btn.wake-glow {
    background: linear-gradient(135deg, rgba(245, 158, 11, 0.24), rgba(91, 91, 240, 0.22));
    border-color: rgba(251, 191, 36, 0.55);
    color: #fef3c7;
    box-shadow: 0 0 18px rgba(245, 158, 11, 0.22);
  }

  .power-row-btn {
    width: 100%;
    height: 46px;
    border-radius: 999px;
    background: rgba(239, 68, 68, 0.11);
    border: 1px solid rgba(248, 113, 113, 0.28);
    color: #fca5a5;
    font-weight: 700;
    font-size: 13.5px;
  }
  @media (hover: hover) {
    .power-row-btn:hover {
      background: rgba(239, 68, 68, 0.18);
      border-color: rgba(248, 113, 113, 0.5);
      box-shadow: 0 6px 20px -4px rgba(239, 68, 68, 0.28);
    }
  }
  .power-row-btn.countdown-active {
    background: rgba(239, 68, 68, 0.24);
    border-color: rgba(248, 113, 113, 0.65);
    color: #fff;
  }

  .sub-caption {
    font-size: 11px;
    font-weight: 600;
    color: var(--muted);
    margin-top: 2px;
    margin-bottom: -4px;
    display: flex;
    justify-content: space-between;
  }

  /* === Animated Modals (Settings + PC Power Sheet) === */
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(4, 4, 8, 0.74);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 16px;
    z-index: 150;
    opacity: 0;
    visibility: hidden;
    pointer-events: none;
    transition: opacity 0.26s var(--smooth), visibility 0.26s var(--smooth);
  }
  .modal-backdrop.open {
    opacity: 1;
    visibility: visible;
    pointer-events: auto;
  }

  .modal-card {
    width: 100%;
    max-width: 390px;
    background: #171721;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 24px;
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 14px;
    box-shadow: 0 24px 60px rgba(0, 0, 0, 0.65);
    transform: translate3d(0, 16px, 0) scale(0.95);
    transition: transform 0.3s var(--spring);
  }
  .modal-backdrop.open .modal-card {
    transform: translate3d(0, 0, 0) scale(1);
  }

  .modal-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 16px;
    font-weight: 700;
  }

  .close-x {
    width: 34px;
    height: 34px;
    border-radius: 10px;
    background: var(--pill);
    padding: 0;
    font-size: 14px;
  }

  .field-group {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .field-group label {
    font-size: 12px;
    color: var(--muted);
    font-weight: 600;
  }
  .field-group select, .field-group input[type="number"] {
    width: 100%;
    height: 44px;
    border-radius: 13px;
    border: 1px solid var(--border-hi);
    background: var(--track);
    color: var(--text);
    padding: 0 12px;
    font-size: 13.5px;
    outline: none;
    transition: border-color 0.25s var(--smooth);
  }
  .field-group select:focus, .field-group input:focus {
    border-color: var(--indigo-3);
  }

  .btn-save {
    height: 46px;
    border-radius: 999px;
    background: linear-gradient(135deg, var(--indigo-1), var(--indigo-2));
    border: 1px solid rgba(165, 180, 252, 0.35);
    color: #fff;
    font-weight: 700;
    box-shadow: 0 8px 22px rgba(91, 91, 240, 0.35);
  }

  .power-grid-timers {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 9px;
  }
  .btn-danger-now {
    height: 48px;
    border-radius: 999px;
    background: linear-gradient(135deg, #dc2626, #b91c1c);
    border: 1px solid rgba(252, 165, 165, 0.35);
    color: #fff;
    font-weight: 700;
    font-size: 14px;
    box-shadow: 0 8px 24px rgba(220, 38, 38, 0.34);
  }
  .btn-cancel-power {
    height: 44px;
    border-radius: 999px;
    background: rgba(16, 185, 129, 0.16);
    border: 1px solid rgba(52, 211, 153, 0.42);
    color: #6ee7b7;
    font-weight: 700;
    display: none;
  }
  .btn-cancel-power.show { display: inline-flex; }

  .power-note {
    font-size: 12px;
    color: var(--muted);
    line-height: 1.45;
  }

  /* === Toast === */
  .toast {
    position: fixed;
    bottom: calc(18px + env(safe-area-inset-bottom));
    left: 50%;
    transform: translate3d(-50%, 20px, 0) scale(0.96);
    background: rgba(24, 24, 35, 0.96);
    border: 1px solid rgba(124, 131, 255, 0.32);
    box-shadow: 0 12px 30px rgba(0, 0, 0, 0.55);
    color: #fff;
    padding: 9px 18px;
    border-radius: 999px;
    font-size: 12.5px;
    font-weight: 600;
    opacity: 0;
    pointer-events: none;
    transition: transform 0.25s var(--spring), opacity 0.22s var(--smooth);
    z-index: 220;
    max-width: 90vw;
    text-align: center;
  }
  .toast.show {
    opacity: 1;
    transform: translate3d(-50%, 0, 0) scale(1);
  }
</style>
</head>
<body>
  <div class="wrap">

    <!-- 1. Status Header Pill -->
    <header class="statusbar stagger" style="--i:0">
      <div class="status-left">
        <div id="orbWrap" class="orb-wrap">
          <div class="orb"></div>
        </div>
        <div class="status-titles">
          <div id="stMain" class="status-main">MODO PC <span class="host-part">• ...</span></div>
          <div id="stSub" class="status-sub">HTTPS Cloud</div>
        </div>
      </div>
      <div class="status-right">
        <button id="btnHeaderTv" class="tv-quick-pill" onclick="toggleTVQuick()">TV</button>
        <button class="gear-btn" onclick="openSettings()" aria-label="Configurações">
          <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.85">
            <circle cx="12" cy="12" r="3.2"></circle>
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.08a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.08a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
          </svg>
        </button>
      </div>
    </header>

    <!-- Contextual Quick Wake Banner (appears immediately whenever screen is turned off) -->
    <div id="wakeBanner" class="alert-pill wake-banner" onclick="monitorWake()">
      <span id="lblWakeBanner">☀️ Tela do PC apagada</span>
      <span class="cta" id="lblWakeBannerBtn">Ligar Tela</span>
    </div>

    <!-- Contextual PC Shutdown Countdown Banner -->
    <div id="shutdownBanner" class="alert-pill shutdown-banner">
      <span id="lblShutdownCountdown">⏻ Desligando PC em --:--</span>
      <button onclick="cancelPcShutdown()" id="lblCancelShutdownTop">Cancelar</button>
    </div>

    <!-- PWA Install Banner -->
    <div id="pwaBanner" class="alert-pill pwa-banner">
      <span id="lblPwaBanner">Instale na tela inicial p/ acesso rápido</span>
      <button id="btnInstallPwa">Instalar</button>
    </div>

    <!-- 2. Navigation & Search Row (Matches Reference 1:1) -->
    <div class="nav-search-row stagger" style="--i:1">
      <button class="sq-nav-btn" onclick="sendKey('back')" aria-label="Voltar">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M19 12H5"></path>
          <path d="M12 19l-7-7 7-7"></path>
        </svg>
      </button>

      <form class="search-shell" onsubmit="submitSearch(event)">
        <input id="searchInput" type="text" placeholder="Buscar" autocomplete="off">
        <button type="submit" class="search-submit-btn" aria-label="Ir">
          <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M5 12h14"></path>
            <path d="M13 6l6 6-6 6"></path>
          </svg>
        </button>
      </form>

      <button class="sq-nav-btn" onclick="sendKey('home')" aria-label="Início">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9">
          <path d="M3 10.5L12 3l9 7.5"></path>
          <path d="M5.5 9.5V20a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1V9.5"></path>
          <path d="M9.5 21v-6h5v6"></path>
        </svg>
      </button>
    </div>

    <!-- 3. Squircle D-Pad & Media Transport Card (Matches Reference 1:1) -->
    <section class="card dpad-card stagger" style="--i:2">
      <div class="squircle-stage">
        <div id="dpadPad" class="dpad-squircle">
          <button class="dpad-dir dpad-up" data-tilt="up" onclick="sendKey('up')" aria-label="Cima">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3">
              <path d="M18 15l-6-6-6 6"></path>
            </svg>
          </button>
          <button class="dpad-dir dpad-left" data-tilt="left" onclick="sendKey('left')" aria-label="Esquerda">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3">
              <path d="M15 18l-6-6 6-6"></path>
            </svg>
          </button>
          <button class="dpad-ok" onclick="sendKey('enter')">OK</button>
          <button class="dpad-dir dpad-right" data-tilt="right" onclick="sendKey('right')" aria-label="Direita">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3">
              <path d="M9 18l6-6-6-6"></path>
            </svg>
          </button>
          <button class="dpad-dir dpad-down" data-tilt="down" onclick="sendKey('down')" aria-label="Baixo">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3">
              <path d="M6 9l6 6 6-6"></path>
            </svg>
          </button>
        </div>
      </div>

      <div class="pill-row-3">
        <button class="pill-btn" onclick="sendKey('rw')">-10s</button>
        <button class="pill-btn" onclick="sendKey('space')">Play / Pause</button>
        <button class="pill-btn" onclick="sendKey('ff')">+10s</button>
      </div>
    </section>

    <!-- 4. Audio & Volume Card (Matches Reference 1:1) -->
    <section class="card section-card stagger" style="--i:3">
      <div class="section-head">
        <span id="lblAudioHead" class="section-label">ÁUDIO & VOLUME</span>
        <span id="volBadge" class="section-value">--%</span>
      </div>

      <div id="volCapsule" class="vol-capsule" role="slider" aria-valuemin="0" aria-valuemax="100" aria-valuenow="25">
        <div id="volFill" class="vol-fill" style="--vol-pct:25%">
          <div class="vol-thumb-glow"></div>
        </div>
        <div class="vol-icon-wrap" id="volIconWrap">
          <svg width="19" height="19" viewBox="0 0 24 24" fill="currentColor">
            <path d="M11 5L6 9H3a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1h3l5 4V5z"></path>
            <path d="M15.54 8.46a5 5 0 0 1 0 7.07" fill="none" stroke="currentColor" stroke-width="2"></path>
            <path d="M18.36 5.64a9 9 0 0 1 0 12.73" fill="none" stroke="currentColor" stroke-width="2"></path>
          </svg>
        </div>
      </div>

      <div class="pill-row-3">
        <button class="pill-btn" onclick="volStep(-5)">-5%</button>
        <button id="muteBtn" class="pill-btn" onclick="toggleMute()"><span id="lblMute">Mudo</span></button>
        <button class="pill-btn" onclick="volStep(5)">+5%</button>
      </div>

      <div id="audioSeg" class="segmented no-selection" style="--cols:2; --idx:0">
        <div class="seg-glider"></div>
        <button id="btnSoundbar" class="seg-btn" onclick="switchAudio('soundbar')">Soundbar / TV</button>
        <button id="btnHeadset" class="seg-btn" onclick="switchAudio('headset')"><span id="lblHeadset">Fone / PC</span></button>
      </div>
    </section>

    <!-- 5. Video Quality Card (Matches Reference 1:1) -->
    <section class="card section-card stagger" style="--i:4">
      <div class="section-head">
        <span id="lblQualityHead" class="section-label">QUALIDADE DO VÍDEO</span>
        <span id="qualBadge" class="section-value">1080p</span>
      </div>

      <div id="qualSeg" class="segmented" style="--cols:4; --idx:0">
        <div class="seg-glider"></div>
        <button id="q1080" class="seg-btn active" onclick="setQuality('1080')">1080p</button>
        <button id="qmax" class="seg-btn" onclick="setQuality('max')"><span id="lblQMax">Máx/4K</span></button>
        <button id="q720" class="seg-btn" onclick="setQuality('720')">720p</button>
        <button id="qauto" class="seg-btn" onclick="setQuality('auto')">Auto</button>
      </div>
    </section>

    <!-- 6. Screen, TV Mode, Sleep Timer & PC Shutdown Card -->
    <section class="card section-card stagger" style="--i:5">
      <div class="section-head">
        <span id="lblBedModeHead" class="section-label">TELA, TIMER & ENERGIA</span>
        <span id="sleepBadge" class="section-value">Off</span>
      </div>

      <!-- YouTube TV Open / Close -->
      <div class="dual-pills">
        <button class="pill-btn indigo-cta" onclick="openTV()">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="2" y="4" width="20" height="14" rx="2.5"></rect>
            <path d="M8 21h8"></path>
          </svg>
          <span id="lblOpenTv">Abrir TV</span>
        </button>
        <button class="pill-btn" onclick="closeTV()">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="5" y="5" width="14" height="14" rx="2"></rect>
          </svg>
          <span id="lblCloseTv">Fechar TV</span>
        </button>
      </div>

      <!-- Monitor Off & Monitor Wake (Dedicated Pair!) -->
      <div class="dual-pills">
        <button class="pill-btn" onclick="monitorOff()">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 12.79A9 9 0 1 1 11.21 3a7 7 0 0 0 9.79 9.79z"></path>
          </svg>
          <span id="lblScreenOff">Apagar Tela</span>
        </button>
        <button id="btnWakeScreen" class="pill-btn" onclick="monitorWake()">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="4"></circle>
            <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"></path>
          </svg>
          <span id="lblScreenOn">Ligar Tela</span>
        </button>
      </div>

      <div class="sub-caption">
        <span id="lblSleepSub">Sleep Timer (Fechar TV + Apagar Tela)</span>
      </div>
      <div id="sleepSeg" class="segmented" style="--cols:4; --idx:0">
        <div class="seg-glider"></div>
        <button id="sl0" class="seg-btn active" onclick="setSleep(0)">Off</button>
        <button id="sl30" class="seg-btn" onclick="setSleep(30)">30m</button>
        <button id="sl60" class="seg-btn" onclick="setSleep(60)">60m</button>
        <button id="sl90" class="seg-btn" onclick="setSleep(90)">90m</button>
      </div>

      <!-- PC Shutdown Button -->
      <button id="btnPowerModal" class="power-row-btn" onclick="openPowerModal()">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
          <path d="M12 2v10"></path>
          <path d="M18.36 6.64a9 9 0 1 1-12.73 0"></path>
        </svg>
        <span id="lblPowerPcBtn">Desligar PC quando cansar...</span>
      </button>
    </section>
  </div>

  <!-- Settings Modal -->
  <div id="settingsModal" class="modal-backdrop" onclick="if(event.target===this)closeSettings()">
    <div class="modal-card">
      <div class="modal-head">
        <span id="lblSettingsTitle">Configurações</span>
        <button class="close-x" onclick="closeSettings()">✕</button>
      </div>

      <div class="field-group">
        <label>Idioma / Language / Idioma</label>
        <div id="langSeg" class="segmented" style="--cols:3; --idx:0">
          <div class="seg-glider"></div>
          <button id="langPt" class="seg-btn active" onclick="setLang('pt')">PT</button>
          <button id="langEn" class="seg-btn" onclick="setLang('en')">EN</button>
          <button id="langEs" class="seg-btn" onclick="setLang('es')">ES</button>
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

  <!-- PC Shutdown Modal -->
  <div id="powerModal" class="modal-backdrop" onclick="if(event.target===this)closePowerModal()">
    <div class="modal-card">
      <div class="modal-head">
        <span id="lblPowerModalTitle">⏻ Desligar Computador</span>
        <button class="close-x" onclick="closePowerModal()">✕</button>
      </div>

      <div id="lblPowerNote" class="power-note">
        Antes de desligar o Windows, o BedTube fecha a TV e devolve o áudio para o Fone/PC para que seu computador ligue normal amanhã.
      </div>

      <button class="btn-danger-now" onclick="triggerPcShutdown('now', 0)">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
          <path d="M12 2v10"></path>
          <path d="M18.36 6.64a9 9 0 1 1-12.73 0"></path>
        </svg>
        <span id="lblShutdownNow">Desligar PC Agora (8s)</span>
      </button>

      <div class="field-group">
        <label id="lblShutdownTimerHead">Ou agendar desligamento (quando pegar no sono):</label>
        <div class="power-grid-timers">
          <button class="pill-btn" onclick="triggerPcShutdown('timer', 15)">⏱ 15 min</button>
          <button class="pill-btn" onclick="triggerPcShutdown('timer', 30)">⏱ 30 min</button>
          <button class="pill-btn" onclick="triggerPcShutdown('timer', 60)">⏱ 60 min</button>
          <button class="pill-btn" onclick="triggerPcShutdown('timer', 90)">⏱ 90 min</button>
        </div>
      </div>

      <button id="btnCancelPowerModal" class="btn-cancel-power" onclick="cancelPcShutdown()">
        <span id="lblCancelShutdownModal">Cancelar Desligamento Agendado</span>
      </button>
    </div>
  </div>

  <div id="toast" class="toast"></div>

<script>
const BOOT = __BEDTUBE_BOOT_JSON__;

const I18N = {
  pt: {
    tvModeUpper: 'MODO TV',
    pcModeUpper: 'MODO PC',
    muted: 'Mudo',
    offlineTitle: 'RECONECTANDO',
    offlineSub: 'Buscando Túnel HTTPS / Relay MQTT...',
    pwaBanner: 'Instale na tela inicial p/ usar sempre',
    install: 'Instalar',
    wakeBanner: '☀️ Tela do PC apagada',
    wakeBannerBtn: 'Ligar Tela',
    cancel: 'Cancelar',
    shuttingIn: '⏻ Desligando PC em',
    searchPh: 'Buscar',
    audioHead: 'ÁUDIO & VOLUME',
    mute: 'Mudo',
    headset: 'Fone / PC',
    qualityHead: 'QUALIDADE DO VÍDEO',
    qMax: 'Máx/4K',
    bedModeHead: 'TELA, TIMER & ENERGIA',
    openTv: 'Abrir TV',
    closeTv: 'Fechar TV',
    screenOff: 'Apagar Tela',
    screenOn: 'Ligar Tela',
    sleepSub: 'Sleep Timer (Fechar TV + Apagar Tela)',
    powerPcBtn: 'Desligar PC quando cansar...',
    powerModalTitle: '⏻ Desligar Computador',
    powerNote: 'Antes de desligar o Windows, o BedTube fecha a TV e devolve o áudio para o Fone/PC para que seu computador ligue normal amanhã.',
    shutdownNow: 'Desligar PC Agora (8s)',
    shutdownTimerHead: 'Ou agendar desligamento (quando dormir):',
    cancelShutdownModal: 'Cancelar Desligamento Agendado',
    settingsTitle: 'Configurações',
    tvDevice: 'Saída da TV / Soundbar (ao abrir TV)',
    pcDevice: 'Saída do Fone / PC (ao fechar TV)',
    nightVol: 'Volume Inicial ao Abrir TV (%)',
    saveBtn: 'Salvar Configurações',
    keepCurrent: '— Manter saída atual —'
  },
  en: {
    tvModeUpper: 'TV MODE',
    pcModeUpper: 'PC MODE',
    muted: 'Muted',
    offlineTitle: 'RECONNECTING',
    offlineSub: 'Trying HTTPS Tunnel / MQTT Relay...',
    pwaBanner: 'Add to Home Screen for instant access',
    install: 'Install',
    wakeBanner: '☀️ PC screen is turned off',
    wakeBannerBtn: 'Wake Screen',
    cancel: 'Cancel',
    shuttingIn: '⏻ PC shutting down in',
    searchPh: 'Search',
    audioHead: 'AUDIO & VOLUME',
    mute: 'Mute',
    headset: 'Headset / PC',
    qualityHead: 'VIDEO QUALITY',
    qMax: 'Max/4K',
    bedModeHead: 'SCREEN, TIMER & POWER',
    openTv: 'Open TV',
    closeTv: 'Close TV',
    screenOff: 'Screen Off',
    screenOn: 'Wake Screen',
    sleepSub: 'Sleep Timer (Close TV + Screen Off)',
    powerPcBtn: 'Shut Down PC when tired...',
    powerModalTitle: '⏻ Shut Down Computer',
    powerNote: 'Before shutting down Windows, BedTube closes TV Mode and restores your Headset/PC audio for your next boot.',
    shutdownNow: 'Shut Down PC Now (8s)',
    shutdownTimerHead: 'Or schedule PC shutdown:',
    cancelShutdownModal: 'Cancel Scheduled Shutdown',
    settingsTitle: 'Settings',
    tvDevice: 'TV / Soundbar Output (when TV opens)',
    pcDevice: 'Headset / PC Output (when TV closes)',
    nightVol: 'Initial Night Volume (%)',
    saveBtn: 'Save Settings',
    keepCurrent: '— Keep current output —'
  },
  es: {
    tvModeUpper: 'MODO TV',
    pcModeUpper: 'MODO PC',
    muted: 'Silencio',
    offlineTitle: 'RECONECTANDO',
    offlineSub: 'Buscando Túnel HTTPS / Relay MQTT...',
    pwaBanner: 'Instalar en pantalla de inicio',
    install: 'Instalar',
    wakeBanner: '☀️ Pantalla de PC apagada',
    wakeBannerBtn: 'Encender Pantalla',
    cancel: 'Cancelar',
    shuttingIn: '⏻ Apagando PC en',
    searchPh: 'Buscar',
    audioHead: 'AUDIO Y VOLUMEN',
    mute: 'Silencio',
    headset: 'Auriculares / PC',
    qualityHead: 'CALIDAD DE VIDEO',
    qMax: 'Máx/4K',
    bedModeHead: 'PANTALLA, TIMER Y ENERGÍA',
    openTv: 'Abrir TV',
    closeTv: 'Cerrar TV',
    screenOff: 'Apagar Pantalla',
    screenOn: 'Encender Pantalla',
    sleepSub: 'Temporizador (Cerrar TV + Apagar Pantalla)',
    powerPcBtn: 'Apagar PC al cansarse...',
    powerModalTitle: '⏻ Apagar Computadora',
    powerNote: 'Antes de apagar Windows, BedTube cierra el Modo TV y devuelve el audio a tus Auriculares/PC.',
    shutdownNow: 'Apagar PC Ahora (8s)',
    shutdownTimerHead: 'O programar apagado automático:',
    cancelShutdownModal: 'Cancelar Apagado Programado',
    settingsTitle: 'Ajustes',
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

function setSegIndex(segId, idx, hasSelection = true) {
  const seg = document.getElementById(segId);
  if (!seg) return;
  seg.classList.toggle('no-selection', !hasSelection);
  seg.style.setProperty('--idx', String(Math.max(0, idx)));
}

function applyLangUI() {
  document.getElementById('lblPwaBanner').textContent = t('pwaBanner');
  document.getElementById('btnInstallPwa').textContent = t('install');
  document.getElementById('lblWakeBanner').textContent = t('wakeBanner');
  document.getElementById('lblWakeBannerBtn').textContent = t('wakeBannerBtn');
  document.getElementById('lblCancelShutdownTop').textContent = t('cancel');
  document.getElementById('searchInput').placeholder = t('searchPh');
  document.getElementById('lblAudioHead').textContent = t('audioHead');
  document.getElementById('lblMute').textContent = t('mute');
  document.getElementById('lblHeadset').textContent = t('headset');
  document.getElementById('lblQualityHead').textContent = t('qualityHead');
  document.getElementById('lblQMax').textContent = t('qMax');
  document.getElementById('lblBedModeHead').textContent = t('bedModeHead');
  document.getElementById('lblOpenTv').textContent = t('openTv');
  document.getElementById('lblCloseTv').textContent = t('closeTv');
  document.getElementById('lblScreenOff').textContent = t('screenOff');
  document.getElementById('lblScreenOn').textContent = t('screenOn');
  document.getElementById('lblSleepSub').textContent = t('sleepSub');
  document.getElementById('lblPowerPcBtn').textContent = t('powerPcBtn');
  document.getElementById('lblPowerModalTitle').textContent = t('powerModalTitle');
  document.getElementById('lblPowerNote').textContent = t('powerNote');
  document.getElementById('lblShutdownNow').textContent = t('shutdownNow');
  document.getElementById('lblShutdownTimerHead').textContent = t('shutdownTimerHead');
  document.getElementById('lblCancelShutdownModal').textContent = t('cancelShutdownModal');
  document.getElementById('lblSettingsTitle').textContent = t('settingsTitle');
  document.getElementById('lblTvDevice').textContent = t('tvDevice');
  document.getElementById('lblPcDevice').textContent = t('pcDevice');
  document.getElementById('lblNightVol').textContent = t('nightVol');
  document.getElementById('lblSaveBtn').textContent = t('saveBtn');

  const langs = ['pt', 'en', 'es'];
  const lIdx = Math.max(0, langs.indexOf(curLang));
  setSegIndex('langSeg', lIdx, true);
  langs.forEach(l => {
    const btn = document.getElementById('lang' + l.charAt(0).toUpperCase() + l.slice(1));
    if (btn) btn.classList.toggle('active', l === curLang);
  });
}

function setLang(l) {
  if (!I18N[l]) return;
  curLang = l;
  localStorage.setItem('bedtube_lang', l);
  applyLangUI();
  if (lastState) renderState(lastState);
}

/* === D-Pad 3D Directional Tilt Feedback === */
const dpadPad = document.getElementById('dpadPad');
document.querySelectorAll('.dpad-dir').forEach(btn => {
  const dir = btn.getAttribute('data-tilt');
  const addTilt = () => dpadPad.classList.add('tilt-' + dir);
  const clearTilt = () => dpadPad.classList.remove('tilt-up', 'tilt-down', 'tilt-left', 'tilt-right');
  btn.addEventListener('pointerdown', addTilt);
  btn.addEventListener('pointerup', clearTilt);
  btn.addEventListener('pointerleave', clearTilt);
  btn.addEventListener('pointercancel', clearTilt);
});

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
let localShutdownTargetMs = null;

let toastTimer = null;
let draggingSlider = false;
let currentSliderVol = 25;

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

function buzz(ms = 18) {
  if (navigator.vibrate) navigator.vibrate(ms);
}

function showToast(msg) {
  if (!msg) return;
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2200);
}

function getConnLabel() {
  if (httpAlive) return apiBase.includes('trycloudflare') ? 'HTTPS Cloud' : 'Wi-Fi Local';
  if (mqttReady) return 'Relay MQTT';
  return 'Offline';
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
    return data;
  } catch (err) {
    clearTimeout(timer);
    httpAlive = false;
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
  buzz(18);
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
    showToast(t('offlineSub'));
    return null;
  }
}

function openTV() { dispatch('/iniciar', 'iniciar'); }
function closeTV() { dispatch('/fechar', 'fechar'); }
function toggleTVQuick() {
  if (lastState && lastState.tv_open) closeTV();
  else openTV();
}
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
function switchAudio(target) { dispatch('/audio/' + target, 'audio_' + target); }
function monitorOff() { dispatch('/monitor/off', 'monitor_off'); }
function monitorWake() { dispatch('/monitor/wake', 'monitor_wake'); }
function setSleep(m) { dispatch('/sleep/' + m, 'sleep', { minutos: m }); }
function setQuality(q) { dispatch('/qualidade/' + q, 'qualidade:' + q); }

/* === Thick Pill Interactive Volume Slider === */
const volCapsule = document.getElementById('volCapsule');
const volFill = document.getElementById('volFill');

function setVisualVolume(pct, muted = false) {
  const clamped = Math.max(0, Math.min(100, Math.round(pct)));
  currentSliderVol = clamped;
  volFill.style.setProperty('--vol-pct', clamped + '%');
  volCapsule.setAttribute('aria-valuenow', String(clamped));
  volCapsule.classList.toggle('is-muted', !!muted);
  document.getElementById('volBadge').textContent = muted ? t('muted') : (clamped + '%');
  const iconWrap = document.getElementById('volIconWrap');
  if (muted || clamped === 0) {
    iconWrap.innerHTML = `<svg width="19" height="19" viewBox="0 0 24 24" fill="currentColor"><path d="M11 5L6 9H3a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1h3l5 4V5z"></path><path d="M22 9l-6 6M16 9l6 6" fill="none" stroke="currentColor" stroke-width="2.2"></path></svg>`;
  } else {
    iconWrap.innerHTML = `<svg width="19" height="19" viewBox="0 0 24 24" fill="currentColor"><path d="M11 5L6 9H3a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1h3l5 4V5z"></path><path d="M15.54 8.46a5 5 0 0 1 0 7.07" fill="none" stroke="currentColor" stroke-width="2"></path><path d="M18.36 5.64a9 9 0 0 1 0 12.73" fill="none" stroke="currentColor" stroke-width="2"></path></svg>`;
  }
}

function calcVolFromPointer(e) {
  const rect = volCapsule.getBoundingClientRect();
  const pad = 4;
  const x = Math.max(0, Math.min(rect.width - pad * 2, e.clientX - rect.left - pad));
  return Math.round((x / Math.max(1, rect.width - pad * 2)) * 100);
}

volCapsule.addEventListener('pointerdown', (e) => {
  draggingSlider = true;
  volCapsule.classList.add('dragging');
  volCapsule.setPointerCapture(e.pointerId);
  setVisualVolume(calcVolFromPointer(e), false);
});
volCapsule.addEventListener('pointermove', (e) => {
  if (!draggingSlider) return;
  setVisualVolume(calcVolFromPointer(e), false);
});
function finishVolumeDrag(e) {
  if (!draggingSlider) return;
  draggingSlider = false;
  volCapsule.classList.remove('dragging');
  const val = currentSliderVol;
  dispatch('/volume/set/' + val, 'vol_set', { nivel: val });
}
volCapsule.addEventListener('pointerup', finishVolumeDrag);
volCapsule.addEventListener('pointercancel', finishVolumeDrag);

/* === PC Shutdown Modal & Live Countdown === */
function openPowerModal() {
  buzz(16);
  document.getElementById('powerModal').classList.add('open');
}
function closePowerModal() {
  document.getElementById('powerModal').classList.remove('open');
}
async function triggerPcShutdown(mode, minutes) {
  buzz(30);
  closePowerModal();
  await dispatch('/pc/shutdown', 'pc_shutdown', { mode, minutes });
}
async function cancelPcShutdown() {
  buzz(20);
  closePowerModal();
  await dispatch('/pc/shutdown/cancel', 'pc_shutdown_cancel', { mode: 'cancel' });
}

function formatSec(sec) {
  if (sec <= 59) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function refreshShutdownUI() {
  const banner = document.getElementById('shutdownBanner');
  const modalCancel = document.getElementById('btnCancelPowerModal');
  const powerBtn = document.getElementById('btnPowerModal');
  if (!localShutdownTargetMs) {
    banner.classList.remove('show');
    modalCancel.classList.remove('show');
    powerBtn.classList.remove('countdown-active');
    document.getElementById('lblPowerPcBtn').textContent = t('powerPcBtn');
    return;
  }
  const remSec = Math.max(0, Math.round((localShutdownTargetMs - Date.now()) / 1000));
  banner.classList.add('show');
  modalCancel.classList.add('show');
  powerBtn.classList.add('countdown-active');
  const txt = `${t('shuttingIn')} ${formatSec(remSec)}`;
  document.getElementById('lblShutdownCountdown').textContent = txt;
  document.getElementById('lblPowerPcBtn').textContent = txt;
}
setInterval(refreshShutdownUI, 1000);

/* === Settings Modal === */
async function openSettings() {
  buzz(14);
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

  const pickBestDeviceName = (list, query) => {
    if (!query) return '';
    const q = query.toLowerCase().trim();
    const exact = list.find(d => d.name.toLowerCase() === q);
    if (exact) return exact.name;
    const matches = list.filter(d => d.name.toLowerCase().includes(q));
    if (!matches.length) return '';
    if (!q.includes('spdif') && !q.includes('s/pdif')) {
      const analog = matches.filter(d => !d.name.toLowerCase().includes('s/pdif') && !d.name.toLowerCase().includes('spdif'));
      if (analog.length) return (analog.find(d => d.is_default) || analog[0]).name;
    }
    return (matches.find(d => d.is_default) || matches[0]).name;
  };

  const fillSelect = (selId, selectedVal) => {
    const sel = document.getElementById(selId);
    sel.innerHTML = '';
    const emptyOpt = document.createElement('option');
    emptyOpt.value = '';
    emptyOpt.textContent = t('keepCurrent');
    sel.appendChild(emptyOpt);
    const bestName = pickBestDeviceName(devs, selectedVal);
    devs.forEach(d => {
      const opt = document.createElement('option');
      opt.value = d.name;
      opt.textContent = d.name;
      if (bestName && d.name === bestName) {
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

/* === Render Synchronized State === */
function renderState(s) {
  if (!s || !s.ok) return;
  lastState = s;

  if (s.tunnel_url && s.tunnel_url !== apiBase) {
    apiBase = s.tunnel_url;
    localStorage.setItem('bedtube_api_base', apiBase);
  }

  const orbWrap = document.getElementById('orbWrap');
  const stMain = document.getElementById('stMain');
  const stSub = document.getElementById('stSub');
  const host = s.hostname || 'PC';
  const modeTxt = s.tv_open ? t('tvModeUpper') : t('pcModeUpper');

  orbWrap.classList.remove('offline');
  orbWrap.classList.toggle('tv-active', !!s.tv_open);
  stMain.innerHTML = `${modeTxt} <span class="host-part">• ${host}</span>`;
  stSub.textContent = getConnLabel();

  const headerTv = document.getElementById('btnHeaderTv');
  headerTv.classList.toggle('tv-is-open', !!s.tv_open);
  headerTv.textContent = s.tv_open ? 'TV ON' : 'TV';

  // Screen off indicator + dedicated Wake Screen highlight
  const isScreenOff = !!s.screen_off;
  document.getElementById('wakeBanner').classList.toggle('show', isScreenOff);
  document.getElementById('btnWakeScreen').classList.toggle('wake-glow', isScreenOff);

  // PC Shutdown Countdown sync
  if (typeof s.pc_shutdown_remaining_sec === 'number' && s.pc_shutdown_remaining_sec > 0) {
    localShutdownTargetMs = Date.now() + (s.pc_shutdown_remaining_sec * 1000);
  } else {
    localShutdownTargetMs = null;
  }
  refreshShutdownUI();

  // Volume & Mute
  if (!draggingSlider && typeof s.volume === 'number') {
    setVisualVolume(s.volume, !!s.muted);
  }
  document.getElementById('muteBtn').classList.toggle('muted-on', !!s.muted);

  // Segmented Audio Output Glider
  const isSb = s.active_profile === 'soundbar';
  const isHs = s.active_profile === 'headset';
  document.getElementById('btnSoundbar').classList.toggle('active', isSb);
  document.getElementById('btnHeadset').classList.toggle('active', isHs);
  if (isSb) setSegIndex('audioSeg', 0, true);
  else if (isHs) setSegIndex('audioSeg', 1, true);
  else setSegIndex('audioSeg', 0, false);

  // Segmented Video Quality Glider
  const pq = s.preferred_quality || '1080';
  const qOrder = ['1080', 'max', '720', 'auto'];
  const qLabels = { '1080': '1080p', 'max': t('qMax'), '720': '720p', 'auto': 'Auto' };
  document.getElementById('qualBadge').textContent = qLabels[pq] || pq;
  const qIdx = Math.max(0, qOrder.indexOf(pq));
  setSegIndex('qualSeg', qIdx, true);
  qOrder.forEach(q => {
    const el = document.getElementById('q' + q);
    if (el) el.classList.toggle('active', pq === q);
  });

  // Segmented Sleep Timer Glider
  const rem = s.sleep_remaining_min;
  document.getElementById('sleepBadge').textContent = rem ? `${rem} min` : 'Off';
  let slIdx = 0;
  if (rem) {
    if (rem <= 45) slIdx = 1;
    else if (rem <= 75) slIdx = 2;
    else slIdx = 3;
  }
  setSegIndex('sleepSeg', slIdx, true);
  [0, 30, 60, 90].forEach((m, i) => {
    const btn = document.getElementById('sl' + m);
    if (btn) btn.classList.toggle('active', i === slIdx);
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
      cli.subscribe(`bedtube/v1/${TOPIC_ID}/state`, { qos: 1 });
      cli.subscribe(`bedtube/v1/${TOPIC_ID}/ack`, { qos: 1 });
      sendViaMqtt('ping').then(renderState).catch(() => {});
    });
    cli.on('close', () => {
      mqttReady = false;
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
      document.getElementById('orbWrap').className = 'orb-wrap offline';
      document.getElementById('stMain').textContent = t('offlineTitle');
      document.getElementById('stSub').textContent = t('offlineSub');
    }
  }
}

initMqttRelay();
pollLoop();
setInterval(pollLoop, 3200);
</script>
</body>
</html>
"""

HTML_DESKTOP_HUB = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BedTube Controller v__VERSION__ — Hub & QR Code</title>
<link rel="icon" type="image/png" href="/icon-192.png">
<style>
  :root {
    --bg: #0b0b10;
    --card: #16161e;
    --pill: #23232f;
    --pill-hover: #2b2b3b;
    --track: #0d0d13;
    --seg-active: #272735;
    --border: rgba(255, 255, 255, 0.08);
    --glow-border: rgba(124, 131, 255, 0.45);
    --text: #f4f4f8;
    --muted: #8b8ba0;
    --indigo-1: #3f37c9;
    --indigo-2: #5b5bf0;
    --indigo-3: #7c83ff;
    --spring: cubic-bezier(0.22, 1, 0.36, 1);
    --smooth: cubic-bezier(0.4, 0, 0.2, 1);
  }

  * { box-sizing: border-box; }

  body {
    margin: 0;
    min-height: 100vh;
    background-color: var(--bg);
    background-image:
      radial-gradient(ellipse 60% 50% at 20% 0%, rgba(91, 91, 240, 0.12), transparent 65%),
      radial-gradient(ellipse 50% 45% at 85% 100%, rgba(63, 55, 201, 0.1), transparent 65%);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, system-ui, sans-serif;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 28px 20px;
    -webkit-font-smoothing: antialiased;
  }

  @keyframes btHubIn {
    0% { opacity: 0; transform: translate3d(0, 20px, 0) scale(0.985); }
    100% { opacity: 1; transform: translate3d(0, 0, 0) scale(1); }
  }

  .stagger {
    will-change: transform, opacity;
    animation: btHubIn 0.48s var(--spring) both;
    animation-delay: calc(var(--i, 0) * 70ms);
  }

  .hub-shell {
    max-width: 940px;
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  /* Top Status Bar matching mobile reference */
  .top-pill {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 14px 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.04), 0 14px 36px rgba(0,0,0,0.4);
  }

  .brand-left {
    display: flex;
    align-items: center;
    gap: 14px;
  }
  .orb-wrap {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: rgba(99, 102, 241, 0.16);
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .orb {
    width: 11px;
    height: 11px;
    border-radius: 50%;
    background: #6366f1;
    box-shadow: 0 0 14px 2px rgba(99, 102, 241, 0.9);
    transition: all 0.3s var(--smooth);
  }
  .brand-titles h1 {
    margin: 0;
    font-size: 16.5px;
    font-weight: 700;
    display: flex;
    align-items: center;
    gap: 9px;
  }
  .ver-tag {
    font-size: 11px;
    padding: 2px 9px;
    border-radius: 999px;
    background: rgba(91, 91, 240, 0.18);
    border: 1px solid rgba(124, 131, 255, 0.35);
    color: #c7d2fe;
    font-weight: 700;
  }
  .brand-sub {
    font-size: 12.5px;
    color: var(--muted);
    margin-top: 2px;
  }

  .hub-grid {
    display: grid;
    grid-template-columns: 355px 1fr;
    gap: 18px;
  }
  @media (max-width: 820px) {
    .hub-grid { grid-template-columns: 1fr; }
  }

  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 24px;
    padding: 22px;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.035), 0 20px 50px rgba(0,0,0,0.48);
    transform: translate3d(0, 0, 0);
    transition: transform 0.28s var(--spring), border-color 0.3s var(--smooth), box-shadow 0.3s var(--smooth);
  }
  .card:hover {
    border-color: rgba(255, 255, 255, 0.125);
    transform: translate3d(0, -2px, 0);
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.05), 0 26px 58px rgba(0,0,0,0.56);
  }

  button {
    appearance: none;
    border: 1px solid var(--border);
    background: var(--pill);
    color: var(--text);
    border-radius: 999px;
    font-family: inherit;
    font-weight: 600;
    font-size: 13.5px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 7px;
    will-change: transform;
    transform: translate3d(0, 0, 0) scale(1);
    transition:
      transform 0.2s var(--spring),
      background-color 0.3s var(--smooth),
      border-color 0.3s var(--smooth),
      color 0.3s var(--smooth),
      box-shadow 0.3s var(--smooth);
  }
  button:hover {
    background: var(--pill-hover);
    border-color: var(--glow-border);
    box-shadow: 0 6px 18px -4px rgba(91, 91, 240, 0.28);
    transform: translate3d(0, -1.5px, 0);
  }
  button:active {
    transform: translate3d(0, 0, 0) scale(0.955) !important;
    transition-duration: 0.08s;
  }

  /* Segmented Sliding Pill */
  .segmented {
    position: relative;
    display: grid;
    grid-template-columns: repeat(var(--cols, 2), 1fr);
    background: var(--track);
    border: 1px solid rgba(255, 255, 255, 0.065);
    border-radius: 999px;
    padding: 4px;
    overflow: hidden;
  }
  .seg-glider {
    position: absolute;
    top: 4px;
    bottom: 4px;
    left: 4px;
    width: calc((100% - 8px) / var(--cols, 2));
    border-radius: 999px;
    background: var(--seg-active);
    border: 1px solid rgba(255, 255, 255, 0.11);
    box-shadow: 0 4px 12px rgba(0,0,0,0.45);
    will-change: transform, opacity;
    transform: translate3d(calc(var(--idx, 0) * 100%), 0, 0);
    transition: transform 0.32s var(--spring), opacity 0.25s var(--smooth);
    pointer-events: none;
  }
  .segmented.no-selection .seg-glider { opacity: 0; }
  .seg-btn {
    position: relative;
    z-index: 1;
    height: 36px;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    font-size: 12.5px;
    color: var(--muted);
    padding: 0 12px;
  }
  .seg-btn:hover { color: #e4e4ee; transform: none; }
  .seg-btn.active { color: #fff; font-weight: 700; }

  /* QR Column */
  .qr-col {
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    gap: 13px;
  }
  .section-tag {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
  }
  .qr-frame {
    background: #12121a;
    padding: 14px;
    border-radius: 22px;
    border: 1px solid rgba(124, 131, 255, 0.26);
    box-shadow: 0 0 32px rgba(91, 91, 240, 0.16), inset 0 1px 0 rgba(255,255,255,0.05);
    transition: transform 0.3s var(--spring), box-shadow 0.3s var(--smooth), border-color 0.3s var(--smooth);
  }
  .qr-frame:hover {
    transform: scale(1.02);
    border-color: rgba(124, 131, 255, 0.52);
    box-shadow: 0 0 40px rgba(91, 91, 240, 0.28);
  }
  .qr-frame img {
    width: 214px;
    height: 214px;
    display: block;
    border-radius: 10px;
  }

  .url-pill {
    width: 100%;
    background: var(--track);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 9px 12px;
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 11.5px;
    color: #a5b4fc;
    word-break: break-all;
    cursor: pointer;
    transition: border-color 0.25s var(--smooth), background-color 0.25s var(--smooth);
  }
  .url-pill:hover {
    border-color: var(--glow-border);
    background: #13131d;
  }

  .quick-grid-2 {
    width: 100%;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  .quick-btn {
    height: 40px;
    font-size: 12.5px;
  }
  .quick-btn.wake-glow {
    border-color: rgba(251, 191, 36, 0.55);
    color: #fde68a;
    background: rgba(245, 158, 11, 0.16);
  }

  .qr-hint {
    font-size: 12px;
    color: var(--muted);
    line-height: 1.48;
  }

  /* Settings Column */
  .settings-col {
    display: flex;
    flex-direction: column;
    gap: 15px;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 7px;
  }
  .field-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .field label {
    font-size: 11.5px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--muted);
  }
  .field select {
    width: 100%;
    height: 44px;
    background: var(--track);
    color: var(--text);
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 14px;
    padding: 0 13px;
    font-size: 13.5px;
    outline: none;
    transition: border-color 0.25s var(--smooth), box-shadow 0.25s var(--smooth);
  }
  .field select:focus {
    border-color: var(--glow-border);
    box-shadow: 0 0 0 3px rgba(91, 91, 240, 0.16);
  }

  /* Thick Pill Night Volume Slider */
  .vol-capsule {
    position: relative;
    width: 100%;
    height: 44px;
    border-radius: 999px;
    background: var(--track);
    border: 1px solid rgba(255, 255, 255, 0.07);
    padding: 4px;
    cursor: pointer;
    user-select: none;
    touch-action: none;
    overflow: hidden;
  }
  .vol-fill {
    height: 100%;
    min-width: 36px;
    width: var(--vol-pct, 25%);
    border-radius: 999px;
    background: linear-gradient(90deg, var(--indigo-1), var(--indigo-2), var(--indigo-3));
    box-shadow: 0 2px 14px rgba(91, 91, 240, 0.45);
    transition: width 0.2s var(--spring);
  }
  .vol-capsule.dragging .vol-fill { transition: none; }
  .vol-icon {
    position: absolute;
    left: 15px;
    top: 50%;
    transform: translateY(-50%);
    pointer-events: none;
    color: #fff;
    display: flex;
  }

  /* Modern Toggle Switch */
  .switch-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    background: var(--track);
    border: 1px solid rgba(255,255,255,0.065);
    border-radius: 15px;
    padding: 11px 14px;
    cursor: pointer;
  }
  .switch-row span {
    font-size: 13px;
    font-weight: 500;
    color: #e4e4ee;
  }
  .toggle-track {
    width: 44px;
    height: 25px;
    border-radius: 999px;
    background: #272733;
    padding: 3px;
    transition: background-color 0.3s var(--smooth);
    flex-shrink: 0;
  }
  .toggle-thumb {
    width: 19px;
    height: 19px;
    border-radius: 50%;
    background: #fff;
    transform: translate3d(0, 0, 0);
    transition: transform 0.28s var(--spring);
  }
  .switch-row.checked .toggle-track {
    background: var(--indigo-2);
  }
  .switch-row.checked .toggle-thumb {
    transform: translate3d(19px, 0, 0);
  }

  .cta-row {
    display: grid;
    grid-template-columns: 1.25fr 1fr;
    gap: 10px;
    margin-top: 2px;
  }
  .btn-save {
    height: 46px;
    background: linear-gradient(135deg, var(--indigo-1), var(--indigo-2));
    border-color: rgba(165, 180, 252, 0.38);
    color: #fff;
    font-weight: 700;
    box-shadow: 0 8px 24px rgba(91, 91, 240, 0.34);
  }
  .btn-tv {
    height: 46px;
    background: #242433;
    border-color: rgba(124, 131, 255, 0.3);
    color: #e0e7ff;
    font-weight: 700;
  }

  .status-feedback {
    min-height: 18px;
    font-size: 12.5px;
    font-weight: 600;
    color: #818cf8;
  }
</style>
</head>
<body>
  <div class="hub-shell">

    <!-- Top Header Pill -->
    <header class="top-pill stagger" style="--i:0">
      <div class="brand-left">
        <div class="orb-wrap"><div id="hubOrb" class="orb"></div></div>
        <div class="brand-titles">
          <h1>
            <span id="hubModeHost">MODO PC • Computador</span>
            <span class="ver-tag">v__VERSION__</span>
          </h1>
          <div id="tunnelBadge" class="brand-sub">Iniciando Túnel HTTPS Cloud...</div>
        </div>
      </div>

      <div id="hubLangSeg" class="segmented" style="--cols:3; --idx:0; width: 162px;">
        <div class="seg-glider"></div>
        <button id="bPt" class="seg-btn active" onclick="setHubLang('pt')">PT</button>
        <button id="bEn" class="seg-btn" onclick="setHubLang('en')">EN</button>
        <button id="bEs" class="seg-btn" onclick="setHubLang('es')">ES</button>
      </div>
    </header>

    <div class="hub-grid">
      <!-- Left Card: QR Pairing & Quick Controls -->
      <section class="card qr-col stagger" style="--i:1">
        <div id="scanSub" class="section-tag">PAREAR CELULAR (QR CODE)</div>

        <div class="qr-frame">
          <img id="qrImg" src="/qr.png" alt="QR Code BedTube">
        </div>

        <div id="pairUrl" class="url-pill" onclick="copyPairUrl()" title="Clique para copiar">Carregando link...</div>

        <div id="hubAudioSeg" class="segmented no-selection" style="--cols:2; --idx:0; width:100%;">
          <div class="seg-glider"></div>
          <button id="hubBtnSb" class="seg-btn" onclick="hubAction('audio_soundbar')">Soundbar / TV</button>
          <button id="hubBtnHs" class="seg-btn" onclick="hubAction('audio_headset')"><span id="lblHubHs">Fone / PC</span></button>
        </div>

        <div class="quick-grid-2">
          <button class="quick-btn" onclick="hubAction('monitor_off')">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px;margin-right:4px">
              <path d="M21 12.79A9 9 0 1 1 11.21 3a7 7 0 0 0 9.79 9.79z"></path>
            </svg>
            <span id="lblHubOff">Apagar Tela</span>
          </button>
          <button id="hubWakeBtn" class="quick-btn" onclick="hubAction('monitor_wake')">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px;margin-right:4px">
              <circle cx="12" cy="12" r="4"></circle>
              <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"></path>
            </svg>
            <span id="lblHubWake">Ligar Tela</span>
          </button>
        </div>

        <div id="qrHint" class="qr-hint">
          Funciona com <b>PC no Cabo + Celular no Wi-Fi</b>.<br>
          Escaneie e toque em <b>Adicionar à Tela Inicial</b>!
        </div>
      </section>

      <!-- Right Card: Audio, Quality & System Settings -->
      <section class="card settings-col stagger" style="--i:2">
        <div class="field">
          <label id="lblSb">SAÍDA DE ÁUDIO DO MODO TV (SOUNDBAR / TV HDMI)</label>
          <select id="selSb"></select>
        </div>

        <div class="field">
          <label id="lblHs">SAÍDA PADRÃO DO PC (FONE / HEADSET AO FECHAR TV)</label>
          <select id="selHs"></select>
        </div>

        <div class="field">
          <div class="field-head">
            <label id="lblVol">VOLUME NOTURNO INICIAL AO ABRIR A TV</label>
            <span id="hubVolVal" style="font-size:13px;font-weight:700;color:#c7d2fe;">25%</span>
          </div>
          <div id="hubVolCapsule" class="vol-capsule">
            <div id="hubVolFill" class="vol-fill" style="--vol-pct:25%"></div>
            <div class="vol-icon">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor">
                <path d="M11 5L6 9H3a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1h3l5 4V5z"></path>
                <path d="M15.54 8.46a5 5 0 0 1 0 7.07" fill="none" stroke="currentColor" stroke-width="2"></path>
              </svg>
            </div>
          </div>
        </div>

        <div class="field">
          <label id="lblQual">QUALIDADE PADRÃO DO VÍDEO (YOUTUBE TV)</label>
          <div id="hubQualSeg" class="segmented" style="--cols:4; --idx:0">
            <div class="seg-glider"></div>
            <button id="hq1080" class="seg-btn active" onclick="pickHubQual('1080')">1080p</button>
            <button id="hqmax" class="seg-btn" onclick="pickHubQual('max')">Máx/4K</button>
            <button id="hq720" class="seg-btn" onclick="pickHubQual('720')">720p</button>
            <button id="hqauto" class="seg-btn" onclick="pickHubQual('auto')">Auto</button>
          </div>
        </div>

        <div id="autostartRow" class="switch-row checked" onclick="toggleAutostartSwitch()">
          <span id="lblAuto">Iniciar silenciosamente junto com o Windows</span>
          <div class="toggle-track"><div class="toggle-thumb"></div></div>
        </div>

        <div id="saveMsg" class="status-feedback"></div>

        <div class="cta-row">
          <button id="btnSave" class="btn-save" onclick="saveHubSettings()">Salvar Configurações</button>
          <button id="btnTestTv" class="btn-tv" onclick="testOpenTv()">Abrir Modo TV</button>
        </div>
      </section>
    </div>
  </div>

<script>
const TXT = {
  pt: {
    tvMode: 'MODO TV',
    pcMode: 'MODO PC',
    cloudOk: 'HTTPS Cloud Ativo • Cabo ↔ Wi-Fi Liberado',
    cloudWait: 'Conectando Túnel HTTPS Seguro...',
    scanSub: 'PAREAR CELULAR (QR CODE)',
    qrHint: 'Funciona com <b>PC no Cabo + Celular no Wi-Fi</b> sem mexer no roteador.<br>No celular, toque em <b>Adicionar à Tela Inicial</b>!',
    lblHubHs: 'Fone / PC',
    lblHubOff: 'Apagar Tela',
    lblHubWake: 'Ligar Tela',
    lblSb: 'SAÍDA DE ÁUDIO DO MODO TV (SOUNDBAR / TV HDMI)',
    lblHs: 'SAÍDA PADRÃO DO PC (FONE / HEADSET AO FECHAR TV)',
    lblVol: 'VOLUME NOTURNO INICIAL AO ABRIR A TV',
    lblQual: 'QUALIDADE PADRÃO DO VÍDEO (YOUTUBE TV)',
    lblAuto: 'Iniciar silenciosamente junto com o Windows',
    btnSave: 'Salvar Configurações',
    btnTestTv: 'Abrir Modo TV',
    btnCloseTv: 'Fechar Modo TV',
    keepCur: '— Manter saída de áudio atual —',
    saved: '✓ Configurações salvas!',
    copied: '✓ Link copiado para a área de transferência!'
  },
  en: {
    tvMode: 'TV MODE',
    pcMode: 'PC MODE',
    cloudOk: 'HTTPS Cloud Active • Wired PC ↔ Wi-Fi Ready',
    cloudWait: 'Starting Secure HTTPS Tunnel...',
    scanSub: 'PAIR PHONE (QR CODE)',
    qrHint: 'Works across <b>Wired PC + Wi-Fi Phone</b> with zero router setup.<br>Tap <b>Add to Home Screen</b> on your phone!',
    lblHubHs: 'Headset / PC',
    lblHubOff: 'Screen Off',
    lblHubWake: 'Wake Screen',
    lblSb: 'TV MODE AUDIO OUTPUT (SOUNDBAR / HDMI TV)',
    lblHs: 'DEFAULT PC AUDIO OUTPUT (HEADSET WHEN TV CLOSES)',
    lblVol: 'INITIAL NIGHT VOLUME WHEN TV OPENS',
    lblQual: 'DEFAULT VIDEO QUALITY (YOUTUBE TV)',
    lblAuto: 'Start silently with Windows (System Tray)',
    btnSave: 'Save Settings',
    btnTestTv: 'Open TV Mode',
    btnCloseTv: 'Close TV Mode',
    keepCur: '— Keep current audio output —',
    saved: '✓ Settings saved!',
    copied: '✓ Pairing link copied to clipboard!'
  },
  es: {
    tvMode: 'MODO TV',
    pcMode: 'MODO PC',
    cloudOk: 'HTTPS Cloud Activo • Cable ↔ Wi-Fi Listo',
    cloudWait: 'Iniciando Túnel HTTPS Seguro...',
    scanSub: 'VINCULAR CELULAR (CÓDIGO QR)',
    qrHint: 'Funciona con <b>PC por Cable + Móvil en Wi-Fi</b>.<br>¡Toca <b>Añadir a pantalla de inicio</b> en tu celular!',
    lblHubHs: 'Auriculares / PC',
    lblHubOff: 'Apagar Pantalla',
    lblHubWake: 'Encender Pantalla',
    lblSb: 'SALIDA DE AUDIO MODO TV (BARRA DE SONIDO / TV HDMI)',
    lblHs: 'SALIDA DE AUDIO DE PC (AURICULARES AL CERRAR TV)',
    lblVol: 'VOLUMEN NOCTURNO INICIAL AL ABRIR TV',
    lblQual: 'CALIDAD DE VIDEO PREDETERMINADA',
    lblAuto: 'Iniciar silenciosamente con Windows',
    btnSave: 'Guardar Ajustes',
    btnTestTv: 'Abrir Modo TV',
    btnCloseTv: 'Cerrar Modo TV',
    keepCur: '— Mantener salida actual —',
    saved: '✓ ¡Ajustes guardados!',
    copied: '✓ ¡Enlace copiado al portapapeles!'
  }
};

let lang = localStorage.getItem('bedtube_hub_lang') || ((navigator.language || 'pt').slice(0, 2));
if (!TXT[lang]) lang = 'pt';
let lastPairingUrl = '';
let initializedForm = false;
let hubNightVol = 25;
let hubPrefQual = '1080';
let hubAutostart = true;
let hubTvOpen = false;
let draggingHubVol = false;

function setSeg(id, idx, hasSel = true) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle('no-selection', !hasSel);
  el.style.setProperty('--idx', String(Math.max(0, idx)));
}

function applyHubLang() {
  const d = TXT[lang];
  document.getElementById('scanSub').textContent = d.scanSub;
  document.getElementById('qrHint').innerHTML = d.qrHint;
  document.getElementById('lblHubHs').textContent = d.lblHubHs;
  document.getElementById('lblHubOff').textContent = d.lblHubOff;
  document.getElementById('lblHubWake').textContent = d.lblHubWake;
  document.getElementById('lblSb').textContent = d.lblSb;
  document.getElementById('lblHs').textContent = d.lblHs;
  document.getElementById('lblVol').textContent = d.lblVol;
  document.getElementById('lblQual').textContent = d.lblQual;
  document.getElementById('lblAuto').textContent = d.lblAuto;
  document.getElementById('btnSave').textContent = d.btnSave;
  document.getElementById('btnTestTv').textContent = hubTvOpen ? d.btnCloseTv : d.btnTestTv;

  const langs = ['pt', 'en', 'es'];
  setSeg('hubLangSeg', langs.indexOf(lang), true);
  langs.forEach(l => {
    document.getElementById('b' + l.charAt(0).toUpperCase() + l.slice(1)).classList.toggle('active', l === lang);
  });
}

function setHubLang(l) {
  lang = l;
  localStorage.setItem('bedtube_hub_lang', l);
  applyHubLang();
  refreshHubState(true);
}

function setHubNightVolUI(v) {
  hubNightVol = Math.max(0, Math.min(100, Math.round(v)));
  document.getElementById('hubVolFill').style.setProperty('--vol-pct', hubNightVol + '%');
  document.getElementById('hubVolVal').textContent = hubNightVol + '%';
}

const hubCapsule = document.getElementById('hubVolCapsule');
function hubVolFromEvt(e) {
  const r = hubCapsule.getBoundingClientRect();
  return Math.round((Math.max(0, Math.min(r.width - 8, e.clientX - r.left - 4)) / Math.max(1, r.width - 8)) * 100);
}
hubCapsule.addEventListener('pointerdown', (e) => {
  draggingHubVol = true;
  hubCapsule.classList.add('dragging');
  hubCapsule.setPointerCapture(e.pointerId);
  setHubNightVolUI(hubVolFromEvt(e));
});
hubCapsule.addEventListener('pointermove', (e) => {
  if (draggingHubVol) setHubNightVolUI(hubVolFromEvt(e));
});
hubCapsule.addEventListener('pointerup', () => {
  draggingHubVol = false;
  hubCapsule.classList.remove('dragging');
});

function pickHubQual(q) {
  hubPrefQual = q;
  const order = ['1080', 'max', '720', 'auto'];
  setSeg('hubQualSeg', Math.max(0, order.indexOf(q)), true);
  order.forEach(item => {
    document.getElementById('hq' + item).classList.toggle('active', item === q);
  });
}

function toggleAutostartSwitch() {
  hubAutostart = !hubAutostart;
  document.getElementById('autostartRow').classList.toggle('checked', hubAutostart);
}

async function hubAction(cmd) {
  await fetch('/api/local/action', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cmd })
  });
  refreshHubState(false);
}

async function refreshHubState(forceRebuildSelects = false) {
  try {
    const r = await fetch('/api/local/hub-state');
    const s = await r.json();
    const d = TXT[lang];

    hubTvOpen = !!s.tv_open;
    const modePrefix = hubTvOpen ? d.tvMode : d.pcMode;
    document.getElementById('hubModeHost').textContent = `${modePrefix} • ${s.hostname || 'PC'}`;
    document.getElementById('tunnelBadge').textContent = s.tunnel_online ? d.cloudOk : d.cloudWait;
    document.getElementById('btnTestTv').textContent = hubTvOpen ? d.btnCloseTv : d.btnTestTv;
    document.getElementById('hubWakeBtn').classList.toggle('wake-glow', !!s.screen_off);

    const isSb = s.active_profile === 'soundbar';
    const isHs = s.active_profile === 'headset';
    document.getElementById('hubBtnSb').classList.toggle('active', isSb);
    document.getElementById('hubBtnHs').classList.toggle('active', isHs);
    if (isSb) setSeg('hubAudioSeg', 0, true);
    else if (isHs) setSeg('hubAudioSeg', 1, true);
    else setSeg('hubAudioSeg', 0, false);

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

      const pickBestDev = (list, query) => {
        if (!query) return '';
        const q = query.toLowerCase().trim();
        const exact = list.find(dev => dev.name.toLowerCase() === q);
        if (exact) return exact.name;
        const matches = list.filter(dev => dev.name.toLowerCase().includes(q));
        if (!matches.length) return '';
        if (!q.includes('spdif') && !q.includes('s/pdif')) {
          const analog = matches.filter(dev => !dev.name.toLowerCase().includes('s/pdif') && !dev.name.toLowerCase().includes('spdif'));
          if (analog.length) return (analog.find(dev => dev.is_default) || analog[0]).name;
        }
        return (matches.find(dev => dev.is_default) || matches[0]).name;
      };

      const populate = (id, selected) => {
        const sel = document.getElementById(id);
        sel.innerHTML = '';
        const empty = document.createElement('option');
        empty.value = '';
        empty.textContent = d.keepCur;
        sel.appendChild(empty);
        const bestName = pickBestDev(devs, selected);
        devs.forEach(dev => {
          const opt = document.createElement('option');
          opt.value = dev.name;
          opt.textContent = dev.name + (dev.is_default ? ' ★' : '');
          if (bestName && dev.name === bestName) {
            opt.selected = true;
          }
          sel.appendChild(opt);
        });
      };
      populate('selSb', sbVal);
      populate('selHs', hsVal);
      setHubNightVolUI(s.night_volume ?? 25);
      pickHubQual(s.preferred_quality || '1080');
      hubAutostart = !!s.autostart;
      document.getElementById('autostartRow').classList.toggle('checked', hubAutostart);
    }
  } catch (_) {}
}

async function saveHubSettings() {
  const body = {
    soundbar_match: document.getElementById('selSb').value,
    headset_match: document.getElementById('selHs').value,
    night_volume: hubNightVol,
    preferred_quality: hubPrefQual,
    autostart: hubAutostart,
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
  if (hubTvOpen) {
    await hubAction('fechar');
  } else {
    await fetch('/api/local/test-tv', { method: 'POST' });
    refreshHubState(false);
  }
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
                "background_color": "#0b0b10",
                "theme_color": "#0b0b10",
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

    @app.route("/api/local/action", methods=["POST"])
    def local_action():
        body = request.get_json(silent=True) or {}
        cmd = str(body.get("cmd") or body.get("action") or "").strip()
        params = body.get("params") if isinstance(body.get("params"), dict) else body
        res = execute_bedtube_action(tv_mgr, cmd, params)
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
        res = execute_bedtube_action(tv_mgr, "monitor_off")
        if cloud_bridge:
            cloud_bridge.publish_state()
        return jsonify(res)

    @app.route("/monitor/wake", methods=["POST"])
    def monitor_wake():
        res = execute_bedtube_action(tv_mgr, "monitor_wake")
        if cloud_bridge:
            cloud_bridge.publish_state()
        return jsonify(res)

    @app.route("/monitor/toggle", methods=["POST"])
    def monitor_toggle():
        res = execute_bedtube_action(tv_mgr, "monitor_toggle")
        if cloud_bridge:
            cloud_bridge.publish_state()
        return jsonify(res)

    @app.route("/sleep/<int:minutos>", methods=["POST"])
    def sleep_timer(minutos: int):
        res = execute_bedtube_action(tv_mgr, "sleep", {"minutos": minutos})
        if cloud_bridge:
            cloud_bridge.publish_state()
        return jsonify(res)

    @app.route("/pc/shutdown", methods=["POST"])
    def pc_shutdown():
        body = request.get_json(silent=True) or {}
        res = execute_bedtube_action(tv_mgr, "pc_shutdown", body)
        if cloud_bridge:
            cloud_bridge.publish_state()
        return jsonify(res)

    @app.route("/pc/shutdown/cancel", methods=["POST"])
    def pc_shutdown_cancel():
        res = execute_bedtube_action(tv_mgr, "pc_shutdown_cancel")
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
