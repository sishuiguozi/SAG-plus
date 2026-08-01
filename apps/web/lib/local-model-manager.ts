import type { LocalModelManagerStatus } from "./types";

export type LocalModelAction = "backend" | "download" | null;

export function toggleLocalModelSelection(
  selected: string[],
  fileName: string,
  checked: boolean,
): string[] {
  return checked
    ? [...new Set([...selected, fileName])]
    : selected.filter((file) => file !== fileName);
}

export function isLocalModelDownloadDisabled(
  status: LocalModelManagerStatus,
  selected: string[],
  action: LocalModelAction,
): boolean {
  return action !== null || selected.length === 0 || status.backend.status !== "ready";
}
