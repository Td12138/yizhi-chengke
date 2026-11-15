'use strict';

/**
 * npm 11 默认不执行依赖的 install 脚本，Electron 二进制可能缺失。
 * 本脚本在 npm install 后检查 node_modules/electron/dist/electron.exe，
 * 缺失时手动执行 electron/install.js（走 npmmirror）。
 */

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const ELECTRON_DIR = path.join(ROOT, 'node_modules', 'electron');
const ELECTRON_EXE = path.join(ELECTRON_DIR, 'dist', 'electron.exe');
const INSTALL_JS = path.join(ELECTRON_DIR, 'install.js');
const DEFAULT_MIRROR = 'https://npmmirror.com/mirrors/electron/';

if (fs.existsSync(ELECTRON_EXE)) {
  console.log('[ensure-electron] electron binary ok');
  process.exit(0);
}

if (!fs.existsSync(INSTALL_JS)) {
  console.log('[ensure-electron] electron package not installed yet, skip');
  process.exit(0);
}

console.log('[ensure-electron] electron binary missing, running install.js ...');
const result = spawnSync(process.execPath, [INSTALL_JS], {
  cwd: ROOT,
  stdio: 'inherit',
  env: {
    ...process.env,
    ELECTRON_MIRROR: process.env.ELECTRON_MIRROR || DEFAULT_MIRROR,
  },
});

if (result.status !== 0 || !fs.existsSync(ELECTRON_EXE)) {
  console.error('[ensure-electron] 安装失败：请手动执行 node node_modules/electron/install.js');
  process.exit(result.status || 1);
}

console.log('[ensure-electron] electron binary installed');
