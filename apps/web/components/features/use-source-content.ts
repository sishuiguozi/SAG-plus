"use client";

import * as React from "react";
import { useTranslations } from "next-intl";

import { api, ApiError } from "@/lib/api";
import type { Doc, Source } from "@/lib/types";

const PROCESSING_DOCUMENT_STATES = new Set(["pending", "loading", "extracting"]);
const RECENT_DOCUMENT_PAGE_SIZE = 300;
const READY_DOCUMENT_PAGE_SIZE = 200;
const PENDING_DOCUMENT_PAGE_SIZE = 1000;
const ACTIVE_DOCUMENT_PAGE_SIZE = 100;
const PAUSED_DOCUMENT_PAGE_SIZE = 100;
const FAILED_DOCUMENT_PAGE_SIZE = 200;
const DOCUMENT_STATUS_DISPLAY_RANK: Record<Doc["status"], number> = {
  loading: 0,
  extracting: 1,
  pending: 2,
  ready: 3,
  failed: 4,
  paused: 5,
};

function mergeDocuments(primary: Doc[], secondary: Doc[]): Doc[] {
  const seen = new Set<string>();
  const merged: Doc[] = [];
  for (const document of [...primary, ...secondary]) {
    if (seen.has(document.id)) continue;
    seen.add(document.id);
    merged.push(document);
  }
  return merged.sort((a, b) => {
    const rankDelta = DOCUMENT_STATUS_DISPLAY_RANK[a.status] - DOCUMENT_STATUS_DISPLAY_RANK[b.status];
    if (rankDelta !== 0) return rankDelta;
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });
}

/**
 * Shared source-detail controller for the normal page and the mini workspace.
 * Presentation stays independent while fetching, polling and failure semantics
 * remain identical in both panel shapes.
 */
export function useSourceContent(sourceId: string, active = true) {
  const t = useTranslations("Knowledge");
  const [source, setSource] = React.useState<Source | null>(null);
  const [documents, setDocuments] = React.useState<Doc[] | null>(null);
  const [error, setError] = React.useState("");
  const [notFound, setNotFound] = React.useState(false);
  const [refreshing, setRefreshing] = React.useState(false);

  const refresh = React.useCallback(async () => {
    if (!sourceId) return;
    setRefreshing(true);
    try {
      const [
        nextSource,
        recentDocuments,
        loadingDocuments,
        extractingDocuments,
        pendingDocuments,
        pausedDocuments,
        failedDocuments,
        readyDocuments,
      ] = await Promise.all([
        api.getSource(sourceId),
        api.listDocuments(sourceId, { limit: RECENT_DOCUMENT_PAGE_SIZE }),
        api.listDocuments(sourceId, { limit: ACTIVE_DOCUMENT_PAGE_SIZE, status: "loading" }),
        api.listDocuments(sourceId, { limit: ACTIVE_DOCUMENT_PAGE_SIZE, status: "extracting" }),
        api.listDocuments(sourceId, { limit: PENDING_DOCUMENT_PAGE_SIZE, status: "pending" }),
        api.listDocuments(sourceId, { limit: PAUSED_DOCUMENT_PAGE_SIZE, status: "paused" }),
        api.listDocuments(sourceId, { limit: FAILED_DOCUMENT_PAGE_SIZE, status: "failed" }),
        api.listDocuments(sourceId, { limit: READY_DOCUMENT_PAGE_SIZE, status: "ready" }),
      ]);
      setSource(nextSource);
      setDocuments(
        mergeDocuments(
          [
            ...loadingDocuments,
            ...extractingDocuments,
            ...pendingDocuments,
            ...failedDocuments,
            ...pausedDocuments,
            ...recentDocuments,
          ],
          readyDocuments,
        ),
      );
      setError("");
      setNotFound(false);
    } catch (reason) {
      const missing = reason instanceof ApiError && reason.status === 404;
      setNotFound(missing);
      setError(
        missing
          ? t("sourceGone")
          : reason instanceof ApiError
            ? reason.message
            : t("sourceContentFailed"),
      );
    } finally {
      setRefreshing(false);
    }
  }, [sourceId, t]);

  React.useEffect(() => {
    setSource(null);
    setDocuments(null);
    setError("");
    setNotFound(false);
    if (active) void refresh();
  }, [active, refresh, sourceId]);

  const processing =
    documents?.some((document) => PROCESSING_DOCUMENT_STATES.has(document.status)) ?? false;

  React.useEffect(() => {
    if (!active || !processing) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, 4000);
    return () => window.clearInterval(timer);
  }, [active, processing, refresh]);

  return {
    source,
    documents,
    error,
    notFound,
    refreshing,
    processing,
    refresh,
  };
}
