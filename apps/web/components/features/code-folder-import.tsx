"use client";

import * as React from "react";
import { FolderOpen } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import {
  classifyLocalCandidates,
  preserveRootRelativePath,
  sha256Hex,
  summarizePlan,
} from "@/lib/code-folder-import";
import type { CodeFolderPlanResponse } from "@/lib/types";

type Props = {
  sourceId: string;
  onImported?: () => void;
};

type LocalFile = {
  relativePath: string;
  file: File;
  sizeBytes: number;
  sha256?: string;
  selected?: boolean;
};

export function CodeFolderImport({ sourceId, onImported }: Props) {
  const t = useTranslations("CodeFolderImport");
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [busy, setBusy] = React.useState(false);
  const [phase, setPhase] = React.useState("");
  const [rootName, setRootName] = React.useState("");
  const [files, setFiles] = React.useState<LocalFile[]>([]);
  const [plan, setPlan] = React.useState<CodeFolderPlanResponse | null>(null);

  React.useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.setAttribute("webkitdirectory", "");
    el.setAttribute("directory", "");
  }, []);

  async function onPick(list: FileList | null) {
    if (!list || list.length === 0) return;
    const arr = Array.from(list);
    const firstRel =
      (arr[0] as File & { webkitRelativePath?: string }).webkitRelativePath ||
      arr[0].name;
    const root = firstRel.replace(/\\/g, "/").split("/")[0] || "code";
    setRootName(root);
    setPlan(null);
    setBusy(true);
    setPhase(t("scanning"));
    try {
      const classified = classifyLocalCandidates(
        arr.map((f) => ({
          relativePath:
            (f as File & { webkitRelativePath?: string }).webkitRelativePath ||
            f.name,
          sizeBytes: f.size,
        })),
        root,
      );
      const locals: LocalFile[] = [];
      for (let i = 0; i < arr.length; i += 1) {
        const file = arr[i];
        const meta = classified[i];
        const relativePath = preserveRootRelativePath(
          (file as File & { webkitRelativePath?: string }).webkitRelativePath ||
            file.name,
          root,
        );
        if (meta.rejected || !meta.defaultSelected) continue;
        const buf = await file.arrayBuffer();
        const bytes = new Uint8Array(buf);
        if (bytes.includes(0)) continue;
        const sha = await sha256Hex(buf);
        locals.push({
          relativePath,
          file,
          sizeBytes: file.size,
          sha256: sha,
          selected: true,
        });
        if ((i + 1) % 20 === 0) {
          setPhase(t("scanProgress", { done: i + 1, total: arr.length }));
        }
      }
      setFiles(locals);
      setPhase(t("planning"));
      const planRes = await api.planCodeFolder(sourceId, {
        root_name: root,
        items: locals.map((f) => ({
          relative_path: f.relativePath,
          sha256: f.sha256!,
          size_bytes: f.sizeBytes,
        })),
      });
      setPlan(planRes);
      setPhase("");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("scanFailed"));
      setPhase("");
    } finally {
      setBusy(false);
    }
  }

  async function uploadPlan() {
    if (!plan) return;
    const summary = summarizePlan(plan.items);
    const wanted = new Set(summary.uploadable.map((i) => i.relative_path));
    const byPath = new Map(files.map((f) => [f.relativePath, f]));
    setBusy(true);
    let ok = 0;
    try {
      for (const rel of wanted) {
        const local = byPath.get(rel);
        if (!local?.sha256) continue;
        setPhase(t("uploadingOne", { name: rel }));
        await api.uploadCodeFolderFile(sourceId, {
          file: local.file,
          relative_path: rel,
          root_name: rootName,
          sha256: local.sha256,
        });
        ok += 1;
      }
      if (ok > 0) {
        toast.success(t("uploaded", { count: ok }));
        onImported?.();
      } else {
        toast.message(t("nothingToUpload"));
      }
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("uploadFailed"));
    } finally {
      setBusy(false);
      setPhase("");
    }
  }

  const summary = plan ? summarizePlan(plan.items) : null;

  return (
    <div className="rounded-lg border border-border bg-card p-4 shadow-sm">
      <div className="mb-1 flex items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold">{t("title")}</div>
          <p className="text-xs leading-5 text-muted-foreground">{t("description")}</p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
        >
          <FolderOpen className="mr-1 size-4" />
          {t("chooseFolder")}
        </Button>
        <input
          ref={inputRef}
          type="file"
          className="hidden"
          multiple
          onChange={(e) => void onPick(e.target.files)}
        />
      </div>
      {phase ? <p className="mt-2 text-xs text-muted-foreground">{phase}</p> : null}
      {summary ? (
        <div className="mt-3 space-y-2 text-xs">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label={t("new")} value={summary.counts.new} />
            <Stat label={t("changed")} value={summary.counts.changed} />
            <Stat label={t("unchanged")} value={summary.counts.unchanged} />
            <Stat label={t("rejected")} value={summary.counts.rejected} />
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-muted-foreground">
              {t("uploadHint", { count: summary.uploadable.length })}
            </span>
            <Button
              type="button"
              size="sm"
              disabled={busy || summary.uploadable.length === 0}
              onClick={() => void uploadPlan()}
            >
              {t("startUpload")}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border bg-background px-2 py-1">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="text-sm font-semibold">{value}</div>
    </div>
  );
}
