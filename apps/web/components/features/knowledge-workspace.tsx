"use client";

import * as React from "react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import {
  ArrowLeft,
  ChevronRight,
  Layers,
  LayoutGrid,
  List,
  Plus,
  RotateCw,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

import { api, ApiError } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import type { IngestStats, Source } from "@/lib/types";
import { dispatchUniverseSourceFocus } from "@/lib/universe-events";
import { cn } from "@/lib/utils";
import { useApp } from "@/components/features/app-shell";
import { CreateSourceDialog } from "@/components/features/create-source-dialog";
import { EmptyState } from "@/components/features/empty-state";
import { useKnowledgeWorkspace } from "@/components/features/knowledge-provider";
import { KnowledgeSourceWorkspace } from "@/components/features/knowledge-source-workspace";
import { PageHeader } from "@/components/features/page-header";
import { SourceCard } from "@/components/features/source-card";
import { SourceCreateForm } from "@/components/features/source-create-form";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import styles from "./knowledge-workspace.module.css";

type View = "grid" | "list";
type KnowledgeWorkspaceVariant = "normal" | "compact";

function formatEta(seconds: number | null, locale: string) {
  if (seconds === null || seconds <= 0) return "—";
  const number = new Intl.NumberFormat(locale);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.max(1, Math.round((seconds % 3600) / 60));
  if (hours > 0) return `${number.format(hours)}h ${number.format(minutes)}m`;
  return `${number.format(minutes)}m`;
}

function IngestStatsPanel({ active }: { active: boolean }) {
  const t = useTranslations("Knowledge");
  const locale = useLocale();
  const [stats, setStats] = React.useState<IngestStats | null>(null);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const load = async () => {
      try {
        const next = await api.ingestStats();
        if (cancelled) return;
        setStats(next);
        setError("");
      } catch (reason) {
        if (cancelled) return;
        setError(reason instanceof ApiError ? reason.message : t("ingestPanelLoadFailed"));
      } finally {
        if (!cancelled) timer = setTimeout(load, 10000);
      }
    };

    void load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [active, t]);

  if (!stats && !error) {
    return <Skeleton className="h-28 rounded-xl" />;
  }

  return (
    <div className="rounded-xl border bg-card/70 p-4 shadow-soft">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-sm font-medium">{t("ingestPanelTitle")}</div>
          <div className="mt-1 text-xs text-muted-foreground">
            {error || t("ingestPanelHint", { minutes: stats?.sample_window_minutes ?? 10 })}
          </div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-semibold tabular-nums">
            {new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(
              stats?.docs_per_minute ?? 0,
            )}
          </div>
          <div className="text-xs text-muted-foreground">{t("ingestRatePerMinute")}</div>
        </div>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-lg bg-muted/60 px-3 py-2">
          <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
            {t("ingestEta")}
          </div>
          <div className="mt-1 text-lg font-medium tabular-nums">
            {formatEta(stats?.eta_seconds ?? null, locale)}
          </div>
        </div>
        <div className="rounded-lg bg-muted/60 px-3 py-2">
          <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
            {t("ingestActiveFiles")}
          </div>
          <div className="mt-1 text-lg font-medium tabular-nums">
            {stats?.active_files ?? 0}
          </div>
          <div className="text-xs text-muted-foreground">
            {t("ingestActiveBreakdown", {
              loading: stats?.loading_files ?? 0,
              extracting: stats?.extracting_files ?? 0,
            })}
          </div>
        </div>
        <div className="rounded-lg bg-muted/60 px-3 py-2">
          <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
            {t("ingestQueue")}
          </div>
          <div className="mt-1 text-lg font-medium tabular-nums">
            {(stats?.queued_jobs ?? 0) + (stats?.running_jobs ?? 0)}
          </div>
          <div className="text-xs text-muted-foreground">
            {t("ingestQueueBreakdown", {
              queued: stats?.queued_jobs ?? 0,
              running: stats?.running_jobs ?? 0,
            })}
          </div>
        </div>
        <div className="rounded-lg bg-muted/60 px-3 py-2">
          <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
            {t("ingestCompletion")}
          </div>
          <div className="mt-1 text-lg font-medium tabular-nums">
            {stats?.indexed_files ?? 0}/{stats?.total_files ?? 0}
          </div>
          <div className="text-xs text-muted-foreground">
            {t("ingestPendingPausedFailed", {
              pending: Math.max(0, (stats?.pending_files ?? 0) - (stats?.paused_files ?? 0)),
              paused: stats?.paused_files ?? 0,
              failed: stats?.failed_files ?? 0,
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

function documentStatusCounts(source: Source) {
  const total = source.document_count ?? 0;
  const ready = source.ready_document_count ?? 0;
  const paused = source.paused_document_count ?? 0;
  const failed = source.failed_document_count ?? 0;
  const pending = source.pending_document_count ?? Math.max(0, total - ready - failed);
  const activePending = Math.max(0, pending - paused);
  return { total, ready, pending, activePending, paused, failed };
}

function SourceRow({ source, first }: { source: Source; first: boolean }) {
  const t = useTranslations("Knowledge");
  const locale = useLocale();
  const { timezone } = useApp();
  const counts = documentStatusCounts(source);
  return (
    <Link
      href={`/knowledge/${source.id}`}
      className={cn(
        "flex items-center gap-3 px-4 py-3 text-sm outline-none transition-colors hover:bg-muted/50 focus-visible:bg-muted/60",
        !first && "border-t",
      )}
    >
      <div className="grid size-9 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
        <Layers className="size-4" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium">{source.name}</div>
        {source.description && (
          <div className="truncate text-xs text-muted-foreground">{source.description}</div>
        )}
      </div>
      <div className="hidden shrink-0 items-center gap-3 text-xs tabular-nums text-muted-foreground sm:flex">
        <span>{t("totalFilesCount", { count: counts.total })}</span>
        <span className="text-emerald-600 dark:text-emerald-400">
          {t("indexedFilesCount", { count: counts.ready })}
        </span>
        <span className="text-amber-600 dark:text-amber-400">
          {t("pendingFilesCount", { count: counts.activePending })}
        </span>
        <span className="text-sky-600 dark:text-sky-400">
          {t("pausedFilesCount", { count: counts.paused })}
        </span>
        <span className={cn(counts.failed > 0 && "text-destructive")}>
          {t("failedFilesCount", { count: counts.failed })}
        </span>
        <span>{t("chunksCount", { count: source.chunk_count })}</span>
        <span>{t("eventsCount", { count: source.event_count })}</span>
        <span>{relativeTime(source.updated_at, timezone, locale)}</span>
      </div>
      <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
    </Link>
  );
}

function CompactSourceRow({
  source,
  onOpen,
}: {
  source: Source;
  onOpen: (source: Source) => void;
}) {
  const t = useTranslations("Knowledge");
  const locale = useLocale();
  const { timezone } = useApp();
  const counts = documentStatusCounts(source);
  return (
    <button
      type="button"
      onClick={() => onOpen(source)}
      className="group/source flex w-full items-center gap-3 rounded-lg px-2.5 py-2.5 text-left outline-none transition-colors hover:bg-muted focus-visible:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground transition-colors group-hover/source:text-foreground">
        <Layers className="size-4" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{source.name}</div>
        <div className="mt-0.5 flex items-center gap-1.5 truncate text-[11px] text-muted-foreground">
          <span>{t("totalFilesCount", { count: counts.total })}</span>
          <span>·</span>
          <span>{t("indexedFilesCount", { count: counts.ready })}</span>
          <span>·</span>
          <span>{t("pendingFilesCount", { count: counts.activePending })}</span>
          <span>·</span>
          <span>{t("pausedFilesCount", { count: counts.paused })}</span>
          {counts.failed > 0 && (
            <>
              <span>·</span>
              <span className="text-destructive">
                {t("failedFilesCount", { count: counts.failed })}
              </span>
            </>
          )}
          <span>·</span>
          <span>{t("eventsCount", { count: source.event_count })}</span>
          <span>·</span>
          <span>{relativeTime(source.updated_at, timezone, locale)}</span>
        </div>
      </div>
      <ChevronRight className="size-3.5 shrink-0 text-muted-foreground/70 transition-transform group-hover/source:translate-x-0.5" />
    </button>
  );
}

export function KnowledgeWorkspace({
  variant = "normal",
  active = true,
}: {
  variant?: KnowledgeWorkspaceVariant;
  active?: boolean;
}) {
  const t = useTranslations("Knowledge");
  const { sources, error, ensureLoaded, refresh, addSource } = useKnowledgeWorkspace();
  const [view, setView] = React.useState<View>("grid");
  const [creating, setCreating] = React.useState(false);
  const [selectedSource, setSelectedSource] = React.useState<{
    id: string;
    initialScreen: "documents" | "add";
  } | null>(null);

  React.useEffect(() => {
    if (active) void ensureLoaded();
  }, [active, ensureLoaded]);

  React.useEffect(() => {
    if (!active) return;
    void refresh();
  }, [active, refresh]);

  React.useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      try {
        await refresh();
      } finally {
        if (!cancelled) timer = setTimeout(tick, 10000);
      }
    };

    timer = setTimeout(tick, 10000);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [active, refresh]);

  React.useEffect(() => {
    if (variant !== "normal") return;
    const saved = window.localStorage.getItem("sag:knowledge-view");
    if (saved === "list") setView("list");
  }, [variant]);

  const changeView = (next: View) => {
    setView(next);
    window.localStorage.setItem("sag:knowledge-view", next);
  };

  if (variant === "compact") {
    if (selectedSource) {
      return (
        <KnowledgeSourceWorkspace
          key={selectedSource.id}
          sourceId={selectedSource.id}
          initialScreen={selectedSource.initialScreen}
          active={active}
          onBack={() => setSelectedSource(null)}
          onSourceChanged={refresh}
        />
      );
    }

    return (
      <div className="flex h-full min-h-0 flex-col bg-background/30">
        <div className="flex h-11 shrink-0 items-center gap-2 border-b px-3">
          {creating ? (
            <>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-7"
                onClick={() => setCreating(false)}
                aria-label={t("back")}
              >
                <ArrowLeft className="size-3.5" />
              </Button>
              <span className="min-w-0 flex-1 truncate text-xs font-medium">{t("newSource")}</span>
            </>
          ) : (
            <>
              <div className="min-w-0 flex-1">
                <p className="text-xs font-medium">{t("title")}</p>
                <p className="text-[10px] text-muted-foreground">
                  {sources === null ? t("syncing") : t("sourcesCount", { count: sources.length })}
                </p>
              </div>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-7"
                onClick={() => void refresh()}
                aria-label={t("refresh")}
                title={t("refresh")}
              >
                <RotateCw className="size-3.5" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-7"
                onClick={() => setCreating(true)}
                aria-label={t("newSource")}
                title={t("newSource")}
              >
                <Plus className="size-3.5" />
              </Button>
            </>
          )}
        </div>

        <AnimatePresence mode="wait" initial={false}>
          {creating ? (
            <motion.div
              key="create"
              initial={{ opacity: 0, x: 8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -8 }}
              className="min-h-0 flex-1 overflow-y-auto p-4"
            >
              <p className="mb-4 text-xs leading-5 text-muted-foreground">
                {t("compactCreateDescription")}
              </p>
              <SourceCreateForm
                compact
                onCancel={() => setCreating(false)}
                onCreated={(source) => {
                  addSource(source);
                  setCreating(false);
                  setSelectedSource({ id: source.id, initialScreen: "add" });
                }}
              />
            </motion.div>
          ) : (
            <motion.div
              key="sources"
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 8 }}
              className="min-h-0 flex-1 overflow-y-auto p-2"
            >
              {error && (
                <div className="mb-2 flex items-center justify-between gap-2 rounded-md bg-destructive/8 px-3 py-2 text-xs text-destructive">
                  <span className="truncate">{error}</span>
                  <button type="button" onClick={() => void refresh()} className="font-medium">
                    {t("retry")}
                  </button>
                </div>
              )}
              {sources === null ? (
                <div className="space-y-2 p-1">
                  {Array.from({ length: 4 }).map((_, index) => (
                    <Skeleton key={index} className="h-14 rounded-lg" />
                  ))}
                </div>
              ) : sources.length === 0 ? (
                <div className="flex min-h-56 flex-col items-center justify-center px-6 text-center">
                  <div className="grid size-10 place-items-center rounded-xl bg-muted text-muted-foreground">
                    <Layers className="size-4" />
                  </div>
                  <p className="mt-3 text-sm font-medium">{t("emptyTitle")}</p>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">
                    {t("emptyCompactDescription")}
                  </p>
                  <Button size="sm" className="mt-4" onClick={() => setCreating(true)}>
                    <Plus className="size-3.5" />
                    {t("newSource")}
                  </Button>
                </div>
              ) : (
                <div className="space-y-0.5">
                  {sources.map((source) => (
                    <CompactSourceRow
                      key={source.id}
                      source={source}
                      onOpen={(item) => {
                        dispatchUniverseSourceFocus(item.id);
                        setSelectedSource({ id: item.id, initialScreen: "documents" });
                      }}
                    />
                  ))}
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    );
  }

  return (
    <div className={cn(styles.workspace, "flex flex-col gap-6 p-4 md:p-6")}>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <>
            <ToggleGroup
              type="single"
              variant="outline"
              size="sm"
              value={view}
              onValueChange={(value) => value && changeView(value as View)}
              aria-label={t("viewAria")}
            >
              <ToggleGroupItem value="grid" aria-label={t("gridView")}>
                <LayoutGrid />
              </ToggleGroupItem>
              <ToggleGroupItem value="list" aria-label={t("listView")}>
                <List />
              </ToggleGroupItem>
            </ToggleGroup>
            <CreateSourceDialog
              onCreated={addSource}
              trigger={
                <Button size="icon" aria-label={t("newSource")} title={t("newSource")}>
                  <Plus />
                </Button>
              }
            />
          </>
        }
      />

      <IngestStatsPanel active={active} />

      <div>
        {error && (
          <div className="mb-4 flex items-center justify-between gap-3 rounded-lg border border-destructive/20 bg-destructive/8 px-4 py-3 text-sm text-destructive">
            <span>{error}</span>
            <Button variant="outline" size="sm" onClick={() => void refresh()}>
              {t("retry")}
            </Button>
          </div>
        )}
        {sources === null ? (
          <div className={styles.sourceGrid}>
            {Array.from({ length: 4 }).map((_, index) => (
              <Skeleton key={index} className="h-[168px]" />
            ))}
          </div>
        ) : sources.length === 0 ? (
          <EmptyState
            icon={Layers}
            title={t("emptyTitle")}
            description={t("emptyDescription")}
            action={<CreateSourceDialog onCreated={addSource} />}
          />
        ) : view === "grid" ? (
          <div className={cn(styles.sourceGrid, "animate-fade-in")}>
            {sources.map((source) => (
              <SourceCard key={source.id} source={source} onChanged={refresh} />
            ))}
          </div>
        ) : (
          <div className="animate-fade-in overflow-hidden rounded-lg border">
            {sources.map((source, index) => (
              <SourceRow key={source.id} source={source} first={index === 0} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
