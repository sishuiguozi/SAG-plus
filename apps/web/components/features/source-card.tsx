"use client";

import Link from "next/link";
import * as React from "react";
import { FileText, Network, Puzzle, Trash2 } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import { api, ApiError } from "@/lib/api";
import type { Source } from "@/lib/types";
import { relativeTime } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EditSourceDialog } from "@/components/features/edit-source-dialog";
import { useApp } from "@/components/features/app-shell";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export function SourceCard({ source, onChanged }: { source: Source; onChanged?: () => void }) {
  const t = useTranslations("SourceCard");
  const locale = useLocale();
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  const [deletePassword, setDeletePassword] = React.useState("");
  const [deleteBusy, setDeleteBusy] = React.useState(false);
  const { timezone } = useApp();
  const total = source.document_count ?? 0;
  const ready = source.ready_document_count ?? 0;
  const paused = source.paused_document_count ?? 0;
  const failed = source.failed_document_count ?? 0;
  const pending = source.pending_document_count ?? Math.max(0, total - ready - failed);
  const activePending = Math.max(0, pending - paused);

  async function deleteSource() {
    if (!deletePassword.trim()) {
      toast.error(t("deletePasswordRequired"));
      return false; // 保持弹窗打开
    }
    setDeleteBusy(true);
    try {
      await api.deleteSource(source.id, deletePassword.trim());
      toast.success(t("deleted"));
      onChanged?.();
      return true;
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("deleteFailed"));
      return false; // 密码错误等失败时保持弹窗打开
    } finally {
      setDeleteBusy(false);
    }
  }

  return (
    <div className="group/source relative h-full min-w-0">
      <Link
        href={`/knowledge/${source.id}`}
        className="flex h-full min-w-0 flex-col rounded-lg border bg-card p-5 shadow-soft transition-all duration-150 ease-smooth hover:border-foreground/15 hover:shadow-lift focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <div className="flex min-w-0 items-start justify-between gap-3 pr-20">
          <h3 className="min-w-0 break-words font-display text-lg font-medium leading-tight text-foreground">
            {source.name}
          </h3>
        </div>
        <p className="mb-4 mt-1.5 line-clamp-2 min-h-[2.5rem] text-sm text-muted-foreground">
          {source.description || t("noDescription")}
        </p>
        <div className="mb-4 grid grid-cols-2 gap-2 text-xs tabular-nums">
          <span className="rounded-md bg-muted px-2.5 py-1.5 text-muted-foreground">
            {t("totalFiles", { count: total })}
          </span>
          <span className="rounded-md bg-emerald-500/10 px-2.5 py-1.5 text-emerald-700 dark:text-emerald-300">
            {t("indexedFiles", { count: ready })}
          </span>
          <span className="rounded-md bg-amber-500/10 px-2.5 py-1.5 text-amber-700 dark:text-amber-300">
            {t("pendingFiles", { count: activePending })}
          </span>
          <span className="rounded-md bg-sky-500/10 px-2.5 py-1.5 text-sky-700 dark:text-sky-300">
            {t("pausedFiles", { count: paused })}
          </span>
          <span className="rounded-md bg-destructive/10 px-2.5 py-1.5 text-destructive">
            {t("failedFiles", { count: failed })}
          </span>
        </div>
        <div className="mt-auto flex flex-wrap items-center gap-x-4 gap-y-2 border-t pt-3 text-xs tabular-nums text-muted-foreground">
          <span className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap">
            <FileText className="size-3.5 shrink-0" />
            {t("documents", { count: source.document_count })}
          </span>
          <span className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap">
            <Puzzle className="size-3.5 shrink-0" />
            {t("chunks", { count: source.chunk_count })}
          </span>
          <span className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap">
            <Network className="size-3.5 shrink-0" />
            {t("events", { count: source.event_count })}
          </span>
          <span className="ml-auto shrink-0 whitespace-nowrap">
            {relativeTime(source.updated_at, timezone, locale)}
          </span>
        </div>
      </Link>

      <div className="absolute right-5 top-5 z-20 flex items-center gap-1 opacity-0 transition-opacity group-hover/source:opacity-100 group-focus-within/source:opacity-100">
        <EditSourceDialog
          source={source}
          onUpdated={onChanged}
          tooltipSide="bottom"
          buttonClassName="bg-background/95 shadow-soft backdrop-blur-sm"
        />
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={t("delete")}
              title={t("delete")}
              onClick={() => setConfirmDelete(true)}
              className="bg-background/95 text-muted-foreground shadow-soft backdrop-blur-sm hover:text-destructive"
            >
              <Trash2 className="size-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">{t("delete")}</TooltipContent>
        </Tooltip>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={(open) => {
          setConfirmDelete(open);
          if (!open) setDeletePassword("");
        }}
        title={t("delete")}
        description={t("deleteDescription", { name: source.name })}
        confirmLabel={t("delete")}
        onConfirm={deleteSource}
      >
        <Field className="px-6 pb-1">
          <FieldLabel htmlFor="delete-source-password">{t("deletePasswordLabel")}</FieldLabel>
          <Input
            id="delete-source-password"
            type="password"
            value={deletePassword}
            onChange={(event) => setDeletePassword(event.target.value)}
            placeholder={t("deletePasswordPlaceholder")}
            autoComplete="current-password"
            disabled={deleteBusy}
            onKeyDown={(event) => {
              if (event.key === "Enter" && deletePassword.trim()) {
                event.preventDefault();
                void deleteSource();
              }
            }}
          />
          <FieldDescription>{t("deletePasswordHelp")}</FieldDescription>
        </Field>
      </ConfirmDialog>
    </div>
  );
}
