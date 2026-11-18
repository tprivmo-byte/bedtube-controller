// BedTube TV Preload — Synchronous Main-World Capabilities & High-Resolution Unlock
// Runs before YouTube Leanback scripts execute (contextIsolation: false, nodeIntegration: false)
(function () {
  if (typeof window === 'undefined') return;

  const isYouTube =
    location.hostname === 'www.youtube.com' ||
    location.hostname === 'youtube.com' ||
    location.hostname.endsWith('.youtube.com');

  if (!isYouTube) return;

  // 1. Cobalt MSE Capability Validator (ported from VacuumTube media-capabilities.js)
  // Why this fixes the 720p cap:
  // YouTube TV probes MediaSource.isTypeSupported with Cobalt MIME parameters like
  // "width=99999" or "framerate=9999" expecting absurd values to be REJECTED.
  // Plain Chromium ignores extra MIME parameters and returns true for everything,
  // causing YouTube TV's capability check to fail and fall back to safe-mode 720p30.
  try {
    if (typeof window.MediaSource !== 'undefined' && typeof MediaSource.isTypeSupported === 'function') {
      const nativeIsTypeSupported = MediaSource.isTypeSupported.bind(MediaSource);

      const isHdrDisplay = () => {
        try {
          return window.matchMedia('(dynamic-range: high)').matches;
        } catch {
          return false;
        }
      };

      const isHdrCodec = (codec) => {
        const parts = codec.split('.');
        const transfer = parts[0] === 'vp09' ? parts[6] : parts[0] === 'av01' ? parts[7] : undefined;
        return transfer === '16' || transfer === '18';
      };

      const paramChecks = {
        width: (v) => Number(v) > 0 && Number(v) <= 7680,
        height: (v) => Number(v) > 0 && Number(v) <= 4320,
        framerate: (v) => Number(v) > 0 && Number(v) <= 120,
        bitrate: (v) => Number(v) > 0 && Number(v) <= 200000000,
        eotf: (v) => v === 'bt709' || ((v === 'smpte2084' || v === 'arib-std-b67') && isHdrDisplay()),
        channels: (v) => Number(v) > 0 && Number(v) <= 8,
        cryptoblockformat: (v) => v === 'subsample',
        'decode-to-texture': (v) => v === 'true' || v === 'false',
      };

      MediaSource.isTypeSupported = function (type) {
        const [mimeType, ...params] = String(type)
          .split(';')
          .map((p) => p.trim());
        const kept = [mimeType];

        for (const param of params) {
          const [rawKey, ...rest] = param.split('=');
          const key = rawKey.trim().toLowerCase();
          const value = rest
            .join('=')
            .trim()
            .replace(/^"|"$/g, '');

          if (key === 'codecs') {
            if (value.split(',').some((c) => isHdrCodec(c.trim())) && !isHdrDisplay()) {
              return false;
            }
            kept.push(param);
          } else if (paramChecks[key]) {
            if (!paramChecks[key](value)) {
              return false;
            }
          } else {
            kept.push(param);
          }
        }

        return nativeIsTypeSupported(kept.join('; '));
      };
    }
  } catch (err) {
    console.error('[BedTube] MediaSource patch error:', err);
  }

  // 2. Cobalt h5vcc Container Override — bypasses Windows DPI scaling (125%/150%) & unlocks up to 4K/8K
  try {
    window.h5vcc = {
      runtime: {
        initialDeepLink: null,
        onDeepLink: { addListener: function () {} },
      },
      system: {
        getVideoContainerSizeOverride: function () {
          return '3840x2160';
        },
      },
    };
  } catch (err) {
    console.error('[BedTube] h5vcc override error:', err);
  }

  // 3. WebP thumbnails + Cast Friendly Name ("BedTube TV")
  try {
    let ytcfgVal;
    Object.defineProperty(window, 'ytcfg', {
      configurable: true,
      get() {
        return ytcfgVal;
      },
      set(val) {
        ytcfgVal = val;
        if (val && typeof val.set === 'function') {
          const origSet = val.set;
          val.set = function (...args) {
            const res = origSet.apply(this, args);
            try {
              if (val.data_ && val.data_.INNERTUBE_CONTEXT && val.data_.INNERTUBE_CONTEXT.client) {
                val.data_.INNERTUBE_CONTEXT.client.webpSupport = true;
              }
            } catch {}
            return res;
          };
        }
      },
    });
  } catch {}

  // 4. Preferred Quality Controller (defaults to 1080p HD, switchable live from mobile remote)
  const initialPref = (function () {
    try {
      return localStorage.getItem('bedtube_preferred_quality') || '1080';
    } catch {
      return '1080';
    }
  })();

  window.__bedtubePreferredQuality = initialPref; // '1080' | 'max' | '720' | 'auto'
  let lastAppliedSig = null;

  function applyPreferredQuality(forceReapply) {
    const pref = window.__bedtubePreferredQuality || '1080';
    if (pref === 'auto') return { ok: true, mode: 'auto' };

    const player = document.querySelector('.html5-video-player');
    if (!player || typeof player.getAvailableQualityData !== 'function' || typeof player.setPlaybackQualityRange !== 'function') {
      return { ok: false, reason: 'no_player' };
    }

    let videoId = '';
    try {
      videoId = player.getVideoData?.()?.video_id || '';
    } catch {}
    if (!videoId) return { ok: false, reason: 'no_video' };

    let qualities = [];
    try {
      qualities = player.getAvailableQualityData() || [];
    } catch {}
    if (!qualities.length) return { ok: false, reason: 'no_qualities' };

    let target = null;
    if (pref === 'max' || pref === '2160' || pref === '4k') {
      const sorted = qualities
        .map((q) => ({ quality: q.quality, label: q.qualityLabel, n: parseInt(q.qualityLabel, 10) || 0 }))
        .sort((a, b) => b.n - a.n);
      target = sorted[0]?.quality || 'highres';
    } else {
      const cap = parseInt(pref, 10) || 1080;
      const sorted = qualities
        .map((q) => ({ quality: q.quality, label: q.qualityLabel, n: parseInt(q.qualityLabel, 10) }))
        .filter((x) => !Number.isNaN(x.n))
        .sort((a, b) => b.n - a.n);
      const pick = sorted.find((x) => x.n <= cap) || sorted[sorted.length - 1];
      target = pick?.quality || null;
    }

    if (!target) return { ok: false, reason: 'no_target' };

    const sig = `${videoId}:${pref}:${target}`;
    if (!forceReapply && sig === lastAppliedSig) {
      return { ok: true, already: true, target };
    }

    try {
      player.setPlaybackQualityRange(target, target);
      if (typeof player.setPlaybackQuality === 'function') {
        player.setPlaybackQuality(target);
      }
      lastAppliedSig = sig;
      return { ok: true, target, videoId };
    } catch (err) {
      return { ok: false, error: String(err) };
    }
  }

  window.__bedtubeSetQuality = function (pref) {
    window.__bedtubePreferredQuality = String(pref || '1080');
    try {
      localStorage.setItem('bedtube_preferred_quality', window.__bedtubePreferredQuality);
    } catch {}
    lastAppliedSig = null;
    return applyPreferredQuality(true);
  };

  window.__bedtubeGetPlayerStats = function () {
    const player = document.querySelector('.html5-video-player');
    const videoEl = document.querySelector('video.html5-main-video') || document.querySelector('video');
    if (!player) {
      return { active: false, preferred: window.__bedtubePreferredQuality };
    }
    let available = [];
    let currentQuality = '';
    let videoData = {};
    try {
      available = (player.getAvailableQualityData?.() || []).map((q) => ({
        quality: q.quality,
        label: q.qualityLabel,
      }));
    } catch {}
    try {
      currentQuality = player.getPlaybackQualityLabel?.() || player.getPlaybackQuality?.() || '';
    } catch {}
    try {
      videoData = player.getVideoData?.() || {};
    } catch {}
    return {
      active: Boolean(videoData.video_id),
      videoId: videoData.video_id || '',
      title: videoData.title || '',
      currentQuality,
      intrinsicRes: videoEl ? `${videoEl.videoWidth}x${videoEl.videoHeight}` : '',
      available,
      preferred: window.__bedtubePreferredQuality,
    };
  };

  setInterval(() => {
    try {
      applyPreferredQuality(false);
    } catch {}
  }, 900);
})();
