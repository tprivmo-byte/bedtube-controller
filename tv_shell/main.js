const { app, BrowserWindow, screen, session, powerSaveBlocker } = require('electron');
const http = require('http');
const fs = require('fs');
const path = require('path');

// Cobalt 27 Chromium-based TV Identity (ported from VacuumTube v2.0.0 ua.js)
// Unlocks 1080p60 / 1440p60 / 4K60 non-DRM VP9/AV1 adaptive streams on Electron 45
const COBALT_VERSION = '27.lts.3.1040976-gold';
const COBALT_MAJOR = '27';
const V8_VERSION = '13.8.258.54';
const STARBOARD_VERSION = 18;
const MODEL_YEAR = new Date().getFullYear();
const BRAND = 'VacuumTube';
const APP_ID = 'rocks.shy.VacuumTube';
const APP_VER = '2.0.0';

const YOUTUBE_TV_UA = `Mozilla/5.0 (Windows NT 10.0; Win64; x64) Cobalt/${COBALT_VERSION} (unlike Gecko) v8/${V8_VERSION}-jit gles Starboard/${STARBOARD_VERSION}, ${BRAND}_DESKTOP_x8664_${MODEL_YEAR}/${APP_VER} (${BRAND}, Windows) ${APP_ID}/${APP_VER}`;
const GENERIC_UA = `BedTube/${APP_VER}`;

const USER_AGENT_METADATA = {
  brands: [{ brand: BRAND, version: COBALT_MAJOR }],
  fullVersionList: [{ brand: BRAND, version: '27.lts.3' }],
  fullVersion: '27.lts.3',
  platform: 'Starboard',
  platformVersion: '',
  architecture: 'x86',
  model: '',
  mobile: false,
  bitness: '64',
  wow64: false,
};

const CLIENT_HINT_HEADERS = {
  'sec-ch-ua': `"${BRAND}";v="${COBALT_MAJOR}"`,
  'sec-ch-ua-mobile': '?0',
  'sec-ch-ua-platform': '"Starboard"',
  'sec-ch-ua-full-version-list': `"${BRAND}";v="27.lts.3"`,
  'sec-ch-ua-full-version': '"27.lts.3"',
  'sec-ch-ua-platform-version': '""',
  'sec-ch-ua-arch': '"x86"',
  'sec-ch-ua-bitness': '"64"',
  'sec-ch-ua-model': '""',
  'sec-ch-ua-wow64': '?0',
};

const GOOGLE_HOSTS = [
  'youtube.com',
  'googlevideo.com',
  'ytimg.com',
  'ggpht.com',
  'google.com',
  'googleapis.com',
  'gstatic.com',
  'doubleclick.net',
  'googleusercontent.com',
  'youtube-nocookie.com',
];

function isGoogleHost(hostname) {
  return GOOGLE_HOSTS.some((h) => hostname === h || hostname.endsWith('.' + h));
}

const YOUTUBE_TV_URL = 'https://www.youtube.com/tv';
const PARTITION_NAME = 'persist:youtube_tv';
const CONTROL_PORT = parseInt(process.env.BEDTUBE_TV_PORT || '5051', 10);

// Isolated userData outside Edge and outside Palabary Hub (zero extension sync, zero lock conflicts)
const USER_DATA_DIR =
  process.env.BEDTUBE_TV_DATA ||
  path.join(process.env.LOCALAPPDATA || app.getPath('appData'), 'BedTube_TV_Data');
fs.mkdirSync(USER_DATA_DIR, { recursive: true });
app.setPath('userData', USER_DATA_DIR);
app.userAgentFallback = YOUTUBE_TV_UA;

// Performance, GPU Hardware Decoding & background-throttling switches
app.commandLine.appendSwitch('disable-background-timer-throttling');
app.commandLine.appendSwitch('disable-renderer-backgrounding');
app.commandLine.appendSwitch('disable-backgrounding-occluded-windows');
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');
app.commandLine.appendSwitch('disable-extensions');
app.commandLine.appendSwitch('enable-features', 'AcceleratedVideoDecoder,AcceleratedVideoEncoder');
app.commandLine.appendSwitch('disable-features', 'Translate,msEdgeSidebarV2');

const logPath = path.join(USER_DATA_DIR, 'youtube_tv.log');
function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}\n`;
  try {
    fs.appendFileSync(logPath, line, 'utf8');
  } catch {}
}

const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
  process.exit(0);
}

let tvWindow = null;
let controlServer = null;
let powerBlockerId = null;
let isClosing = false;

const AUTO_ATTACH_CONFIG = { autoAttach: true, waitForDebuggerOnStart: true, flatten: true };

async function applyCobaltMetadata(webContents) {
  try {
    if (!webContents.debugger.isAttached()) {
      webContents.debugger.attach('1.3');
    }

    webContents.debugger.on('message', async (_event, method, params) => {
      if (method === 'Target.attachedToTarget') {
        const { sessionId, targetInfo, waitingForDebugger } = params;
        try {
          if (targetInfo && targetInfo.type === 'iframe') {
            await webContents.debugger.sendCommand(
              'Emulation.setUserAgentOverride',
              { userAgent: YOUTUBE_TV_UA, userAgentMetadata: USER_AGENT_METADATA },
              sessionId
            );
            await webContents.debugger.sendCommand('Target.setAutoAttach', AUTO_ATTACH_CONFIG, sessionId);
          }
        } catch {} finally {
          if (waitingForDebugger) {
            webContents.debugger.sendCommand('Runtime.runIfWaitingForDebugger', {}, sessionId).catch(() => {});
          }
        }
      }
    });

    await webContents.debugger.sendCommand('Emulation.setUserAgentOverride', {
      userAgent: YOUTUBE_TV_UA,
      userAgentMetadata: USER_AGENT_METADATA,
    });
    await webContents.debugger.sendCommand('Target.setAutoAttach', AUTO_ATTACH_CONFIG);
  } catch (err) {
    log(`Aviso CDP UserAgentOverride: ${err.message}`);
  }
}

function focusTVWindow() {
  if (!tvWindow || tvWindow.isDestroyed()) return false;
  try {
    if (tvWindow.isMinimized()) tvWindow.restore();
    tvWindow.setFullScreen(true);
    tvWindow.show();
    tvWindow.moveTop();
    tvWindow.focus();
    tvWindow.webContents.focus();
    return true;
  } catch (err) {
    log(`Aviso ao focar janela: ${err.message}`);
    return false;
  }
}

async function gracefulClose() {
  if (isClosing) return;
  isClosing = true;
  log('Fechando YouTube TV de forma limpa...');
  try {
    const tvSession = session.fromPartition(PARTITION_NAME);
    await tvSession.cookies.flushStore();
  } catch {}
  try {
    if (powerBlockerId !== null && powerSaveBlocker.isStarted(powerBlockerId)) {
      powerSaveBlocker.stop(powerBlockerId);
      powerBlockerId = null;
    }
  } catch {}
  try {
    if (controlServer) {
      controlServer.close();
      controlServer = null;
    }
  } catch {}
  try {
    if (tvWindow && !tvWindow.isDestroyed()) {
      tvWindow.destroy();
    }
  } catch {}
  tvWindow = null;
  app.quit();
}

function isAtHomeRoot(url) {
  if (!url) return true;
  return (
    url === 'https://www.youtube.com/tv' ||
    url === 'https://www.youtube.com/tv/' ||
    url === 'https://www.youtube.com/tv#' ||
    url === 'https://www.youtube.com/tv#/'
  );
}

function handleBackNavigation() {
  if (!tvWindow || tvWindow.isDestroyed()) return;
  const wc = tvWindow.webContents;
  const beforeUrl = wc.getURL();

  if (isAtHomeRoot(beforeUrl)) {
    log(`Voltar na tela inicial (${beforeUrl}): fechando YouTube TV`);
    gracefulClose();
    return;
  }

  log(`Voltar dentro do YouTube TV (${beforeUrl}): enviando Escape p/ Leanback`);
  wc.executeJavaScript(
    `if (typeof window.__palabaryDispatch === 'function') {
       window.__palabaryDispatch('Escape', 'Escape', 27);
     }`
  ).catch(() => {});

  setTimeout(() => {
    if (!tvWindow || tvWindow.isDestroyed() || wc.isDestroyed()) return;
    const afterUrl = wc.getURL();
    if (afterUrl === beforeUrl && wc.canGoBack() && !isAtHomeRoot(afterUrl)) {
      log('Fallback goBack() acionado');
      wc.goBack();
    }
  }, 220);
}

function showTvToast(text) {
  if (!tvWindow || tvWindow.isDestroyed()) return;
  tvWindow.webContents
    .executeJavaScript(
      `(function() {
        let badge = document.getElementById('palabary-controller-badge');
        if (!badge) {
          badge = document.createElement('div');
          badge.id = 'palabary-controller-badge';
          document.body.appendChild(badge);
        }
        badge.innerHTML = ${JSON.stringify(text)};
        badge.style.opacity = '0.95';
        clearTimeout(window.__bedtubeBadgeTimer);
        window.__bedtubeBadgeTimer = setTimeout(() => {
          if (badge) badge.style.opacity = '0';
        }, 3200);
      })();`
    )
    .catch(() => {});
}

// Exact dual-dispatch engine from Palabary Hub (DOM KeyboardEvent + Native Chromium sendInputEvent)
function sendNativeKey(key) {
  if (!tvWindow || tvWindow.isDestroyed()) return false;
  const wc = tvWindow.webContents;

  const keyMap = {
    Up: { code: 'ArrowUp', key: 'ArrowUp', keyCode: 38, electronKey: 'Up' },
    Down: { code: 'ArrowDown', key: 'ArrowDown', keyCode: 40, electronKey: 'Down' },
    Left: { code: 'ArrowLeft', key: 'ArrowLeft', keyCode: 37, electronKey: 'Left' },
    Right: { code: 'ArrowRight', key: 'ArrowRight', keyCode: 39, electronKey: 'Right' },
    ArrowUp: { code: 'ArrowUp', key: 'ArrowUp', keyCode: 38, electronKey: 'Up' },
    ArrowDown: { code: 'ArrowDown', key: 'ArrowDown', keyCode: 40, electronKey: 'Down' },
    ArrowLeft: { code: 'ArrowLeft', key: 'ArrowLeft', keyCode: 37, electronKey: 'Left' },
    ArrowRight: { code: 'ArrowRight', key: 'ArrowRight', keyCode: 39, electronKey: 'Right' },
    Return: { code: 'Enter', key: 'Enter', keyCode: 13, electronKey: 'Return' },
    Enter: { code: 'Enter', key: 'Enter', keyCode: 13, electronKey: 'Return' },
    Select: { code: 'Enter', key: 'Enter', keyCode: 13, electronKey: 'Return' },
    Back: { code: 'Escape', key: 'Escape', keyCode: 27, electronKey: 'Escape' },
    Escape: { code: 'Escape', key: 'Escape', keyCode: 27, electronKey: 'Escape' },
    Space: { code: 'Space', key: ' ', keyCode: 32, electronKey: 'Space' },
    MediaPlayPause: {
      code: 'MediaPlayPause',
      key: 'MediaPlayPause',
      keyCode: 179,
      electronKey: 'MediaPlayPause',
    },
    Search: { code: 'Slash', key: '/', keyCode: 191, electronKey: '/' },
    s: { code: 'KeyS', key: 's', keyCode: 83, electronKey: 's' },
    '/': { code: 'Slash', key: '/', keyCode: 191, electronKey: '/' },
    Backspace: { code: 'Backspace', key: 'Backspace', keyCode: 8, electronKey: 'Backspace' },
  };

  const entry = keyMap[key] || {
    code: key,
    key,
    keyCode: key.length === 1 ? key.toUpperCase().charCodeAt(0) : 0,
    electronKey: key,
  };

  try {
    log(`sendNativeKey: Disparando '${key}' (${entry.electronKey})`);

    wc.executeJavaScript(
      `if (typeof window.__palabaryDispatch === 'function') {
         window.__palabaryDispatch(${JSON.stringify(entry.code)}, ${JSON.stringify(entry.key)}, ${Number(entry.keyCode) || 0});
       }`
    ).catch(() => {});

    wc.sendInputEvent({
      type: 'keyDown',
      keyCode: entry.electronKey,
    });

    if (entry.electronKey === 'Return' || entry.electronKey === 'Enter') {
      wc.sendInputEvent({ type: 'char', keyCode: '\r' });
    } else if (entry.electronKey === 'Space') {
      wc.sendInputEvent({ type: 'char', keyCode: ' ' });
    } else if (entry.electronKey.length === 1) {
      wc.sendInputEvent({ type: 'char', keyCode: entry.electronKey });
    }

    setTimeout(() => {
      if (tvWindow && !tvWindow.isDestroyed() && !wc.isDestroyed()) {
        wc.sendInputEvent({
          type: 'keyUp',
          keyCode: entry.electronKey,
        });
      }
    }, 40);

    return true;
  } catch (err) {
    log(`Erro em sendNativeKey: ${err.message}`);
    return false;
  }
}

function extractVideoId(input) {
  const s = String(input || '').trim();
  if (!s) return null;
  try {
    if (s.includes('youtube.com/') || s.includes('youtu.be/')) {
      const url = new URL(/^https?:\/\//i.test(s) ? s : `https://${s}`);
      if (url.hostname.includes('youtu.be')) {
        return url.pathname.split('/').filter(Boolean)[0] || null;
      }
      if (url.searchParams.get('v')) {
        return url.searchParams.get('v');
      }
      const shortsMatch = url.pathname.match(/\/shorts\/([a-zA-Z0-9_-]{6,})/);
      if (shortsMatch) return shortsMatch[1];
    }
  } catch {}
  return null;
}

async function typeSearchQuery(query) {
  if (!tvWindow || tvWindow.isDestroyed() || !query) return false;

  // If the user pasted a direct YouTube link, open the video directly on TV!
  const directVideoId = extractVideoId(query);
  if (directVideoId) {
    log(`Abrindo vídeo direto por link: ${directVideoId}`);
    tvWindow.webContents.loadURL(`https://www.youtube.com/tv?v=${encodeURIComponent(directVideoId)}`, {
      userAgent: YOUTUBE_TV_UA,
    });
    showTvToast('🎬 <span>Abrindo vídeo enviado pelo celular...</span>');
    return { ok: true, mode: 'direct_video', videoId: directVideoId };
  }

  sendNativeKey('/');
  await new Promise((r) => setTimeout(r, 280));
  for (const ch of String(query)) {
    if (!tvWindow || tvWindow.isDestroyed()) return false;
    tvWindow.webContents.insertText(ch);
    await new Promise((r) => setTimeout(r, 25));
  }
  await new Promise((r) => setTimeout(r, 120));
  sendNativeKey('Enter');
  return true;
}

function injectLeanbackEngine(wc) {
  log('did-finish-load: Injetando estilos de Leanback, auto-hide de cursor e motor de controle');

  const initialPref = process.env.BEDTUBE_PREFERRED_QUALITY || process.env.BEDTUBE_PREF_QUALITY || '';

  wc.insertCSS(`
    html.bedtube-hide-cursor,
    html.bedtube-hide-cursor * {
      cursor: none !important;
    }
    #palabary-controller-badge {
      position: fixed;
      top: 24px;
      right: 28px;
      z-index: 999999;
      background: rgba(10, 14, 22, 0.92);
      border: 1.5px solid rgba(255, 42, 75, 0.75);
      color: #f8fafc;
      padding: 8px 16px;
      border-radius: 20px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 13px;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 8px;
      box-shadow: 0 4px 16px rgba(0,0,0,0.75);
      pointer-events: none;
      opacity: 0.92;
      transition: opacity 0.5s ease;
    }
  `).catch(() => {});

  wc.executeJavaScript(`
    (function() {
      ${initialPref ? `if (typeof window.__bedtubeSetQuality === 'function') { window.__bedtubeSetQuality(${JSON.stringify(initialPref)}); }` : ''}

      // 1. Hide mouse cursor immediately on TV; show briefly only on physical mouse movement
      document.documentElement.classList.add('bedtube-hide-cursor');
      if (!window.__bedtubeCursorGuard) {
        window.__bedtubeCursorGuard = true;
        let cursorTimer = null;
        window.addEventListener('mousemove', () => {
          document.documentElement.classList.remove('bedtube-hide-cursor');
          clearTimeout(cursorTimer);
          cursorTimer = setTimeout(() => {
            document.documentElement.classList.add('bedtube-hide-cursor');
          }, 2200);
        }, { passive: true });
      }

      // 2. Discrete TV Status Badge (fades out after 4s)
      if (!document.getElementById('palabary-controller-badge')) {
        const badge = document.createElement('div');
        badge.id = 'palabary-controller-badge';
        badge.innerHTML = '📺 <span>BedTube TV Full HD / 4K Ativo</span>';
        document.body.appendChild(badge);
        window.__bedtubeBadgeTimer = setTimeout(() => { if (badge) badge.style.opacity = '0'; }, 4000);
      }

      // 3. Active In-Page Synthetic Key + 60fps Gamepad Engine (from Palabary Hub youtubeView.ts)
      if (!window.__palabaryGamepadEngineActive) {
        window.__palabaryGamepadEngineActive = true;

        function dispatchKey(code, key, keyCode) {
          const target = document.activeElement || document.body || window;
          const evtDown = new KeyboardEvent('keydown', {
            bubbles: true,
            cancelable: true,
            key: key,
            code: code,
            keyCode: keyCode,
            which: keyCode,
            view: window
          });
          try {
            Object.defineProperty(evtDown, 'keyCode', { get: () => keyCode });
            Object.defineProperty(evtDown, 'which', { get: () => keyCode });
          } catch (e) {}
          window.dispatchEvent(evtDown);
          document.dispatchEvent(evtDown);
          if (target && target !== window && target !== document) {
            target.dispatchEvent(evtDown);
          }

          setTimeout(() => {
            const evtUp = new KeyboardEvent('keyup', {
              bubbles: true,
              cancelable: true,
              key: key,
              code: code,
              keyCode: keyCode,
              which: keyCode,
              view: window
            });
            try {
              Object.defineProperty(evtUp, 'keyCode', { get: () => keyCode });
              Object.defineProperty(evtUp, 'which', { get: () => keyCode });
            } catch (e) {}
            window.dispatchEvent(evtUp);
            document.dispatchEvent(evtUp);
            if (target && target !== window && target !== document) {
              target.dispatchEvent(evtUp);
            }
          }, 40);
        }

        window.__palabaryDispatch = dispatchKey;

        let lastNav = 0;
        let lastPollTime = 0;
        const btnPrev = {};
        let bHeldStart = 0;

        function poll() {
          const now = performance.now();
          if (now - lastPollTime < 10) return;
          lastPollTime = now;

          const pads = navigator.getGamepads ? navigator.getGamepads() : [];
          let pad = null;
          for (let i = 0; i < pads.length; i++) {
            if (pads[i] && pads[i].connected) { pad = pads[i]; break; }
          }
          if (!pad) return;

          const ax = pad.axes[0] || 0;
          const ay = pad.axes[1] || 0;
          const DEADZONE = 0.45;

          const dUp = pad.buttons[12]?.pressed || ay < -DEADZONE;
          const dDown = pad.buttons[13]?.pressed || ay > DEADZONE;
          const dLeft = pad.buttons[14]?.pressed || ax < -DEADZONE;
          const dRight = pad.buttons[15]?.pressed || ax > DEADZONE;

          if (now - lastNav > 175) {
            if (dLeft) { dispatchKey('ArrowLeft', 'ArrowLeft', 37); lastNav = now; }
            else if (dRight) { dispatchKey('ArrowRight', 'ArrowRight', 39); lastNav = now; }
            else if (dUp) { dispatchKey('ArrowUp', 'ArrowUp', 38); lastNav = now; }
            else if (dDown) { dispatchKey('ArrowDown', 'ArrowDown', 40); lastNav = now; }
          }

          function check(btnIndex, callback) {
            const isPressed = pad.buttons[btnIndex]?.pressed;
            if (isPressed && !btnPrev[btnIndex]) callback();
            btnPrev[btnIndex] = isPressed;
          }

          check(0, () => dispatchKey('Enter', 'Enter', 13));

          const isBPressed = pad.buttons[1]?.pressed;
          if (isBPressed) {
            if (!bHeldStart) bHeldStart = now;
            if (now - bHeldStart > 850) {
              window.close();
              bHeldStart = now + 999999;
            }
          } else {
            if (bHeldStart && (now - bHeldStart <= 850)) {
              dispatchKey('Escape', 'Escape', 27);
            }
            bHeldStart = 0;
          }

          check(2, () => dispatchKey('KeyS', 's', 83));
          check(3, () => dispatchKey('Slash', '/', 191));
        }

        setInterval(poll, 20);
        function rafLoop() {
          poll();
          requestAnimationFrame(rafLoop);
        }
        requestAnimationFrame(rafLoop);
      }
    })();
  `).catch(() => {});
}

async function createTVWindow() {
  const tvSession = session.fromPartition(PARTITION_NAME);
  tvSession.setUserAgent(YOUTUBE_TV_UA);

  // One-time cache/service-worker migration when upgrading from Tizen 5.0 UA to Cobalt 27 UA
  // Preserves login cookies while ensuring YouTube serves the fresh 1080p/4K Cobalt bundle
  const migrationMarker = path.join(USER_DATA_DIR, 'engine_cobalt27_v1.flag');
  if (!fs.existsSync(migrationMarker)) {
    try {
      log('Limpando ServiceWorker/Cache antigo do Tizen 5.0 (preservando cookies)...');
      await tvSession.clearStorageData({ storages: ['serviceworkers', 'cachestorage'] });
      await tvSession.clearCache();
      fs.writeFileSync(migrationMarker, 'ok', 'utf8');
    } catch (err) {
      log(`Aviso na migração de cache: ${err.message}`);
    }
  }

  tvSession.webRequest.onBeforeRequest((details, callback) => {
    try {
      const url = new URL(details.url);
      if (url.host === 'csp.withgoogle.com') {
        return callback({ cancel: true });
      }
    } catch {}
    callback({ cancel: false });
  });

  // Apply exact Cobalt 27 User-Agent & Starboard Client Hints on all Google/YouTube requests
  tvSession.webRequest.onBeforeSendHeaders((details, callback) => {
    const headers = { ...details.requestHeaders };
    let google = true;
    try {
      google = isGoogleHost(new URL(details.url).hostname);
    } catch {}

    for (const key of Object.keys(headers)) {
      const lower = key.toLowerCase();
      if (lower === 'user-agent') {
        delete headers[key];
      } else if (lower.startsWith('sec-ch-ua')) {
        delete headers[key];
        if (google && CLIENT_HINT_HEADERS[lower] !== undefined) {
          headers[lower] = CLIENT_HINT_HEADERS[lower];
        }
      }
    }

    headers['User-Agent'] = google ? YOUTUBE_TV_UA : GENERIC_UA;
    callback({ cancel: false, requestHeaders: headers });
  });

  const primaryDisplay = screen.getPrimaryDisplay();
  const bounds = primaryDisplay.bounds;

  tvWindow = new BrowserWindow({
    x: bounds.x,
    y: bounds.y,
    width: bounds.width,
    height: bounds.height,
    fullscreen: true,
    frame: false,
    autoHideMenuBar: true,
    backgroundColor: '#000000',
    title: 'BedTube TV',
    webPreferences: {
      partition: PARTITION_NAME,
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: false, // Required for synchronous MediaSource & h5vcc override before YouTube bundle runs
      nodeIntegrationInSubFrames: true,
      sandbox: false,
      spellcheck: false,
    },
  });

  tvWindow.setMenuBarVisibility(false);
  tvWindow.webContents.setUserAgent(YOUTUBE_TV_UA);

  tvWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.includes('accounts.google.com') || url.includes('youtube.com/tv')) {
      return { action: 'allow' };
    }
    return { action: 'deny' };
  });

  const guardDesktopRedirect = (event, targetUrl) => {
    try {
      const u = new URL(targetUrl);
      const isYoutubeHost =
        u.hostname === 'www.youtube.com' ||
        u.hostname === 'youtube.com' ||
        u.hostname === 'm.youtube.com';
      if (isYoutubeHost && (u.pathname === '/' || u.pathname === '/index')) {
        log(`Bloqueado redirecionamento para YouTube Desktop (${targetUrl}) -> mantendo /tv`);
        event.preventDefault();
        if (tvWindow && !tvWindow.isDestroyed()) {
          tvWindow.webContents.loadURL(YOUTUBE_TV_URL, { userAgent: YOUTUBE_TV_UA });
        }
      }
    } catch {}
  };

  tvWindow.webContents.on('will-redirect', guardDesktopRedirect);
  tvWindow.webContents.on('will-navigate', guardDesktopRedirect);

  tvWindow.webContents.on('did-finish-load', () => {
    if (!tvWindow || tvWindow.isDestroyed()) return;
    log(`Página carregada: ${tvWindow.webContents.getURL()}`);
    injectLeanbackEngine(tvWindow.webContents);
  });

  tvWindow.webContents.on('before-input-event', (_event, input) => {
    if (input.type === 'keyDown' && (input.key === 'Escape' || input.code === 'BrowserBack')) {
      handleBackNavigation();
    }
  });

  tvWindow.on('closed', () => {
    tvWindow = null;
    gracefulClose();
  });

  log(`Abrindo YouTube TV Cobalt 27 Full HD/4K em tela cheia (${bounds.width}x${bounds.height})...`);
  await tvWindow.loadURL('about:blank');
  await applyCobaltMetadata(tvWindow.webContents);
  await tvWindow.loadURL(YOUTUBE_TV_URL, { userAgent: YOUTUBE_TV_UA });
  focusTVWindow();
}

function startControlServer() {
  controlServer = http.createServer((req, res) => {
    const sendJson = (code, obj) => {
      res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify(obj));
    };

    if (req.method === 'GET' && req.url === '/health') {
      const open = Boolean(tvWindow && !tvWindow.isDestroyed());
      return sendJson(200, {
        ok: open,
        pid: process.pid,
        url: open ? tvWindow.webContents.getURL() : '',
        title: open ? tvWindow.webContents.getTitle() : '',
        canGoBack: open ? tvWindow.webContents.canGoBack() : false,
      });
    }

    if (req.method === 'POST' && req.url === '/cmd') {
      let raw = '';
      req.on('data', (chunk) => {
        raw += chunk;
      });
      req.on('end', async () => {
        let body = {};
        try {
          body = JSON.parse(raw || '{}');
        } catch {}

        const action = body.action || '';
        if (action === 'focus') {
          return sendJson(200, { ok: focusTVWindow() });
        }
        if (action === 'close') {
          sendJson(200, { ok: true });
          setTimeout(() => gracefulClose(), 30);
          return;
        }
        if (action === 'back') {
          handleBackNavigation();
          return sendJson(200, { ok: true });
        }
        if (action === 'key') {
          const ok = sendNativeKey(body.key || '');
          return sendJson(200, { ok });
        }
        if (action === 'search_text') {
          const sr = await typeSearchQuery(body.text || '');
          if (sr && typeof sr === 'object') return sendJson(200, sr);
          return sendJson(200, { ok: Boolean(sr) });
        }
        if (action === 'set_quality') {
          const q = String(body.quality || body.pref || '1080');
          if (!tvWindow || tvWindow.isDestroyed()) {
            return sendJson(200, { ok: false, error: 'tv_closed' });
          }
          try {
            const result = await tvWindow.webContents.executeJavaScript(
              `typeof window.__bedtubeSetQuality === 'function' ? window.__bedtubeSetQuality(${JSON.stringify(q)}) : { ok: false }`
            );
            const stats = await tvWindow.webContents.executeJavaScript(
              `typeof window.__bedtubeGetPlayerStats === 'function' ? window.__bedtubeGetPlayerStats() : {}`
            );
            const labels = {
              '1080': '1080p Full HD',
              max: 'Máxima (até 4K)',
              '720': '720p HD',
              auto: 'Automática',
            };
            showTvToast(`🎞️ <span>Qualidade: ${labels[q] || q}</span>`);
            return sendJson(200, {
              ok: true,
              quality: q,
              result,
              currentQuality: stats?.currentQuality || '',
              intrinsicRes: stats?.intrinsicRes || '',
            });
          } catch (err) {
            return sendJson(200, { ok: false, error: err.message });
          }
        }
        if (action === 'player_stats') {
          if (!tvWindow || tvWindow.isDestroyed()) {
            return sendJson(200, { ok: false, active: false });
          }
          try {
            const stats = await tvWindow.webContents.executeJavaScript(
              `typeof window.__bedtubeGetPlayerStats === 'function' ? window.__bedtubeGetPlayerStats() : { active: false }`
            );
            return sendJson(200, { ok: true, stats });
          } catch (err) {
            return sendJson(200, { ok: false, error: err.message });
          }
        }
        return sendJson(400, { ok: false, error: 'unknown_action' });
      });
      return;
    }

    sendJson(404, { ok: false });
  });

  controlServer.on('error', (err) => {
    log(`Erro no servidor local de controle (${CONTROL_PORT}): ${err.message}`);
  });

  controlServer.listen(CONTROL_PORT, '127.0.0.1', () => {
    log(`Servidor local da TV ativo em http://127.0.0.1:${CONTROL_PORT}`);
  });
}

app.on('second-instance', () => {
  focusTVWindow();
});

app.whenReady().then(async () => {
  try {
    powerBlockerId = powerSaveBlocker.start('prevent-display-sleep');
  } catch {}
  startControlServer();
  await createTVWindow();
});

app.on('window-all-closed', () => {
  gracefulClose();
});
