"use client";

import * as React from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import type { TreeSitterResourceStatus } from "@/lib/types";

export function TreeSitterResourceCard({ embedded = false }: { embedded?: boolean } = {}) {
  const t = useTranslations("TreeSitterResource");
  const [status, setStatus] = React.useState<TreeSitterResourceStatus | null>(null);
  const [busy, setBusy] = React.useState(false);

  const refresh = React.useCallback(async () => {
    try {
      setStatus(await api.getTreeSitterStatus());
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("loadFailed"));
    }
  }, [t]);

  React.useEffect(() => {
    void refresh();
  }, [refresh]);

  React.useEffect(() => {
    const state = String(status?.state || "");
    if (!["downloading", "repairing"].includes(state)) return;
    const timer = window.setInterval(() => {
      void refresh();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [status?.state, refresh]);

  async function run(action: "download" | "pause" | "resume" | "repair") {
    setBusy(true);
    try {
      const next =
        action === "download"
          ? await api.downloadTreeSitter()
          : action === "pause"
            ? await api.pauseTreeSitter()
            : action === "resume"
              ? await api.resumeTreeSitter()
              : await api.repairTreeSitter();
      setStatus(next);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setBusy(false);
    }
  }

  const state = String(status?.state || "missing");
  const rawProgress = Number(status?.progress || 0);
  const progress = Math.round(rawProgress > 0 && rawProgress <= 1 ? rawProgress * 100 : rawProgress);

  return (
    <div className={embedded ? "rounded-md border bg-muted/20 p-3" : "rounded-lg border bg-card/40 p-4"}>
      {!embedded ? (
        <>
          <div className="mb-1 text-sm font-medium">{t("title")}</div>
          <p className="mb-3 text-xs text-muted-foreground">{t("description")}</p>
        </>
      ) : (
        <p className="mb-3 text-xs text-muted-foreground">{t("description")}</p>
      )}
      <div className="mb-3 grid gap-1 text-xs">
        <div>
          {t("state")}:{" "}
          {state === "missing" ||
          state === "downloading" ||
          state === "paused" ||
          state === "ready" ||
          state === "error" ||
          state === "repairing"
            ? t(`states.${state}`)
            : state}
        </div>
        <div>
          {t("languages")}: {status?.installed_languages ?? 0}/{status?.total_languages ?? 306}
        </div>
        <div>{t("version")}: {status?.version || "-"}</div>
        {status?.message ? <div className="text-muted-foreground">{status.message}</div> : null}
        {status?.error ? <div className="text-destructive">{status.error}</div> : null}
        {["downloading", "repairing"].includes(state) ? (
          <div>{t("progress")}: {progress}%</div>
        ) : null}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" disabled={busy || state === "downloading"} onClick={() => void run("download")}>
          {t("download")}
        </Button>
        <Button type="button" size="sm" variant="outline" disabled={busy || state !== "downloading"} onClick={() => void run("pause")}>
          {t("pause")}
        </Button>
        <Button type="button" size="sm" variant="outline" disabled={busy || state !== "paused"} onClick={() => void run("resume")}>
          {t("resume")}
        </Button>
        <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void run("repair")}>
          {t("repair")}
        </Button>
      </div>
    </div>
  );
}
