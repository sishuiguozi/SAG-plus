import type { LocalModelManagerStatus, ModelConfig } from "./types";

export type LocalModelAction = "backend" | "download" | null;

export function localEmbeddingTestDraftKey(
  embeddingProvider: ModelConfig["embedding_provider"],
  modelFile: string,
  nCtx: number,
  nThreads: number,
): string {
  return JSON.stringify([embeddingProvider, modelFile, nCtx, nThreads]);
}

export function isLocalEmbeddingTestResponseCurrent(
  requestDraftKey: string,
  currentDraftKey: string,
): boolean {
  return requestDraftKey === currentDraftKey;
}

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

export function isLocalEmbeddingTestDisabled(
  embeddingProvider: ModelConfig["embedding_provider"],
  modelFile: string,
  status: LocalModelManagerStatus,
  action: LocalModelAction,
  testing: boolean,
): boolean {
  const activeModel = status.models.find(
    (model) => model.file_name === modelFile,
  );
  return (
    testing ||
    action !== null ||
    embeddingProvider !== "local" ||
    status.backend.status !== "ready" ||
    activeModel?.status !== "ready"
  );
}
