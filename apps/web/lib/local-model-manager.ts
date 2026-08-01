import type { LocalModelManagerStatus, ModelConfig } from "./types";

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

export function isLocalEmbeddingTestDisabled(
  config: Pick<ModelConfig, "embedding_provider" | "embedding_local_model_file">,
  status: LocalModelManagerStatus,
  action: LocalModelAction,
  testing: boolean,
): boolean {
  const activeModel = status.models.find(
    (model) => model.file_name === config.embedding_local_model_file,
  );
  return (
    testing ||
    action !== null ||
    config.embedding_provider !== "local" ||
    status.backend.status !== "ready" ||
    activeModel?.status !== "ready"
  );
}
