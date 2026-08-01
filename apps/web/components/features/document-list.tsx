"use client";

import * as React from "react";
import { FileText, Pause, Pencil, Play, RefreshCw, Search, Trash2, X } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import { api, ApiError } from "@/lib/api";
import type { Doc, Source } from "@/lib/types";
import { formatBytes, formatTokenCount, relativeTime } from "@/lib/format";
import { useDetailPanel } from "@/components/features/detail-panel";
import { useApp } from "@/components/features/app-shell";
import { DocStatusBadge } from "@/components/features/status-badge";
import { DocumentParsingDetails } from "@/components/features/document-parsing-details";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Virtuoso } from "react-virtuoso";

type DocumentListTab = "ready" | "processing" | "paused" | "failed";

const PROCESSING_STATUSES = new Set<Doc["status"]>(["pending", "loading", "extracting"]);
const DOCUMENT_LIST_TABS: DocumentListTab[] = ["ready", "processing", "paused", "failed"];

function canBatchOperateInTab(document: Doc, tab: DocumentListTab) {
  if (tab === "failed") return document.status === "failed";
  if (tab === "paused") return document.status === "paused";
  if (tab === "processing") return PROCESSING_STATUSES.has(document.status);
  return false;
}

function documentListTabKey(tab: DocumentListTab) {
  switch (tab) {
    case "ready":
      return "tabs.ready";
    case "processing":
      return "tabs.processing";
    case "paused":
      return "tabs.paused";
    case "failed":
      return "tabs.failed";
  }
}

function documentListEmptyTabKey(tab: DocumentListTab) {
  switch (tab) {
    case "ready":
      return "emptyTab.ready";
    case "processing":
      return "emptyTab.processing";
    case "paused":
      return "emptyTab.paused";
    case "failed":
      return "emptyTab.failed";
  }
}

function documentListBatchHintKey(tab: DocumentListTab) {
  switch (tab) {
    case "ready":
      return "batchHintByTab.ready";
    case "processing":
      return "batchHintByTab.processing";
    case "paused":
      return "batchHintByTab.paused";
    case "failed":
      return "batchHintByTab.failed";
  }
}

export function DocumentList({
  sourceId,
  source,
  documents,
  onChange,
  variant = "normal",
  onOpenDocument,
}: {
  sourceId: string;
  source?: Source | null;
  documents: Doc[];
  onChange: () => void;
  variant?: "normal" | "compact";
  onOpenDocument?: (document: Doc) => void;
}) {
  const t = useTranslations("DocumentList");
  const locale = useLocale();
  const [pending, setPending] = React.useState<string | null>(null);
  const { open } = useDetailPanel();
  const { timezone } = useApp();
  const tc = useTranslations("Common");
  const [renameTarget, setRenameTarget] = React.useState<Doc | null>(null);
  const [renameValue, setRenameValue] = React.useState("");
  const [savingRename, setSavingRename] = React.useState(false);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [busyBatch, setBusyBatch] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const deferredQuery = React.useDeferredValue(query);
  const normalizedQuery = deferredQuery.trim().toLocaleLowerCase();
  const compactFilteredDocuments = React.useMemo(
    () =>
      normalizedQuery
        ? documents.filter((document) =>
            document.filename.toLocaleLowerCase().includes(normalizedQuery),
          )
        : documents,
    [documents, normalizedQuery],
  );
  const [activeTab, setActiveTab] = React.useState<DocumentListTab>("ready");
  const tabCounts = React.useMemo(
    () =>
      source
        ? {
            ready: source.ready_document_count,
            processing: Math.max(
              0,
              source.pending_document_count - source.paused_document_count,
            ),
            paused: source.paused_document_count,
            failed: source.failed_document_count,
          }
        : {
            ready: documents.filter((document) => document.status === "ready").length,
            processing: documents.filter((document) => PROCESSING_STATUSES.has(document.status)).length,
            paused: documents.filter((document) => document.status === "paused").length,
            failed: documents.filter((document) => document.status === "failed").length,
          },
    [documents, source],
  );
  const tabDocuments = React.useMemo(
    () =>
      documents.filter((document) => {
        if (activeTab === "processing") return PROCESSING_STATUSES.has(document.status);
        return document.status === activeTab;
      }),
    [activeTab, documents],
  );
  const filteredDocuments = React.useMemo(
    () =>
      normalizedQuery
        ? tabDocuments.filter((document) =>
            document.filename.toLocaleLowerCase().includes(normalizedQuery),
          )
        : tabDocuments,
    [normalizedQuery, tabDocuments],
  );
  const selectableDocuments = React.useMemo(
    () =>
      filteredDocuments.filter((document) => canBatchOperateInTab(document, activeTab)),
    [activeTab, filteredDocuments],
  );
  const isSelectable = React.useCallback(
    (document: Doc) => canBatchOperateInTab(document, activeTab),
    [activeTab],
  );

  React.useEffect(() => {
    setSelected(new Set());
  }, [activeTab, normalizedQuery]);

  async function reprocess(d: Doc) {
    setPending(d.id);
    try {
      await api.reprocessDocument(sourceId, d.id);
      toast.success(t("requeued"));
      onChange();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("operationFailed"));
    } finally {
      setPending(null);
    }
  }

  const [deleteTarget, setDeleteTarget] = React.useState<Doc | null>(null);
  const [deletePassword, setDeletePassword] = React.useState("");
  const [deleteBusy, setDeleteBusy] = React.useState(false);
  const [batchDeleteOpen, setBatchDeleteOpen] = React.useState(false);
  const [batchDeletePassword, setBatchDeletePassword] = React.useState("");
  const [batchDeleteBusy, setBatchDeleteBusy] = React.useState(false);

  async function confirmDelete() {
    if (!deleteTarget) return false;
    if (!deletePassword.trim()) {
      toast.error(t("deletePasswordRequired"));
      return false; // 保持弹窗打开
    }
    setDeleteBusy(true);
    try {
      await api.deleteDocument(sourceId, deleteTarget.id, deletePassword.trim());
      toast.success(t("deleted"));
      onChange();
      return true;
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("deleteFailed"));
      return false; // 密码错误等失败时保持弹窗打开
    } finally {
      setDeleteBusy(false);
    }
  }

  async function confirmBatchDelete() {
    const targets = filteredDocuments.filter((d) => selected.has(d.id));
    if (targets.length === 0) return false;
    if (!batchDeletePassword.trim()) {
      toast.error(t("deletePasswordRequired"));
      return false;
    }
    setBatchDeleteBusy(true);
    let ok = 0;
    let fail = 0;
    let firstError = "";
    for (const d of targets) {
      try {
        await api.deleteDocument(sourceId, d.id, batchDeletePassword.trim());
        ok += 1;
      } catch (err) {
        fail += 1;
        firstError = firstError || (err instanceof ApiError ? err.message : t("deleteFailed"));
      }
    }
    setBatchDeleteBusy(false);
    if (ok > 0) {
      toast.success(`${t("deleted")} · ${ok}`);
      onChange();
    }
    if (fail > 0) {
      toast.error(
        `${t("batchPartialFail", { count: fail })}${firstError ? `：${firstError}` : ""}`,
      );
    }
    clearSelected();
    setBatchDeletePassword("");
    if (ok === 0 && fail > 0) {
      return false; // 全部失败（如密码错误）保持弹窗打开重输
    }
    setBatchDeleteOpen(false);
    return true;
  }

  async function pause(d: Doc) {
    setPending(d.id);
    try {
      await api.pauseDocument(sourceId, d.id);
      toast.success(t("pausing"));
      onChange();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("pauseFailed"));
    } finally {
      setPending(null);
    }
  }

  async function resume(d: Doc) {
    setPending(d.id);
    try {
      await api.resumeDocument(sourceId, d.id);
      toast.success(t("resumed"));
      onChange();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("resumeFailed"));
    } finally {
      setPending(null);
    }
  }

  async function commitRename() {
    if (!renameTarget) return;
    const name = renameValue.trim();
    if (!name) return;
    setSavingRename(true);
    try {
      await api.renameDocument(sourceId, renameTarget.id, name);
      toast.success(t("renamed"));
      setRenameTarget(null);
      onChange();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("renameFailed"));
    } finally {
      setSavingRename(false);
    }
  }

  const toggleSelected = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const selectAll = () => setSelected(new Set(selectableDocuments.map((d) => d.id)));
  const clearSelected = () => setSelected(new Set());

  async function runBatch(
    targets: Doc[],
    op: (d: Doc) => Promise<unknown>,
    successMsg: string,
  ) {
    if (targets.length === 0) {
      toast(t("noMatchingDocs"));
      return;
    }
    setBusyBatch(true);
    let ok = 0;
    let fail = 0;
    let firstError = "";
    for (const d of targets) {
      try {
        await op(d);
        ok += 1;
      } catch (err) {
        fail += 1;
        if (!firstError) {
          firstError = err instanceof ApiError ? err.message : t("operationFailed");
        }
      }
    }
    setBusyBatch(false);
    if (ok > 0) {
      toast.success(`${successMsg} · ${ok}`);
      onChange();
    }
    if (fail > 0) {
      toast.error(
        firstError
          ? `${t("batchPartialFail", { count: fail })}：${firstError}`
          : t("batchPartialFail", { count: fail }),
      );
    }
    clearSelected();
  }

  async function batchReprocess() {
    await runBatch(
      filteredDocuments.filter((d) => selected.has(d.id) && d.status === "failed"),
      (d) => api.reprocessDocument(sourceId, d.id),
      t("requeued"),
    );
  }
  async function batchPause() {
    await runBatch(
      filteredDocuments.filter((d) => selected.has(d.id) && PROCESSING_STATUSES.has(d.status)),
      (d) => api.pauseDocument(sourceId, d.id),
      t("pausing"),
    );
  }
  async function batchResume() {
    await runBatch(
      filteredDocuments.filter((d) => selected.has(d.id) && d.status === "paused"),
      (d) => api.resumeDocument(sourceId, d.id),
      t("resumed"),
    );
  }

  const allVisibleSelected =
    selectableDocuments.length > 0 &&
    selectableDocuments.every((document) => selected.has(document.id));
  const activeTabEmptyMessage = normalizedQuery
    ? t("noSearchResults")
    : t(documentListEmptyTabKey(activeTab));
  const batchHint = t(documentListBatchHintKey(activeTab));

  if (variant === "compact") {
    return (
      <div className="flex min-h-0 flex-1 flex-col gap-2">
        <div className="relative shrink-0">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("searchPlaceholder")}
            className="h-9 border-0 bg-muted pl-8 pr-8 text-sm shadow-none"
            aria-label={t("searchPlaceholder")}
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground"
              aria-label={t("clearSearch")}
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>
        {compactFilteredDocuments.length === 0 ? (
          <div className="shrink-0 rounded-lg px-3 py-6 text-center text-xs text-muted-foreground">
            {t("noSearchResults")}
          </div>
        ) : (
          <Virtuoso
            data={compactFilteredDocuments}
            className="min-h-0 flex-1"
            style={{ height: "100%" }}
            itemContent={(_index, document) => (
              <div className="px-1 py-0.5">
                <button
                  type="button"
                  onClick={() => {
                    if (onOpenDocument) onOpenDocument(document);
                    else open({ kind: "document", sourceId, documentId: document.id });
                  }}
                  className="group/document flex w-full items-center gap-3 rounded-lg px-2.5 py-2.5 text-left outline-none transition-colors hover:bg-muted focus-visible:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
                  title={t("viewDocument")}
                >
                  <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground transition-colors group-hover/document:text-foreground">
                    <FileText className="size-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-foreground">
                      {document.filename}
                    </div>
                    <div className="mt-0.5 flex min-w-0 items-center gap-1.5 text-[11px] text-muted-foreground">
                      <span>{formatBytes(document.size_bytes, locale)}</span>
                      <span>·</span>
                      <span>{relativeTime(document.created_at, timezone, locale)}</span>
                      {document.status === "ready" && (
                        <>
                          <span>·</span>
                          <span className="truncate">{t("events", { count: document.event_count })}</span>
                        </>
                      )}
                    </div>
                  </div>
                  <DocStatusBadge status={document.status} />
                </button>
              </div>
            )}
          />
        )}
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col rounded-lg border bg-card">
      <div className="border-b px-3 py-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("searchPlaceholder")}
            className="h-9 pl-8 pr-8 text-sm"
            aria-label={t("searchPlaceholder")}
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground"
              aria-label={t("clearSearch")}
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>
        <Tabs
          value={activeTab}
          onValueChange={(value) => setActiveTab(value as DocumentListTab)}
          className="mt-2"
        >
          <TabsList className="grid w-full grid-cols-4">
            {DOCUMENT_LIST_TABS.map((tab) => (
              <TabsTrigger key={tab} value={tab} className="gap-1.5 text-xs">
                <span>{t(documentListTabKey(tab))}</span>
                <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                  {tabCounts[tab].toLocaleString(locale)}
                </span>
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>
      {documents.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-b bg-muted/30 px-3 py-1.5">
          <Checkbox
            checked={allVisibleSelected}
            disabled={selectableDocuments.length === 0 || busyBatch}
            onCheckedChange={(v) => (v ? selectAll() : clearSelected())}
            aria-label={t("selectAll")}
          />
          {selected.size > 0 ? (
            <>
              <span className="text-xs font-medium text-foreground">
                {t("selected", { count: selected.size })}
              </span>
              {activeTab === "failed" && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 gap-1 px-2 text-xs"
                  onClick={batchReprocess}
                  disabled={busyBatch}
                >
                  <RefreshCw className="size-3.5" />
                  {t("batchRetry")}
                </Button>
              )}
              {activeTab === "processing" && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 gap-1 px-2 text-xs"
                  onClick={batchPause}
                  disabled={busyBatch}
                >
                  <Pause className="size-3.5" />
                  {t("batchPause")}
                </Button>
              )}
              {activeTab === "paused" && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 gap-1 px-2 text-xs"
                  onClick={batchResume}
                  disabled={busyBatch}
                >
                  <Play className="size-3.5" />
                  {t("batchResume")}
                </Button>
              )}
              <Button
                size="sm"
                variant="ghost"
                className="h-7 gap-1 px-2 text-xs text-destructive hover:text-destructive"
                onClick={() => {
                  setBatchDeletePassword("");
                  setBatchDeleteOpen(true);
                }}
                disabled={busyBatch}
              >
                <Trash2 className="size-3.5" />
                {t("batchDelete")}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="ml-auto h-7 px-2 text-xs"
                onClick={clearSelected}
                disabled={busyBatch}
              >
                {t("clearSelection")}
              </Button>
            </>
          ) : (
            <span className="text-xs text-muted-foreground">{batchHint}</span>
          )}
        </div>
      )}
      {filteredDocuments.length === 0 ? (
        <div className="px-4 py-10 text-center text-sm text-muted-foreground">
          {activeTabEmptyMessage}
        </div>
      ) : (
        <Virtuoso
          data={filteredDocuments}
          className="min-h-0 flex-1"
          style={{ height: "100%" }}
          itemContent={(_index, d) => {
            const progress = Math.min(100, Math.max(0, Math.round(d.progress ?? 0)));
            const showProgress = d.status === "loading" || d.status === "extracting" || d.status === "paused";
            const showMetrics = showProgress || d.status === "failed";
            const selectable = isSelectable(d);
            return (
              <div className="flex items-center gap-3 px-2 py-3 transition-colors hover:bg-muted/60">
                <Checkbox
                  checked={selected.has(d.id)}
                  disabled={!selectable}
                  onCheckedChange={() => toggleSelected(d.id)}
                  aria-label={t("selectDoc", { name: d.filename })}
                  className="shrink-0"
                />
                <div className="grid size-9 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
                  <FileText className="size-4" />
                </div>

                <button
                  type="button"
                  onClick={() => open({ kind: "document", sourceId, documentId: d.id })}
                  className="min-w-0 flex-1 rounded-md text-left outline-none focus-visible:bg-muted/60"
                  title={t("viewDetails")}
                >
                  <div className="truncate text-sm font-medium text-foreground">{d.filename}</div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
                    <span>{formatBytes(d.size_bytes, locale)}</span>
                    <span>·</span>
                    <span>{relativeTime(d.created_at, timezone, locale)}</span>
                    {d.status === "ready" && (
                      <>
                        <span>·</span>
                        <span>
                          {t("chunksAndEvents", {
                            chunks: d.chunk_count,
                            events: d.event_count,
                          })}
                        </span>
                        <span>·</span>
                        <span>100% · {t("tokens", { count: formatTokenCount(d.token_usage, locale) })}</span>
                      </>
                    )}
                    {showMetrics && (
                      <>
                        <span>·</span>
                        <span>{progress}% · {t("tokens", { count: formatTokenCount(d.token_usage, locale) })}</span>
                      </>
                    )}
                    {d.status === "failed" && d.error && (
                      <>
                        <span>·</span>
                        <span className="truncate text-destructive" title={d.error}>
                          {d.error}
                        </span>
                      </>
                    )}
                  </div>
                  {showProgress && (
                    <div className="mt-1.5 h-1 w-full max-w-56 overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-primary/60 transition-[width] duration-300"
                        style={{ width: `${progress}%` }}
                      />
                    </div>
                  )}
                </button>

                <DocStatusBadge status={d.status} />

                <div className="flex shrink-0 items-center gap-0.5">
                  <DocumentParsingDetails doc={d} />
                  <Button
                    variant="ghost"
                    size="icon"
                    title={t("rename")}
                    aria-label={t("rename")}
                    disabled={pending === d.id}
                    onClick={() => {
                      setRenameValue(d.filename);
                      setRenameTarget(d);
                    }}
                  >
                    <Pencil className="size-4" />
                  </Button>
                  {PROCESSING_STATUSES.has(d.status) && (
                    <Button
                      variant="ghost"
                      size="icon"
                      title={t("pause")}
                      disabled={pending === d.id}
                      onClick={() => pause(d)}
                    >
                      <Pause className="size-4" />
                    </Button>
                  )}
                  {d.status === "paused" && (
                    <Button
                      variant="ghost"
                      size="icon"
                      title={t("resume")}
                      disabled={pending === d.id}
                      onClick={() => resume(d)}
                    >
                      <Play className="size-4" />
                    </Button>
                  )}
                  {d.status === "failed" && (
                    <Button
                      variant="ghost"
                      size="icon"
                      title={t("reprocess")}
                      disabled={pending === d.id}
                      onClick={() => reprocess(d)}
                    >
                      <RefreshCw className="size-4" />
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="icon"
                    title={t("delete")}
                    disabled={pending === d.id}
                    onClick={() => {
                      setDeletePassword("");
                      setDeleteTarget(d);
                    }}
                    className="text-muted-foreground hover:text-destructive"
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              </div>
            );
          }}
        />
      )}
      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setDeleteTarget(null);
            setDeletePassword("");
          }
        }}
        title={t("delete")}
        description={t("deleteDescription", { name: deleteTarget?.filename ?? "" })}
        confirmLabel={t("delete")}
        onConfirm={confirmDelete}
      >
        <Field className="px-6 pb-1">
          <FieldLabel htmlFor="delete-document-password">{t("deletePasswordLabel")}</FieldLabel>
          <Input
            id="delete-document-password"
            type="password"
            value={deletePassword}
            onChange={(event) => setDeletePassword(event.target.value)}
            placeholder={t("deletePasswordPlaceholder")}
            autoComplete="current-password"
            disabled={deleteBusy}
            onKeyDown={(event) => {
              if (event.key === "Enter" && deletePassword.trim()) {
                event.preventDefault();
                void confirmDelete();
              }
            }}
          />
          <FieldDescription>{t("deletePasswordHelp")}</FieldDescription>
        </Field>
      </ConfirmDialog>

      <ConfirmDialog
        open={batchDeleteOpen}
        onOpenChange={(open) => {
          if (!open) {
            setBatchDeleteOpen(false);
            setBatchDeletePassword("");
          }
        }}
        title={t("batchDelete")}
        description={t("batchDeleteDescription", { count: selected.size })}
        confirmLabel={t("delete")}
        onConfirm={confirmBatchDelete}
      >
        <Field className="px-6 pb-1">
          <FieldLabel htmlFor="batch-delete-password">{t("deletePasswordLabel")}</FieldLabel>
          <Input
            id="batch-delete-password"
            type="password"
            value={batchDeletePassword}
            onChange={(event) => setBatchDeletePassword(event.target.value)}
            placeholder={t("deletePasswordPlaceholder")}
            autoComplete="current-password"
            disabled={batchDeleteBusy}
            onKeyDown={(event) => {
              if (event.key === "Enter" && batchDeletePassword.trim()) {
                event.preventDefault();
                void confirmBatchDelete();
              }
            }}
          />
          <FieldDescription>{t("deletePasswordHelp")}</FieldDescription>
        </Field>
      </ConfirmDialog>

      <Dialog
        open={renameTarget !== null}
        onOpenChange={(o) => {
          if (!o) setRenameTarget(null);
        }}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("rename")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <Input
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              autoFocus
              onKeyDown={(e) => {
                if (e.key === "Enter" && !savingRename) commitRename();
              }}
            />
            <DialogFooter>
              <Button
                variant="ghost"
                onClick={() => setRenameTarget(null)}
                disabled={savingRename}
              >
                {tc("cancel")}
              </Button>
              <Button
                onClick={commitRename}
                disabled={savingRename || !renameValue.trim()}
              >
                {savingRename ? tc("saving") : tc("save")}
              </Button>
            </DialogFooter>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
