import { contextBridge, ipcRenderer } from "electron";

import type { DataRootInfo, UpdateState } from "./channels";

// 沙箱 preload 不支持 require() 相对模块，因此这里内联通道名。
// 与 ./channels（主进程使用）保持一致；新增通道时两边都要改。
const APP_INFO_CHANNEL = "desktop:app-info";
const CHECK_FOR_UPDATES_CHANNEL = "desktop:check-for-updates";
const UPDATE_STATE_CHANNEL = "desktop:update-state";
const GET_DATA_ROOT_CHANNEL = "desktop:get-data-root";
const SET_DATA_ROOT_CHANNEL = "desktop:set-data-root";
const CHOOSE_DATA_ROOT_CHANNEL = "desktop:choose-data-root";

export interface SagDesktopBridge {
  readonly isDesktop: true;
  readonly platform: NodeJS.Platform;
  appInfo(): Promise<{ version: string; platform: NodeJS.Platform; arch: string }>;
  checkForUpdates(): Promise<{ supported: boolean }>;
  onUpdateState(listener: (state: UpdateState) => void): () => void;
  getDataRoot(): Promise<DataRootInfo>;
  setDataRoot(root: string): Promise<DataRootInfo>;
  chooseDataRoot(): Promise<{ canceled: boolean; dataRoot: DataRootInfo }>;
}

const bridge: SagDesktopBridge = Object.freeze({
  isDesktop: true,
  platform: process.platform,
  appInfo: () => ipcRenderer.invoke(APP_INFO_CHANNEL),
  checkForUpdates: () => ipcRenderer.invoke(CHECK_FOR_UPDATES_CHANNEL),
  onUpdateState: (listener: (state: UpdateState) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, state: UpdateState) => {
      listener(state);
    };
    ipcRenderer.on(UPDATE_STATE_CHANNEL, handler);
    return () => ipcRenderer.removeListener(UPDATE_STATE_CHANNEL, handler);
  },
  getDataRoot: () => ipcRenderer.invoke(GET_DATA_ROOT_CHANNEL),
  setDataRoot: (root: string) => ipcRenderer.invoke(SET_DATA_ROOT_CHANNEL, root),
  chooseDataRoot: () => ipcRenderer.invoke(CHOOSE_DATA_ROOT_CHANNEL),
});

contextBridge.exposeInMainWorld("sagDesktop", bridge);
