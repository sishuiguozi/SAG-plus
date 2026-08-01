"use client";

import * as React from "react";
import { Check, Copy, Info } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import type { Doc } from "@/lib/types";
import { formatBytes, formatDateTime, formatDuration, formatTokenCount } from "@/lib/format";
import { useApp } from "@/components/features/app-shell";
import { DocStatusBadge } from "@/components/features/status-badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[5.5rem_1fr] items-start gap-3 text-xs">
      <span className="pt-0.5 text-muted-foreground">{label}</span>
      <div className="min-w-0 text-foreground">{children}</div>
    </div>
  );
}

/**
 * Parsing status detail dialog. Surface what the document list truncates:
 * stage explanation, progress, processing duration, timestamps, stats, and the
 * full (copyable) error. SAG has no per-step progress log, so duration is
 * approximated from created_at -> updated_at.
 */
export function DocumentParsingDetails({ doc }: { doc: Doc }) {
  const t = useTranslations("DocumentList");
  const locale = useLocale();
  const { timezone } = useApp();
  const [open, setOpen] = React.useState(false);
  const [copied, setCopied] = React.useState(false);

  const progress = Math.min(100, Math.max(0, Math.round(doc.progress ?? 0)));
  const showProgress =
    doc.status === "loading" || doc.status === "extracting" || doc.status === "paused";
  const duration = formatDuration(doc.created_at, doc.updated_at, locale);
  // Literal keys per branch keep next-intl's strict typed-message checker happy.
  const stageLabel = (() => {
    switch (doc.status) {
      case "loading":
        return t("stageLoading");
      case "extracting":
        return t("stageExtracting");
      case "paused":
        return t("stagePaused");
      case "ready":
        return t("stageReady");
      case "failed":
        return t("stageFailed");
      default:
        return t("stagePending");
    }
  })();

  async function copyError() {
    if (!doc.error) return;
    try {
      await navigator.clipboard.writeText(doc.error);
      setCopied(true);
      toast.success(t("copied"));
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error(t("copyError"));
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          title={t("viewStatus")}
          aria-label={t("viewStatus")}
        >
          <Info className="size-4" />
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="truncate pr-6">{doc.filename}</DialogTitle>
          <DialogDescription className="sr-only">{t("parsingDetails")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <Row label={t("stage")}>
            <div className="flex flex-wrap items-center gap-2">
              <DocStatusBadge status={doc.status} />
              <span className="text-muted-foreground">{stageLabel}</span>
            </div>
          </Row>

          {showProgress && (
            <Row label={t("progress")}>
              <div className="space-y-1.5">
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-primary/60 transition-[width] duration-300"
                    style={{ width: `${progress}%` }}
                  />
                </div>
                <span className="font-mono text-[11px] text-muted-foreground">{progress}%</span>
              </div>
            </Row>
          )}

          {duration && (
            <Row label={t("duration")}>
              <span className="font-mono text-[11px]">{duration}</span>
            </Row>
          )}

          <Row label={t("started")}>
            <span className="text-[11px] text-muted-foreground">
              {formatDateTime(doc.created_at, timezone, locale)}
            </span>
          </Row>
          <Row label={t("updated")}>
            <span className="text-[11px] text-muted-foreground">
              {formatDateTime(doc.updated_at, timezone, locale)}
            </span>
          </Row>

          <Row label={t("statistics")}>
            <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
              <span>{formatBytes(doc.size_bytes, locale)}</span>
              <span>
                {t("chunksAndEvents", {
                  chunks: doc.chunk_count,
                  events: doc.event_count,
                })}
              </span>
              <span>{t("tokens", { count: formatTokenCount(doc.token_usage, locale) })}</span>
            </div>
          </Row>

          {doc.status === "failed" && (
            <Row label={t("errorDetail")}>
              {doc.error ? (
                <div className="space-y-1.5">
                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md border border-destructive/30 bg-destructive/5 p-2 text-[11px] leading-relaxed text-destructive">
                    {doc.error}
                  </pre>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={copyError}
                    className="h-6 gap-1 text-[11px] text-muted-foreground"
                  >
                    {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
                    {t("copyError")}
                  </Button>
                </div>
              ) : (
                <span className="text-[11px] text-muted-foreground">{t("noError")}</span>
              )}
            </Row>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
