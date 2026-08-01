export interface SagDesktopDataRootInfo {
  root: string;
  databaseUrl: string;
  dataDir: string;
  uploadDir: string;
  modelsDir: string;
  source: "override" | "default";
  restartRequired: boolean;
}

export interface SagDesktopBridge {
  readonly isDesktop: true;
  readonly platform: string;
  appInfo(): Promise<{ version: string; platform: string; arch: string }>;
  checkForUpdates(): Promise<{ supported: boolean }>;
  onUpdateState(listener: (state: unknown) => void): () => void;
  getDataRoot(): Promise<SagDesktopDataRootInfo>;
  setDataRoot(root: string): Promise<SagDesktopDataRootInfo>;
  chooseDataRoot(): Promise<{ canceled: boolean; dataRoot: SagDesktopDataRootInfo }>;
}

declare global {
  interface Window {
    sagDesktop?: SagDesktopBridge;
  }
}

export {};
