export const DESKTOP_CHANNELS = {
  appInfo: "desktop:app-info",
  checkForUpdates: "desktop:check-for-updates",
  updateState: "desktop:update-state",
  getDataRoot: "desktop:get-data-root",
  setDataRoot: "desktop:set-data-root",
  chooseDataRoot: "desktop:choose-data-root",
} as const;

export type UpdateState =
  | { status: "idle" }
  | { status: "checking" }
  | { status: "available"; version: string }
  | { status: "not-available" }
  | { status: "downloading"; percent: number }
  | { status: "downloaded"; version: string }
  | { status: "error"; message: string };

export interface DataRootInfo {
  root: string;
  databaseUrl: string;
  dataDir: string;
  uploadDir: string;
  modelsDir: string;
  source: "override" | "default";
  restartRequired: boolean;
}

