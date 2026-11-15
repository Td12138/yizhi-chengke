'use strict';

/**
 * 一纸成课桌面版主进程。
 *
 * 职责：
 *   1. whenReady 第一件事就出窗口（loading.html），不等后端、不探端口
 *   2. 把启动拆成四步并实时推给加载页：准备运行时 → 启动本地服务 → 加载界面 → 完成
 *      （端口顺延、每步耗时、总耗时都在加载页可见，避免用户以为没反应）
 *   3. 在 8710 → 8711 → 8712 里选一个空闲端口，启动 FastAPI 后端
 *      （dev：宿主 Python；打包：内置 embeddable Python）
 *   4. 先在隐藏窗口里加载主界面，加载好再切换，切换前停留 200ms，避免闪跳
 *   5. 首次运行标记：userData/first-run.done；首次加载主界面带 ?tour=1，成功后才写标记
 *   6. 退出时 taskkill 进程树，确保不留 python.exe
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
const PROCESS_START = Date.now(); // 进程启动基准（主进程模块最早可测点）
const HOST = '127.0.0.1';
const BASE_PORT = Number(process.env.APP_PORT || 8710);
const PORT_ATTEMPTS = 3;
const READY_TIMEOUT_MS = Number(process.env.YIZHI_READY_TIMEOUT_MS || 40000);
// 40s 而不是 15s：portable 首次要解压 512 MB 到 %TEMP%，Windows 实时防护会逐个文件扫描，
// 本机实测解压 + 首次导入就绪要 40~50s，15s 会直接把评委送进错误页。
const POLL_INTERVAL_MS = 300;
const HEALTH_TIMEOUT_MS = 1200;
const PORT_PROBE_TIMEOUT_MS = 700;
const LOG_TAIL_LINES = 40;
const WINDOW_WIDTH = 1280;
const WINDOW_HEIGHT = 860;
const WINDOW_MIN_WIDTH = 960;
const WINDOW_MIN_HEIGHT = 640;
const SLOW_HINT_FIRST_MS = 12000; // 12s：首次解压运行时提示
const SLOW_HINT_SECOND_MS = 25000; // 25s：仍在启动，引导看日志
const EXTEND_WAIT_MS = 30000; // 错误页「再等 30 秒」延长窗口
const DONE_HOLD_MS = 400; // 「完成」态至少停留时间，避免加载页闪跳
const STEP_MIN_ACTIVE_MS = 300; // 每步 active 至少显示这么久，否则动画/文字来不及看清
const MIN_STARTUP_PACING_MS = 1200; // 总耗时过短时把节奏放慢到这个长度
const PAINT_WAIT_MS = 1500; // 切主界面前等首帧的兜底
const FIRST_RUN_MARKER = 'first-run.done';
const TOUR_QUERY = '?tour=1';
const MIN_UI_TEXT_LEN = 80; // 主界面至少要有这么多可见文字，否则视为空白/报错页

// 加载页步骤（顺序即展示顺序）
const STEP_RUNTIME = 'runtime';
const STEP_SERVER = 'server';
const STEP_UI = 'ui';
const STEP_DONE = 'done';
const STEP_KEYS = [STEP_RUNTIME, STEP_SERVER, STEP_UI, STEP_DONE];
const STEP_LABELS = {
  [STEP_RUNTIME]: '准备运行时',
  [STEP_SERVER]: '启动本地服务',
  [STEP_UI]: '加载界面',
  [STEP_DONE]: '完成',
};
const STATUS_PENDING = 'pending';
const STATUS_ACTIVE = 'active';
const STATUS_DONE = 'done';
const STATUS_FAILED = 'failed';

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
const FIRST_RUN_PATH = path.join(app.getPath('userData'), FIRST_RUN_MARKER);
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
let loadingWindow = null; // 加载页窗口（先出现，切换后销毁）
let appWindow = null; // 主界面窗口（隐藏加载，加载成功后显示）
let backend = null;
let backendExit = null;
let backendPort = BASE_PORT;
let backendUrl = `http://${HOST}:${BASE_PORT}`;
let quitting = false;
let startupStartedAt = 0;
let logTail = [];

// ---- 加载页进度状态 ----
const progressSteps = STEP_KEYS.map((key) => ({
  key,
  label: STEP_LABELS[key],
  status: STATUS_PENDING,
  startedAt: null,
  endedAt: null,
  detail: '',
}));
let progressNote = '';
let slowHintText = '';
let slowHintTimers = [];
let loadingPageReady = false;
let startupReady = false;
let activeStepKey = null;

// 视觉队列：串行执行加载页的状态更新（含每步最小展示时长），与真实启动流程解耦
let visualQueue = Promise.resolve();

function queueVisual(task) {
  visualQueue = visualQueue.then(task).catch(() => {
    // 视觉更新失败不影响启动
  });
  return visualQueue;
}

function flushVisual() {
  return visualQueue;
}

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

function sinceStart() {
  return Date.now() - PROCESS_START;
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

// ---- 加载页进度 ----

function findStep(key) {
  return progressSteps.find((step) => step.key === key);
}

function buildProgressPayload() {
  return {
    startedAt: PROCESS_START,
    steps: progressSteps.map((step) => ({
      key: step.key,
      label: step.label,
      status: step.status,
      startedAt: step.startedAt,
      endedAt: step.endedAt,
      detail: step.detail,
    })),
    note: progressNote,
    slowHint: slowHintText,
  };
}

/** 把当前进度推给加载页（页面未就绪时先攒着，did-finish-load 后统一渲染）。 */
function pushProgress() {
  if (!loadingWindow || loadingWindow.isDestroyed() || loadingPageReady === false) return;
  const json = JSON.stringify(buildProgressPayload()).replace(/</g, '\\u003c');
  loadingWindow.webContents
    .executeJavaScript(`window.__startup && window.__startup.render(${json})`)
    .catch(() => {
      // 页面正在导航/已销毁，忽略
    });
}

function beginStep(key, detail) {
  const step = findStep(key);
  if (!step) return;
  activeStepKey = key;
  log(`[启动] ${step.label} …${detail ? `（${detail}）` : ''}`);
  // 视觉更新走队列：保证上一段的「最小展示时长」先满足，但绝不阻塞真实启动流程
  queueVisual(() => {
    step.status = STATUS_ACTIVE;
    step.startedAt = Date.now();
    step.endedAt = null;
    if (detail !== undefined) step.detail = detail;
    pushProgress();
  });
}

function endStep(key, detail) {
  const step = findStep(key);
  if (!step) return;
  if (activeStepKey === key) activeStepKey = null;
  queueVisual(async () => {
    const startedAt = step.startedAt === null ? Date.now() : step.startedAt;
    const shown = Date.now() - startedAt;
    if (shown < STEP_MIN_ACTIVE_MS) {
      await sleep(STEP_MIN_ACTIVE_MS - shown);
    }
    step.status = STATUS_DONE;
    step.endedAt = Date.now();
    if (detail !== undefined) step.detail = detail;
    log(
      `[启动] ${step.label} 完成，耗时 ${step.endedAt - startedAt} ms${detail ? `（${detail}）` : ''}`
    );
    pushProgress();
  });
}

function failStep(key, message) {
  const step = findStep(key);
  if (!step) return;
  if (activeStepKey === key) activeStepKey = null;
  queueVisual(() => {
    if (step.startedAt === null) step.startedAt = Date.now();
    step.status = STATUS_FAILED;
    step.endedAt = Date.now();
    step.detail = message;
    pushProgress();
  });
}

function setNote(text) {
  progressNote = text || '';
  log(`[启动] 提示：${progressNote}`);
  pushProgress();
}

function clearSlowHints() {
  for (const timer of slowHintTimers) clearTimeout(timer);
  slowHintTimers = [];
}

/** 回到加载页（用于「再等 30 秒」续等）。 */
function showLoadingPage() {
  if (!loadingWindow || loadingWindow.isDestroyed()) return;
  loadingPageReady = false;
  loadingWindow.loadFile(path.join(__dirname, 'loading.html'));
}

/** 用户选择续等：回到加载页，把「启动本地服务」重新置为进行中。 */
function resumeWaiting(note) {
  log(`继续等待：${note}`);
  showLoadingPage();
  queueVisual(() => {
    const step = findStep(STEP_SERVER);
    if (step) {
      step.status = STATUS_ACTIVE;
      step.startedAt = Date.now();
      step.endedAt = null;
      step.detail = '继续等待后端就绪…';
    }
    progressNote = note;
    pushProgress();
  });
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
    if (startupReady) {
      showErrorPage(
        '后端已停止',
        '后端进程意外退出，界面无法继续使用。请关闭本窗口后重新启动。',
        tailText()
      );
    }
  });

  return { child, python };
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
  let deadline = Date.now() + READY_TIMEOUT_MS;
  while (true) {
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

    // 40s 到期：先给用户「再等 30 秒」的机会，同时后台继续探测 health
    const decision = await waitForTimeoutDecision();
    if (decision === 'ready') continue; // 期间后端就绪，下一轮立刻命中
    if (decision === 'extend') {
      resumeWaiting(`已按你的要求再等 ${EXTEND_WAIT_MS / 1000}s`);
      deadline = Date.now() + EXTEND_WAIT_MS;
      continue;
    }
    return { ok: false, reason: `后端 ${READY_TIMEOUT_MS / 1000}s 内未就绪`, detail: '' };
  }
}

/**
 * 超时决策：显示带「再等 30 秒」按钮的错误页，返回 'ready' | 'extend' | 'quit'。
 * 不重启进程，只延长轮询窗口；期间每 300ms 继续探 health。
 */
function waitForTimeoutDecision() {
  return new Promise((resolve) => {
    let settled = false;
    let poll = null;

    const finish = (decision) => {
      if (settled) return;
      settled = true;
      if (poll) clearInterval(poll);
      ipcMain.removeListener('desktop:wait-more', onWaitMore);
      ipcMain.removeListener('desktop:quit', onQuit);
      log(`超时等待结束：${decision}`);
      resolve(decision);
    };

    const onWaitMore = () => finish('extend');
    const onQuit = () => finish('quit');
    ipcMain.on('desktop:wait-more', onWaitMore);
    ipcMain.on('desktop:quit', onQuit);

    poll = setInterval(async () => {
      if (settled) return;
      const health = await probeHealth(backendPort);
      if (health && health.ok) finish('ready');
    }, POLL_INTERVAL_MS);

    failStep(STEP_SERVER, `后端 ${READY_TIMEOUT_MS / 1000}s 内未就绪，仍在后台继续检测`);
    showErrorPage(
      '后端启动较慢',
      [
        `本地服务 ${READY_TIMEOUT_MS / 1000}s 内没有就绪，但它可能只是被实时防护拖慢了，并没有挂掉。`,
        `点「再等 30 秒」会继续等待并轮询 /api/health，不用重启程序。`,
        '',
        `日志：${LOG_FILE}`,
      ].join('\n'),
      tailText(),
      { waitMore: true }
    );
  });
}

/** 当前可见窗口（错误页优先落在用户正在看的那个窗口）。 */
function visibleWindow() {
  if (appWindow && !appWindow.isDestroyed() && appWindow.isVisible()) return appWindow;
  if (loadingWindow && !loadingWindow.isDestroyed()) return loadingWindow;
  if (appWindow && !appWindow.isDestroyed()) return appWindow;
  return null;
}

function showErrorPage(title, detail, logText, options) {
  const target = visibleWindow();
  if (!target) return;
  const query = {
    title,
    detail,
    log: (logText || '').slice(-6000),
    url: backendUrl,
  };
  if (options && options.waitMore) query.waitMore = '1';
  target.loadFile(path.join(__dirname, 'error.html'), { query });
}

/** 只认本机后端 origin（127.0.0.1）；绝不给 file:// 或外部站点放权。 */
function isLocalOrigin(url) {
  return typeof url === 'string' && url.startsWith(LOCAL_ORIGIN_PREFIX);
}

/**
 * 导航判定：必须拿 origin 精确比较，backendUrl 自带端口。
 * 用 startsWith 会放过 http://127.0.0.1:87101/ 这类「前缀相同、端口不同」的站点。
 */
function isBackendOrigin(url) {
  if (typeof url !== 'string') return false;
  try {
    return new URL(url).origin === backendUrl;
  } catch {
    return false;
  }
}

/** 归档附件（原图 / PDF / DOCX / 文本）路径：这些链接允许交给系统浏览器预览下载 */
const ATTACHMENT_PATH_PREFIX = '/api/sources/';

function isBackendAttachment(url) {
  if (!isBackendOrigin(url)) return false;
  try {
    return new URL(url).pathname.startsWith(ATTACHMENT_PATH_PREFIX);
  } catch {
    return false;
  }
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

/**
 * 本机界面的 CSP：只给主文档加响应头，接口 / SSE / 静态资源原样返回。
 * 与 Vite 开发服务器无关（dev 走 5173 的 HMR，不经过这里），因此不会破坏热更新。
 */
const APP_CSP = [
  "default-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "media-src 'self' blob:",
  "connect-src 'self'",
  "frame-src 'self'",
].join('; ');

/**
 * 后端用 FastAPI 直接托管构建产物，没有方便改头的地方，
 * 这里在 Electron 侧补 CSP（只在主文档响应上加）。
 */
function setupCsp() {
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    if (details.resourceType !== 'mainFrame' || !isBackendOrigin(details.url)) {
      callback({ responseHeaders: details.responseHeaders });
      return;
    }
    callback({
      responseHeaders: {
        ...(details.responseHeaders || {}),
        'Content-Security-Policy': [APP_CSP],
      },
    });
  });
}

/** 外部链接交给系统浏览器，窗口本身只承载本机界面。 */
function attachNavigationHandlers(win) {
  win.webContents.setWindowOpenHandler(({ url }) => {
    // 外部站点一律交给系统浏览器；本机后端的附件链接（PDF / DOCX 预览下载）同样交出去，
    // 但同源的应用页面不开新窗口 —— 判定用 origin 精确比较，不用字符串前缀
    if (/^https?:\/\//i.test(url) && (!isBackendOrigin(url) || isBackendAttachment(url))) {
      shell.openExternal(url);
    } else if (!isBackendOrigin(url)) {
      log(`拦截新窗口：${url}`);
    }
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (event, url) => {
    // 应用自身的同源页面放行；file:// 留给本机错误页 / 加载页
    if (isBackendOrigin(url) || url.startsWith('file://')) return;
    event.preventDefault();
    log(`拦截导航：${url}`);
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
  });
  win.webContents.on('did-fail-load', (event, code, desc, url) => {
    log(`页面加载失败 code=${code} desc=${desc} url=${url}`);
  });
}

/**
 * 第一件事：把加载页窗口显示出来。
 * show:true 立即出窗（背景色与加载页一致，不会白闪）——用户先看到窗口，再看到内容，
 * 避免「双击了没反应」的错觉；冷启动时内容可能晚 0.5~1s 才画出来。
 */
function createLoadingWindow() {
  loadingWindow = new BrowserWindow({
    width: WINDOW_WIDTH,
    height: WINDOW_HEIGHT,
    minWidth: WINDOW_MIN_WIDTH,
    minHeight: WINDOW_MIN_HEIGHT,
    show: true,
    center: true,
    backgroundColor: '#f7f9fc',
    title: '一纸成课',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  log(`加载窗口可见，距进程启动 ${sinceStart()} ms`);

  loadingWindow.webContents.on('did-start-loading', () => {
    loadingPageReady = false;
  });

  loadingWindow.webContents.on('did-finish-load', () => {
    loadingPageReady = true;
    log(`加载页就绪，距进程启动 ${sinceStart()} ms`);
    pushProgress();
  });

  attachNavigationHandlers(loadingWindow);

  loadingWindow.on('closed', () => {
    loadingWindow = null;
    // 用户关掉加载页 = 不想再等；已切主界面时（startupReady）由主窗口接管
    if (!startupReady) app.quit();
  });

  loadingWindow.loadFile(path.join(__dirname, 'loading.html'));
  log(`加载窗口已创建，距进程启动 ${sinceStart()} ms`);
}

/** 主界面窗口：隐藏加载，加载好并切过去后才显示。 */
function createAppWindow() {
  const bounds = loadingWindow && !loadingWindow.isDestroyed() ? loadingWindow.getBounds() : null;
  const win = new BrowserWindow({
    width: bounds ? bounds.width : WINDOW_WIDTH,
    height: bounds ? bounds.height : WINDOW_HEIGHT,
    x: bounds ? bounds.x : undefined,
    y: bounds ? bounds.y : undefined,
    minWidth: WINDOW_MIN_WIDTH,
    minHeight: WINDOW_MIN_HEIGHT,
    show: false,
    backgroundColor: '#0e1320',
    title: '一纸成课',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      // 隐藏窗口不解码/不节流，保证切过去时已经渲染好
      backgroundThrottling: false,
    },
  });

  // 在创建时立刻挂 ready-to-show，避免加载太快错过
  win.firstPaint = new Promise((resolve) => {
    let settled = false;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      resolve(ok);
    };
    win.once('ready-to-show', () => finish(true));
    setTimeout(() => finish(false), PAINT_WAIT_MS);
  });

  win.on('closed', () => {
    appWindow = null;
    app.quit();
  });

  attachNavigationHandlers(win);
  return win;
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

function isFirstRun() {
  return !fs.existsSync(FIRST_RUN_PATH);
}

/** 首次运行标记只在主界面加载成功之后写，失败不算已用。 */
function markFirstRunDone() {
  try {
    fs.mkdirSync(path.dirname(FIRST_RUN_PATH), { recursive: true });
    fs.writeFileSync(FIRST_RUN_PATH, `${new Date().toISOString()}\n`, 'utf8');
    log(`已写首次运行标记：${FIRST_RUN_PATH}`);
  } catch (err) {
    log(`写首次运行标记失败：${err && err.message ? err.message : err}`);
  }
}

async function loadApp(firstRun) {
  const target = `${backendUrl}/${firstRun ? TOUR_QUERY : ''}`;
  beginStep(STEP_UI, target);
  log(`加载主界面：${target}${firstRun ? '（首次运行）' : ''}`);

  appWindow = createAppWindow();
  appWindow.webContents.on('did-finish-load', () => {
    log(`主界面 did-finish-load：${appWindow ? appWindow.webContents.getURL() : ''}`);
  });

  try {
    await appWindow.loadURL(target);
  } catch (err) {
    throw new StartupError(STEP_UI, `主界面加载失败：${err && err.message ? err.message : err}`);
  }

  const loadedUrl = appWindow.webContents.getURL();
  if (!loadedUrl.startsWith(backendUrl)) {
    throw new StartupError(STEP_UI, `主界面加载到了意外地址：${loadedUrl}`);
  }

  // 自检：确认渲染进程真的把页面画出来了
  let title = '';
  let textLen = 0;
  try {
    title = await appWindow.webContents.executeJavaScript('document.title');
    textLen = await appWindow.webContents.executeJavaScript('document.body.innerText.length');
    log(`界面自检：title=${title} textLen=${textLen}`);
  } catch (err) {
    log(`界面自检失败：${err && err.message ? err.message : err}`);
    throw new StartupError(STEP_UI, `界面渲染异常：${err && err.message ? err.message : err}`);
  }

  // 空白/JSON 报错页说明这个端口上的服务不是本应用的版本（常见于复用了别的进程）
  if (!String(title || '').trim() || textLen < MIN_UI_TEXT_LEN) {
    throw new StartupError(
      STEP_UI,
      [
        `界面内容异常（title="${title}"，可见文字 ${textLen} 字）。`,
        `端口 ${backendPort} 上的服务可能不是本应用的版本。`,
        '请关闭占用该端口的程序后重新启动：',
        `  netstat -ano | findstr :${backendPort}`,
      ].join('\n')
    );
  }

  endStep(STEP_UI, `已渲染：${title}`);
  return { title, textLen };
}

class StartupError extends Error {
  constructor(stepKey, message) {
    super(message);
    this.stepKey = stepKey;
  }
}

async function finishStartup(win) {
  beginStep(STEP_DONE, '正在进入主界面');
  await flushVisual(); // 等「完成」这一行真正画出来
  await sleep(DONE_HOLD_MS); // 至少停留 400ms，避免一闪而过
  endStep(STEP_DONE, '');
  await flushVisual();

  // 整体节奏：真实启动很快时也放慢到 1.2s，别让四步像闪屏
  const elapsed = sinceStart();
  if (elapsed < MIN_STARTUP_PACING_MS) {
    await sleep(MIN_STARTUP_PACING_MS - elapsed);
  }

  startupReady = true;
  clearSlowHints();

  const painted = await win.firstPaint;
  log(`主界面首帧${painted ? '已就绪' : '等待超时（强制显示）'}`);

  win.show();
  win.focus();
  log(`主界面可见，距进程启动 ${sinceStart()} ms`);

  if (loadingWindow && !loadingWindow.isDestroyed()) {
    loadingWindow.destroy();
  }
}

async function boot() {
  log(`app ready，距进程启动 ${sinceStart()} ms`);

  // 1) 先出窗口，绝不等后端
  createLoadingWindow();

  // 2) 进度条的第一步：进程一起来就算「准备运行时」
  beginStep(STEP_RUNTIME, IS_PACKAGED ? '内置运行时' : '宿主 Python');

  slowHintTimers = [
    setTimeout(() => {
      if (startupReady) return;
      slowHintText = '首次启动需要解压运行时，可能比平时慢一倍，请再等一会儿';
      log(`启动已超过 ${SLOW_HINT_FIRST_MS} ms，显示首次解压提示`);
      pushProgress();
    }, SLOW_HINT_FIRST_MS),
    setTimeout(() => {
      if (startupReady) return;
      slowHintText = '仍在启动，如果超过 1 分钟请点『查看日志』';
      log(`启动已超过 ${SLOW_HINT_SECOND_MS} ms，显示「查看日志」提示`);
      pushProgress();
    }, SLOW_HINT_SECOND_MS),
  ];

  setupPermissions();
  setupCsp();

  const firstRun = isFirstRun();
  log(`首次运行：${firstRun ? '是' : '否'}（标记 ${FIRST_RUN_PATH}）`);

  try {
    // ---- 准备运行时 ----
    const python = resolvePythonCommand();
    const pythonOk = IS_PACKAGED ? await pathExists(python) : true;
    if (!pythonOk) {
      throw new StartupError(STEP_RUNTIME, `找不到内置 Python：${python}\n请重新解压/安装桌面版。`);
    }
    endStep(STEP_RUNTIME, IS_PACKAGED ? '内置 Python 3.14' : python);

    // ---- 启动本地服务 ----
    beginStep(STEP_SERVER, '正在选择端口');
    const picked = await pickPort();
    if (!picked.port) {
      throw new StartupError(
        STEP_SERVER,
        [
          `端口 ${BASE_PORT}-${BASE_PORT + PORT_ATTEMPTS - 1} 均被占用，无法启动本地服务。`,
          '请关闭占用端口的程序后重试：',
          `  netstat -ano | findstr :${BASE_PORT}`,
        ].join('\n')
      );
    }
    backendPort = picked.port;
    backendUrl = `http://${HOST}:${backendPort}`;

    if (picked.port !== BASE_PORT) {
      setNote(`${BASE_PORT} 被占用，改用 ${picked.port}`);
    }

    if (picked.reuse) {
      setNote(`${picked.port} 上已有本地服务在运行，直接复用`);
      endStep(STEP_SERVER, `复用 ${backendUrl}`);
    } else {
      beginStep(STEP_SERVER, `正在启动 127.0.0.1:${backendPort}`);
      const started = await spawnBackend();
      if (started.error) {
        throw new StartupError(STEP_SERVER, started.error);
      }
      const ready = await waitForBackend();
      if (!ready.ok) {
        throw new StartupError(STEP_SERVER, `${ready.reason}\n${ready.detail || ''}\n\n日志：${LOG_FILE}`);
      }
      endStep(STEP_SERVER, `${backendUrl}（${ready.elapsed} ms）`);
    }

    // ---- 加载界面 ----
    await loadApp(firstRun);

    // ---- 完成：首次标记在界面加载成功之后才写 ----
    if (firstRun) markFirstRunDone();
    await finishStartup(appWindow);
  } catch (err) {
    const stepKey = err instanceof StartupError ? err.stepKey : activeStepKey || STEP_RUNTIME;
    const message = err && err.stack ? err.stack : String(err);
    log(`启动失败：${message}`);
    failStep(stepKey, err && err.message ? err.message : String(err));
    clearSlowHints();
    await flushVisual(); // 先让失败态显示出来，再切错误页
    showErrorPage('启动失败', err && err.message ? err.message : String(err), tailText());
  }
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
    const target = visibleWindow();
    if (!target) return;
    if (target.isMinimized()) target.restore();
    target.focus();
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
