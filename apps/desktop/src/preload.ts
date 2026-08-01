import { contextBridge, ipcRenderer } from "electron";

import { DESKTOP_CHANNELS, type DataRootInfo, type UpdateState } from "./channels";

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
  appInfo: () => ipcRenderer.invoke(DESKTOP_CHANNELS.appInfo),
  checkForUpdates: () => ipcRenderer.invoke(DESKTOP_CHANNELS.checkForUpdates),
  onUpdateState: (listener: (state: UpdateState) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, state: UpdateState) => {
      listener(state);
    };
    ipcRenderer.on(DESKTOP_CHANNELS.updateState, handler);
    return () => ipcRenderer.removeListener(DESKTOP_CHANNELS.updateState, handler);
  },
  getDataRoot: () => ipcRenderer.invoke(DESKTOP_CHANNELS.getDataRoot),
  setDataRoot: (root: string) => ipcRenderer.invoke(DESKTOP_CHANNELS.setDataRoot, root),
  chooseDataRoot: () => ipcRenderer.invoke(DESKTOP_CHANNELS.chooseDataRoot),
});

contextBridge.exposeInMainWorld("sagDesktop", bridge);
