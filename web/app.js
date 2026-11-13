import { Touchpad } from './gestures.js?v=10';
import { FrameDecoder, LatestFrame } from './decoder.js?v=10';
const $ = id => document.getElementById(id);
let socket, retry, heartbeat, installPrompt, toastTimer;
let sentText = null, activePanel = 'touch';
let directMode = false;
let live = false, screenReady = false, frameTimer, screenVersion = 0;
let videoEnabled = FrameDecoder.supported, activeStream = null;
let screens = [], layoutQueued = false;
for (const id of ['stream-quality', 'stream-fps']) {
  const select = $(id);
  try {
    const saved = localStorage.getItem(id);
    if ([...select.options].some(option => option.value === saved)) select.value = saved;
  } catch { /* Storage may be unavailable in private browsing. */ }
  select.addEventListener('change', () => {
    try { localStorage.setItem(id, select.value); } catch {}
    screenVersion++;
    if (id === 'stream-quality') videoEnabled = FrameDecoder.supported;
    requestFrame();
  });
}
const canvas = $('screen-canvas');
const screenContext = canvas.getContext('2d', {alpha: false, desynchronized: true});
const cursor = $('virtual-cursor');
let cursorX = .5, cursorY = .5, pointerSeq = 0, sentPointerSeq = 0;
function setScreenStatus(message) {
  $('screen-status').textContent = message;
  const performance = message.match(/^(\d+×\d+) · (\d+ FPS)/);
  $('live-indicator').textContent = performance ? `${performance[2]} · ${performance[1]}` : message;
  $('live-indicator').classList.toggle('notice', !performance);
}
function scheduleLayout() {
  if (layoutQueued) return;
  layoutQueued = true;
  requestAnimationFrame(() => {
    layoutQueued = false;
    const app = document.querySelector('.app'), toolbar = document.querySelector('.toolbar');
    const monitor = screens.find(screen => screen.id === $('screen-select').value);
    const aspect = monitor ? monitor.width / monitor.height : 16 / 9;
    const area = () => {
      const r = $('touchpad').getBoundingClientRect();
      if (!live) return r.width * r.height;
      const w = Math.min(r.width, r.height * aspect);
      return w * w / aspect;
    };
    // Measure both real layouts (including safe areas and wrapped buttons) in
    // one animation frame. Popup panels are overlays and consume no dock area.
    app.dataset.dock = 'bottom';
    const bottom = area();
    app.dataset.dock = 'left';
    for (let columns = 1; columns <= 3; columns++) {
      app.style.setProperty('--rail-columns', columns);
      if (toolbar.scrollHeight <= toolbar.clientHeight + 1) break;
    }
    const left = area();
    app.dataset.dock = left > bottom + 1 ? 'left' : 'bottom';
    // Bound the popup to the visual viewport, including an open phone keyboard.
    const bounds = app.getBoundingClientRect();
    const dock = document.querySelector('.dock').getBoundingClientRect();
    document.querySelector('.panel-shell').style.maxHeight = `${Math.max(90, app.dataset.dock === 'left' ? bounds.height - 16 : dock.top - bounds.top - 16)}px`;
    drawCursor();
  });
}
new ResizeObserver(scheduleLayout).observe(document.querySelector('.app'));
window.addEventListener('resize', scheduleLayout);
function updateScreenButtons() {
  const current = Math.max(0, screens.findIndex(screen => screen.id === $('screen-select').value));
  const button = $('screen-select');
  button.disabled = screens.length < 2;
  $('screen-number').textContent = String(current + 1);
  const label = screens.length ? `切换显示器：当前屏幕 ${current+1}，共 ${screens.length} 个` : '没有可用显示器';
  button.setAttribute('aria-label', label); button.title = label;
  $('screen-options').replaceChildren(...screens.map((screen, index) => {
    const choice = document.createElement('button');
    choice.className = 'screen-choice'; choice.dataset.screen = screen.id;
    choice.textContent = `屏幕 ${index + 1}`;
    choice.title = `${screen.width} × ${screen.height}`;
    choice.setAttribute('aria-pressed', String(index === current));
    choice.addEventListener('click', () => selectScreen(screen.id));
    return choice;
  }));
}
function selectScreen(identifier) {
  if ($('screen-select').value === identifier) return;
  pad.reset(); $('screen-select').value = identifier;
  clearScreen(); updateScreenButtons(); scheduleLayout(); requestFrame();
}
function drawCursor() {
  cursor.hidden = !live || !screenReady;
  if (cursor.hidden) return;
  const r = canvas.getBoundingClientRect(), padRect = $('touchpad').getBoundingClientRect();
  cursor.style.transform = `translate(${r.left-padRect.left+cursorX*r.width-1.6875}px, ${r.top-padRect.top+cursorY*r.height-1.125}px)`;
}
function localPosition(x, y) {
  cursorX = Math.max(0, Math.min(1, x)); cursorY = Math.max(0, Math.min(1, y));
  pointerSeq++; drawCursor();
  return {screen: $('screen-select').value, x: cursorX, y: cursorY};
}
function controlSend(data) {
  if (live && screenReady && (data.type === 'click' || (data.type === 'button' && data.down))) {
    if (!controlSend({type: 'position', screen: $('screen-select').value, x: cursorX, y: cursorY})) return false;
  }
  if (data.type === 'position') data.seq = pointerSeq;
  const sent = send(data);
  if (sent && data.type === 'position') sentPointerSeq = pointerSeq;
  return sent;
}
new ResizeObserver(drawCursor).observe($('touchpad'));
function clearScreen() {
  screenVersion++; screenReady = false;
  cursorX = cursorY = .5; pointerSeq++; sentPointerSeq = pointerSeq; drawCursor();
  screenContext.clearRect(0, 0, canvas.width, canvas.height);
}
function requestFrame() {
  clearTimeout(frameTimer);
  if (!live || !$('screen-select').value) return;
  if (!videoEnabled) { setScreenStatus('当前浏览器无法解码 H.264，请使用支持 WebCodecs 的新版浏览器'); return; }
  const settings = {type: 'stream', screen: $('screen-select').value, generation: screenVersion,
                    quality: $('stream-quality').value, fps: Number($('stream-fps').value),
                    codec: 'h264'};
  const key = JSON.stringify(settings);
  if (key !== activeStream && send({...settings, seq: sentPointerSeq})) activeStream = key;
}
function refreshScreens() { pad.reset(); clearScreen(); if (live) { setScreenStatus('正在读取屏幕…'); send({type: 'screens'}); } }
$('view-toggle').addEventListener('click', () => {
  pad.reset(); live = !live; clearScreen(); clearTimeout(frameTimer);
  const viewLabel = live ? '切回触控板' : '实时画面';
  $('view-toggle').setAttribute('aria-label', viewLabel); $('view-toggle').title = viewLabel;
  $('view-toggle').querySelector('use').setAttribute('href', live ? '#i-touchpad' : '#i-screen');
  showPanel('touch');
  $('view-toggle').setAttribute('aria-pressed', String(live));
  for (const id of ['screen-controls', 'input-mode', 'display-settings', 'screen-canvas', 'live-indicator']) $(id).hidden = !live;
  $('touchpad').classList.toggle('live', live);
  document.querySelector('.app').classList.toggle('live', live);
  $('touchpad').setAttribute('aria-label', live ? '远程实时画面' : '触摸板');
  if (live) refreshScreens();
  else { send({type: 'stream_stop'}); activeStream = null; }
  scheduleLayout();
});
$('screen-select').addEventListener('click', () => {
  const index = screens.findIndex(screen => screen.id === $('screen-select').value);
  if (screens.length > 1) selectScreen(screens[(index + 1) % screens.length].id);
});
$('refresh-screens').addEventListener('click', refreshScreens);
$('input-mode').addEventListener('click', () => {
  pad.reset(); directMode = !directMode;
  const button = $('input-mode');
  button.setAttribute('aria-pressed', String(directMode));
  button.setAttribute('aria-label', directMode ? '鼠标模式' : '触控板模式');
  button.title = directMode ? '鼠标模式' : '触控板模式';
  button.querySelector('.mode-touchpad').toggleAttribute('hidden', directMode);
  button.querySelector('.mode-mouse').toggleAttribute('hidden', !directMode);
});
function toast(message) {
  $('toast').textContent = message; $('toast').classList.add('visible');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').classList.remove('visible'), 2400);
}
function send(data) {
  if (socket?.readyState !== WebSocket.OPEN) return false;
  if (socket.bufferedAmount > 32768) { socket.close(); return false; }
  socket.send(JSON.stringify(data)); return true;
}
function command(data) { if (!controlSend(data)) toast('未连接，正在重试'); }
function connectionStatus(message, state = 'error') {
  $('connection-status').textContent = message;
  $('connection-status').dataset.state = state;
  scheduleLayout();
}
async function diagnoseConnection(connection) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3000);
  let message = '连接失败，请检查 Wi-Fi，并确认手机已安装和信任电脑的 CA 证书';
  try {
    const response = await fetch('/api/status', {cache: 'no-store', signal: controller.signal});
    if (response.ok) {
      const status = await response.json();
      message = status.busy ? '另一台设备或标签页正在控制，请关闭后重试' :
        '控制连接失败：请确认手机已信任 CA 证书，并用系统浏览器重新打开';
    }
  } catch {} finally { clearTimeout(timer); }
  if (socket === connection && connection.readyState !== WebSocket.OPEN) connectionStatus(message);
}
const pad = new Touchpad($('touchpad'), controlSend, {
  enabled: () => !live || screenReady,
  direct: () => live && directMode,
  relativePosition: (dx, dy) => {
    if (!live || !screenReady) return null;
    const r = canvas.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    return localPosition(cursorX+dx/r.width, cursorY+dy/r.height);
  },
  position: (e, clamp) => {
    const r = canvas.getBoundingClientRect();
    if (!screenReady || !r.width || !r.height) return null;
    const x = (e.clientX-r.left)/r.width, y = (e.clientY-r.top)/r.height;
    if (!clamp && (x < 0 || x > 1 || y < 0 || y > 1)) return null;
    return localPosition(x, y);
  },
  sensitivity: () => +$('sensitivity').value,
  natural: () => $('natural-scroll').checked,
  onLock: value => $('drag-lock').setAttribute('aria-pressed', String(value)),
  vibrate: () => navigator.vibrate?.(8)
});
function connect() {
  clearTimeout(retry);
  if (document.hidden || socket?.readyState === WebSocket.CONNECTING || socket?.readyState === WebSocket.OPEN) return;
  let connection;
  try {
    connection = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws`);
  } catch {
    connectionStatus('浏览器阻止了控制连接，请检查证书信任或更换浏览器');
    retry = setTimeout(connect, 5000);
    return;
  }
  socket = connection;
  let ready = false;
  const connectTimer = setTimeout(() => {
    if (socket !== connection || ready) return;
    connectionStatus('连接超时，请检查网络和手机的证书信任设置');
    connection.close();
    // Retry even when a browser never completes its WebSocket close handshake.
    socket = null;
    if (!document.hidden) retry = setTimeout(connect, 5000);
  }, 8000);
  connection.onerror = () => {
    if (socket === connection) connectionStatus('控制连接失败，正在检查…');
  };
  connection.binaryType = 'arraybuffer';
  activeStream = null; videoEnabled = FrameDecoder.supported;
  const decoder = new FrameDecoder();
  let displayedFrames = 0, statsAt = 0, statsGeneration = null, metadata = null, rtt = 0;
  let receivedBytes = 0, decodedFrames = 0;
  const renderer = new LatestFrame((bitmap, meta) => {
    if (socket !== connection || !live || meta.generation !== screenVersion) return;
    const width = bitmap.displayWidth, height = bitmap.displayHeight;
    const changed = canvas.width !== width || canvas.height !== height;
    if (changed) { canvas.width = width; canvas.height = height; }
    screenContext.drawImage(bitmap, 0, 0);
    const first = !screenReady; screenReady = true;
    if (changed || first) drawCursor();
    if (changed) scheduleLayout();
    const now = performance.now();
    if (statsGeneration !== meta.generation) {
      statsAt = now; displayedFrames = 0; receivedBytes = 0; decodedFrames = 0; statsGeneration = meta.generation;
    } else displayedFrames++;
    if (now - statsAt >= 1000) {
      const fps = Math.round(displayedFrames * 1000 / (now - statsAt));
      const mbps = receivedBytes * .008 / (now - statsAt);
      setScreenStatus(`${width}×${height} · ${fps} FPS · NVENC · ${mbps.toFixed(1)} Mbps · 采编 ${Math.round(meta.capture_ms)}ms / 解码 ${Math.round(meta.decode_ms)}ms / RTT ${Math.round(rtt)}ms`);
      send({type: 'stream_stats', generation: meta.generation, fps, decoded_fps: decodedFrames * 1000 / (now-statsAt),
            mbps, decode_ms: meta.decode_ms, rtt_ms: rtt, width, height});
      statsAt = now; displayedFrames = 0; receivedBytes = 0; decodedFrames = 0;
    }
  });
  connection.onmessage = e => {
    if (socket !== connection) return;
    if (e.data instanceof ArrayBuffer) {
      const meta = metadata;
      metadata = null;
      if (!meta) { connection.close(); return; }
      receivedBytes += e.data.byteLength;
      const started = performance.now();
      const ack = () => { if (socket === connection) send({type: 'frame_ack', frame_id: meta.frame_id}); };
      if (!live || meta.generation !== screenVersion) { ack(); return; }
      decoder.decode(e.data, meta).then(bitmap => {
        meta.decode_ms = performance.now() - started;
        decodedFrames++;
        // Receipt and decoding can advance while the last image waits for vsync.
        if (socket === connection && live && meta.generation === screenVersion) renderer.offer(bitmap, meta);
        else bitmap.close();
      }).catch(error => {
        if (error.name !== 'AbortError' && socket === connection && live && meta.generation === screenVersion) {
          videoEnabled = false; decoder.close(); renderer.clear(); screenVersion++;
          send({type: 'stream_stop'}); activeStream = null;
          screenReady = false; drawCursor();
          setScreenStatus('视频解码失败，请降低画质或更换支持 H.264 硬件解码的浏览器');
        }
      }).finally(ack);
      return;
    }
    const data = JSON.parse(e.data);
    if (data.type === 'ready') {
      ready = true; clearTimeout(connectTimer);
      connectionStatus('已连接', 'ready');
      if (!live) setScreenStatus('已连接，可开启实时画面');
      if (live) refreshScreens();
      send({type: 'ping', at: performance.now()});
      clearInterval(heartbeat); heartbeat = setInterval(() => send({type: 'ping', at: performance.now()}), 1500);
    } else if (data.type === 'pong') {
      if (Number.isFinite(data.at)) rtt = performance.now() - data.at;
    } else if (data.type === 'cursor') {
      metadata = data;
      // Ignore delayed feedback from older input or another monitor/view generation.
      if (live && data.screen === $('screen-select').value && data.generation === screenVersion &&
          data.seq === pointerSeq && !pad.points.size && !pad.locked && data.visible &&
          Number.isFinite(data.x) && Number.isFinite(data.y)) {
        const x = Math.max(0, Math.min(1, data.x)), y = Math.max(0, Math.min(1, data.y));
        if (x !== cursorX || y !== cursorY) { cursorX = x; cursorY = y; drawCursor(); }
      }
    } else if (data.type === 'screens') {
      const previous = $('screen-select').value;
      screens = data.screens;
      $('screen-select').value = screens.some(screen => screen.id === previous) ? previous : (screens[0]?.id || '');
      updateScreenButtons(); scheduleLayout();
      if (!data.screens.length) setScreenStatus('没有可用显示器');
      requestFrame();
    } else if (data.type === 'screen_error') {
      activeStream = null; pad.reset(); clearScreen(); setScreenStatus(data.message);
      clearTimeout(frameTimer); frameTimer = setTimeout(refreshScreens, 2000);
    } else if (data.type === 'error') {
      toast(data.message); sentText = null; $('send-text').disabled = false;
    } else if (data.type === 'text_sent') {
      if ($('text-input').value === sentText) $('text-input').value = '';
      sentText = null; $('send-text').disabled = false;
    }
  };
  connection.onclose = () => {
    clearTimeout(connectTimer);
    if (socket !== connection) return;
    clearInterval(heartbeat); pad.reset(); $('send-text').disabled = false;
    decoder.close(); renderer.clear(); activeStream = null;
    clearTimeout(frameTimer); clearScreen(); setScreenStatus('连接断开，正在重连…');
    connectionStatus('连接断开，正在重连…');
    diagnoseConnection(connection);
    if (sentText !== null) { toast('连接中断，请确认文字是否已发送'); sentText = null; }
    if (!document.hidden) retry = setTimeout(connect, 5000);
  };
}
const panelLabels = {keyboard: '键盘输入', media: '媒体控制', navigation: '方向与导航', shortcuts: '快捷操作', settings: '偏好设置', display: '画面设置'};
function showPanel(name) {
  pad.reset(); activePanel = name;
  if (activePanel !== 'keyboard') $('text-input').blur();
  document.querySelector('.panel-shell').hidden = activePanel === 'touch';
  $('panel-title').textContent = panelLabels[activePanel] || '';
  document.querySelectorAll('.panel').forEach(panel => { panel.hidden = panel.id !== `panel-${activePanel}`; });
  document.querySelectorAll('[data-panel]').forEach(item => {
    const selected = item.dataset.panel === activePanel;
    item.classList.toggle('selected', selected); item.setAttribute('aria-pressed', String(selected));
    if (item.dataset.panel !== 'touch') item.setAttribute('aria-expanded', String(selected));
  });
  scheduleLayout();
}
document.querySelectorAll('[data-panel]').forEach(button => button.addEventListener('click', () => {
  showPanel(activePanel === button.dataset.panel ? 'touch' : button.dataset.panel);
}));
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && activePanel !== 'touch') { showPanel('touch'); event.preventDefault(); }
});
$('touchpad').addEventListener('pointerdown', () => { if (activePanel !== 'touch') showPanel('touch'); }, {capture: true});
document.querySelectorAll('[data-key]').forEach(button => button.addEventListener('click', () => command({type: 'key', key: button.dataset.key})));
document.querySelectorAll('[data-shortcut]').forEach(button => button.addEventListener('click', () => command({type: 'shortcut', key: button.dataset.shortcut})));
document.querySelectorAll('[data-click]').forEach(button => button.addEventListener('click', () => command({type: 'click', button: button.dataset.click})));
$('drag-lock').addEventListener('click', () => { if (socket?.readyState === WebSocket.OPEN) pad.toggleLock(); else toast('未连接，正在重试'); });
$('send-text').addEventListener('click', () => {
  const text = $('text-input').value; if (!text) return;
  if (send({type: 'text', text})) { sentText = text; $('send-text').disabled = true; }
  else toast('未连接，文字尚未发送');
});
function savePreferences() {
  const value = +$('sensitivity').value;
  $('sensitivity-value').textContent = `${value.toFixed(1)}×`;
  try { localStorage.setItem('palm-preferences', JSON.stringify({sensitivity: value, natural: $('natural-scroll').checked})); } catch {}
}
try {
  const saved = JSON.parse(localStorage.getItem('palm-preferences') || '{}');
  if (Number.isFinite(saved.sensitivity)) $('sensitivity').value = saved.sensitivity;
  $('natural-scroll').checked = saved.natural !== false;
} catch {}
$('sensitivity').addEventListener('input', savePreferences);
$('natural-scroll').addEventListener('change', savePreferences); savePreferences();
window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); installPrompt = e; });
$('install').addEventListener('click', async () => {
  if (installPrompt) { await installPrompt.prompt(); await installPrompt.userChoice; installPrompt = null; }
  else toast(window.isSecureContext ? '浏览器菜单 → 安装 / 添加到主屏幕' : '请通过可信 HTTPS 安装');
});
function suspend() { pad.reset(); clearScreen(); clearTimeout(frameTimer); clearInterval(heartbeat); clearTimeout(retry); socket?.close(); }
document.addEventListener('visibilitychange', () => { if (document.hidden) suspend(); else connect(); });
window.addEventListener('pagehide', suspend); window.addEventListener('online', connect); window.addEventListener('pageshow', connect);
// Keep the dock above the phone keyboard and leave the rest available for gestures.
function fitViewport() {
  if (!window.visualViewport) return;
  const app = document.querySelector('.app');
  app.style.height = `${window.visualViewport.height}px`;
  app.style.top = `${window.visualViewport.offsetTop}px`;
  scheduleLayout();
}
window.visualViewport?.addEventListener('resize', fitViewport);
window.visualViewport?.addEventListener('scroll', fitViewport); fitViewport();
if ('serviceWorker' in navigator && window.isSecureContext) {
  const registration = navigator.serviceWorker.register('/sw.js', {updateViaCache: 'none'});
  const checkUpdate = () => registration.then(worker => worker.update()).catch(() => {});
  checkUpdate();
  document.addEventListener('visibilitychange', () => { if (!document.hidden) checkUpdate(); });
}
connect();
