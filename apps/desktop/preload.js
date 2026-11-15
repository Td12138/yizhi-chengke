'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('desktop', {
  openLog: () => ipcRenderer.send('desktop:open-log'),
  quit: () => ipcRenderer.send('desktop:quit'),
  waitMore: () => ipcRenderer.send('desktop:wait-more'),
});
