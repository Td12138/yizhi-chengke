'use strict';

/**
 * 一纸成课桌面版主进程。
 *
 * 职责：
 *   1. 先出窗口骨架（loading.html），避免白屏等后端
 *   2. 在 8710 → 8711 → 8712 里选一个空闲端口，启动 FastAPI 后端
 *      （dev：宿主 Python；打包：内置 embeddable Python）
 *   3. 轮询 /api/health，15s 内就绪则加载界面，否则显示可读错误页 + 查看日志
 *   4. 退出时 taskkill 进程树，确保不留 python.exe
 *
 * Web 模式（vite 5173 + 后端 8710）完全不受本文件影响。
 */

const { app, BrowserWindow, ipcMain, session, shell } = require('electron');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const net = require('node:net');
const path = require('node:path');

// ---- 常量（集中定义，正文只引用）----
const HOST = '127.0.0.1';
const BASE_PORT = Number(process.env.APP_PORT || 8710);
const PORT_ATTEMPTS = 3;
const READY_TIMEOUT_MS = Number(process.env.YIZHI_READY_TIMEOUT_MS || 15000);
const POLL_INTERVAL_MS = 300;
const HEALTH_TIMEOUT_MS = 1200;
const PORT_PROBE_TIMEOUT_MS = 700;
const LOG_TAIL_LINES = 40;
const WINDOW_WIDTH = 1280;
const WINDOW_HEIGHT = 860;

const IS_PACKAGED = app.isPackaged;
const REPO_ROOT = path.resolve(__dirname, '..', '..');
const WEB_DIST = IS_PACKAGED
  ? path.join(process.resourcesPath, 'web')
  : path.join(REPO_ROOT, 'apps', 'web', 'dist');
const SERVER_DIR = IS_PACKAGED
  ? path.join(process.resourcesPath, 'server')
  : path.join(REPO_ROOT, 'apps', 'server');
const RUNTIME_PYTHON = path.join(process.resourcesPath || '', 'runtime', 'python', 'python.exe');
const DATA_DIR = IS_PACKAGED ? path.join(app.getPath('userData'), 'data') : '';
const LOG_DIR = path.join(app.getPath('userData'), 'logs');
const LOG_FILE = path.join(LOG_DIR, 'desktop.log');
const LOCAL_ORIGIN_PREFIX = `http://${HOST}:`;

// 仅用于本机验证摄像头：设置 YIZHI_FAKE_MEDIA=1 时 Chromium 会用假摄像头
// YIZHI_FAKE_MEDIA_UI=0 可关掉「自动同意」，用于验证 request handler 确实被调用
if (process.env.YIZHI_FAKE_MEDIA === '1') {
  app.commandLine.appendSwitch('use-fake-device-for-media-stream');
  if (process.env.YIZHI_FAKE_MEDIA_UI !== '0') {
    app.commandLine.appendSwitch('use-fake-ui-for-media-stream');
  }
}

// ---- 运行状态 ----
let mainWindow = null;
let backend = null;
let backendExit = null;
let backendPort = BASE_PORT;
let backendUrl = `http://${HOST}:${BASE_PORT}`;
let quitting = false;
let startupStartedAt = 0;
let logTail = [];

function timestamp() {
  return new Date().toISOString();
}

function log(message) {
  const line = `[${timestamp()}] ${message}`;
  logTail.push(line);
  if (logTail.length > LOG_TAIL_LINES) logTail.shift();
  console.log(line);
  try {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    fs.appendFileSync(LOG_FILE, line + '\n', 'utf8');
  } catch {
    // 日志写失败不影响启动
  }
}

function tailText() {
  return logTail.join('\n');
}

async function pathExists(target) {
  try {
    await fs.promises.access(target);
    return true;
  } catch {
    return false;
  }
}

/** 打包模式用内置 embeddable Python；dev 模式用宿主 Python（可用 YIZHI_PYTHON 指定）。 */
function resolvePythonCommand() {
  if (IS_PACKAGED) return RUNTIME_PYTHON;
  return process.env.YIZHI_PYTHON || 'python';
}

function childEnv() {
  const env = {
    ...process.env,
    APP_HOST: HOST,
    APP_PORT: String(backendPort),
    SERVE_WEB: '1',
    WEB_DIST,
    APP_LOG_LEVEL: 'info',
    PYTHONUNBUFFERED: '1',
    PYTHONIOENCODING: 'utf-8',
  };
  if (DATA_DIR) env.APP_DATA_DIR = DATA_DIR;
  return env;
}

async function spawnBackend() {
  const python = resolvePythonCommand();
  if (IS_PACKAGED && !(await pathExists(python))) {
    return { error: `找不到内置 Python：${python}\n请重新解压/安装桌面版。` };
  }
  if (IS_PACKAGED && !(await pathExists(path.join(SERVER_DIR, 'app', 'main.py')))) {
    return { error: `后端代码缺失：${path.join(SERVER_DIR, 'app', 'main.py')}` };
  }

  const args = [
    '-m',
    'uvicorn',
    'app.main:app',
    '--host',
    HOST,
    '--port',
    String(backendPort),
    '--log-level',
    'info',
  ];

  startupStartedAt = Date.now();
  log(`启动后端：${python} ${args.join(' ')}`);
  log(`工作目录：${SERVER_DIR}；端口：${backendPort}`);

  backendExit = null;
  const child = spawn(python, args, {
    cwd: SERVER_DIR,
    env: childEnv(),
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  backend = child;

  const pipe = (stream, tag) => {
    stream.setEncoding('utf8');
    stream.on('data', (chunk) => {
      for (const line of chunk.split(/\r?\n/)) {
        if (!line.trim()) continue;
        log(`[${tag}] ${line}`);
      }
    });
  };
  pipe(child.stdout, 'server');
  pipe(child.stderr, 'server');

  child.on('error', (err) => {
    log(`后端进程错误：${err.message}`);
    backendExit = { code: null, signal: null, error: err.message };
  });
  child.on('exit', (code, signal) => {
    backendExit = { code, signal };
    backend = null;
    if (quitting) {
      log(`后端已退出（code=${code} signal=${signal}），应用正在关闭`);
      return;
    }
    log(`后端意外退出（code=${code} signal=${signal}）`);
    if (mainWindow && !mainWindow.isDestroyed()) {
      showErrorPage(
        '后端已停止',
        '后端进程意外退出，界面无法继续使用。请关闭本窗口后重新启动。',
        tailText()
      );
    }
  });

  return { child };
}

function probePort(port) {
  return new Promise((resolve) => {
    const socket = net.connect({ host: HOST, port });
    const finish = (open) => {
      socket.destroy();
      resolve(open);
    };
    socket.setTimeout(PORT_PROBE_TIMEOUT_MS);
    socket.once('connect', () => finish(true));
    socket.once('timeout', () => finish(false));
    socket.once('error', () => finish(false));
  });
}

function probeHealth(port) {
  return new Promise((resolve) => {
    const req = http.get(`http://${HOST}:${port}/api/health`, { timeout: HEALTH_TIMEOUT_MS }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => {
        body += chunk;
      });
      res.on('end', () => {
        try {
          resolve(JSON.parse(body));
        } catch {
          resolve(null);
        }
      });
    });
    req.on('timeout', () => {
      req.destroy();
      resolve(null);
    });
    req.on('error', () => resolve(null));
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForBackend() {
  const deadline = Date.now() + READY_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (backendExit) {
      return {
        ok: false,
        reason: '后端进程启动失败或提前退出',
        detail: backendExit.error || `exit code=${backendExit.code}`,
      };
    }
    const health = await probeHealth(backendPort);
    if (health && health.ok) {
      const elapsed = Date.now() - startupStartedAt;
      log(`后端就绪，端口 ${backendPort}，耗时 ${elapsed} ms；health=${JSON.stringify(health)}`);
      return { ok: true, health, elapsed };
    }
    await sleep(POLL_INTERVAL_MS);
  }
  return { ok: false, reason: `后端 ${READY_TIMEOUT_MS / 1000}s 内未就绪`, detail: '' };
}

function showErrorPage(title, detail, logText) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.loadFile(path.join(__dirname, 'error.html'), {
    query: {
      title,
      detail,
      log: (logText || '').slice(-6000),
      url: backendUrl,
    },
  });
}

/** 只认本机后端 origin（127.0.0.1）；绝不给 file:// 或外部站点放权。 */
function isLocalOrigin(url) {
  return typeof url === 'string' && url.startsWith(LOCAL_ORIGIN_PREFIX);
}

/**
 * 摄像头权限：前端 CameraCapture 用 getUserMedia。
 * Electron 默认不弹系统授权框，不显式放行会静默失败。
 * 放行范围仅限 http://127.0.0.1:<port>（安全上下文），其余一律拒绝。
 */
function setupPermissions() {
  const ses = session.defaultSession;

  ses.setPermissionRequestHandler((webContents, permission, callback, details) => {
    const requestUrl = (details && details.requestingUrl) || webContents.getURL();
    if (permission === 'media' && isLocalOrigin(requestUrl)) {
      const mediaTypes = details && details.mediaTypes ? details.mediaTypes.join(',') : 'unknown';
      log(`放行媒体权限：${mediaTypes}（${requestUrl}）`);
      callback(true);
      return;
    }
    log(`拒绝权限：${permission}（${requestUrl}）`);
    callback(false);
  });

  ses.setPermissionCheckHandler((webContents, permission, requestingOrigin) => {
    const allowed = permission === 'media' && isLocalOrigin(requestingOrigin);
    if (allowed) log(`权限检查通过：${permission}（${requestingOrigin}）`);
    return allowed;
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: WINDOW_WIDTH,
    height: WINDOW_HEIGHT,
    minWidth: 960,
    minHeight: 640,
    show: true,
    backgroundColor: '#0e1320',
    title: '一纸成课',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  // 立刻给出骨架，避免白屏
  mainWindow.loadFile(path.join(__dirname, 'loading.html'));

  mainWindow.on('closed', () => {
    mainWindow = null;
    app.quit();
  });

  mainWindow.webContents.on('did-finish-load', () => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    log(`页面加载完成：${mainWindow.webContents.getURL()}`);
  });
  mainWindow.webContents.on('did-fail-load', (event, code, desc, url) => {
    log(`页面加载失败 code=${code} desc=${desc} url=${url}`);
  });

  // 外部链接交给系统浏览器，窗口本身只承载本机界面
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith(backendUrl) && !url.startsWith('file://')) {
      event.preventDefault();
      if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    }
  });
}

/**
 * 依次探测 8710 / 8711 / 8712：
 *   - 已有同款后端（health.ok）→ 复用
 *   - 端口被别的程序占用 → 顺延
 *   - 空闲 → 就用它
 */
async function pickPort() {
  for (let offset = 0; offset < PORT_ATTEMPTS; offset += 1) {
    const port = BASE_PORT + offset;
    const health = await probeHealth(port);
    if (health && health.ok) {
      log(`端口 ${port} 上已有同款后端，复用`);
      return { port, reuse: true };
    }
    if (await probePort(port)) {
      log(`端口 ${port} 被其他程序占用，尝试 ${port + 1}`);
      continue;
    }
    return { port, reuse: false };
  }
  return { port: null, reuse: false };
}

async function loadApp() {
  try {
    await mainWindow.loadURL(backendUrl);
    log(`窗口已加载 ${backendUrl}`);
    const title = await mainWindow.webContents.executeJavaScript('document.title');
    const textLen = await mainWindow.webContents.executeJavaScript('document.body.innerText.length');
    log(`界面自检：title=${title} textLen=${textLen}`);
  } catch (err) {
    showErrorPage('界面加载失败', String(err && err.message ? err.message : err), tailText());
  }
}

async function boot() {
  setupPermissions();
  createWindow();

  const picked = await pickPort();
  if (!picked.port) {
    const msg = [
      `端口 ${BASE_PORT}-${BASE_PORT + PORT_ATTEMPTS - 1} 均被占用，无法启动本地服务。`,
      '请关闭占用端口的程序后重试：',
      `  netstat -ano | findstr :${BASE_PORT}`,
    ].join('\n');
    log(msg);
    showErrorPage('端口被占用', msg, tailText());
    return;
  }
  backendPort = picked.port;
  backendUrl = `http://${HOST}:${backendPort}`;

  if (picked.reuse) {
    await loadApp();
    return;
  }

  const started = await spawnBackend();
  if (started.error) {
    log(started.error);
    showErrorPage('后端无法启动', started.error, tailText());
    return;
  }

  const ready = await waitForBackend();
  if (!ready.ok) {
    const msg = `${ready.reason}\n${ready.detail || ''}\n\n日志：${LOG_FILE}`;
    log(msg);
    showErrorPage('后端启动失败', msg, tailText());
    return;
  }

  await loadApp();
}

function killBackend() {
  if (!backend || !backend.pid) return;
  const pid = backend.pid;
  backend = null;
  log(`结束后端进程 pid=${pid}`);
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid', String(pid), '/T', '/F'], {
      windowsHide: true,
      stdio: 'ignore',
    });
  } else {
    try {
      process.kill(pid, 'SIGTERM');
    } catch {
      // 已退出
    }
  }
}

ipcMain.on('desktop:open-log', () => {
  if (fs.existsSync(LOG_FILE)) shell.openPath(LOG_FILE);
});
ipcMain.on('desktop:quit', () => app.quit());

// ---- 单实例锁：避免两个进程抢端口 ----
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.setAppUserModelId('com.td12137.yizhichengke');

  app.whenReady().then(boot).catch((err) => {
    log(`启动异常：${err && err.stack ? err.stack : err}`);
    showErrorPage('启动异常', String(err), tailText());
  });

  app.on('before-quit', () => {
    quitting = true;
    killBackend();
  });

  app.on('window-all-closed', () => {
    app.quit();
  });

  process.on('exit', () => {
    quitting = true;
    killBackend();
  });

  process.on('SIGINT', () => app.quit());
  process.on('SIGTERM', () => app.quit());
}
