"use client";

import * as React from "react";
import { History } from "lucide-react";
import { useTranslations } from "next-intl";

import { api } from "@/lib/api";
import type { DocumentActivityItem } from "@/lib/types";

interface ActivityEvent {
  document_id: string;
  filename: string;
  from_status: string;
  to_status: string;
  progress: number;
  error: string | null;
  updated_at: string | null;
}

function formatTime(iso: string | null) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

const STATUS_KEYS: Record<string, string> = {
  pending: "pending",
  loading: "loading",
  extracting: "extracting",
  paused: "paused",
  ready: "ready",
  failed: "failed",
};

/** SAG-OPT-502：信源处理活动流 —— 轮询最近文档快照，对比生成「旧状态 → 新状态」事件。 */
export function IngestActivityPanel({
  sourceId,
  active = true,
}: {
  sourceId: string;
  active?: boolean;
}) {
  const t = useTranslations("IngestActivity");
  const statusT = useTranslations("DocumentStatus");
  const statusLabel = React.useCallback(
    (key: string) => statusT(key as never),
    [statusT],
  );
  const [events, setEvents] = React.useState<ActivityEvent[]>([]);
  const snapshotRef = React.useRef<Record<string, DocumentActivityItem>>({});

  React.useEffect(() => {
    if (!sourceId || !active) return;
    let cancelled = false;

    const tick = async () => {
      if (typeof document !== "undefined" && document.hidden) return;
      try {
        const { events: items } = await api.getSourceActivity(sourceId, 30);
        if (cancelled) return;
        const prev = snapshotRef.current;
        const merged = { ...prev };
        const nextEvents: ActivityEvent[] = [];
        for (const item of items) {
          const before = merged[item.document_id];
          merged[item.document_id] = item;
          if (before && before.status !== item.status) {
            nextEvents.push({
              document_id: item.document_id,
              filename: item.filename,
              from_status: before.status,
              to_status: item.status,
              progress: item.progress,
              error: item.error,
              updated_at: item.updated_at,
            });
          }
        }
        snapshotRef.current = merged;
        if (nextEvents.length > 0) {
          setEvents((current) => [...nextEvents.reverse(), ...current].slice(0, 12));
        }
      } catch {
        // 轮询失败静默，下一轮重试
      }
    };

    void tick();
    const timer = window.setInterval(tick, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [sourceId, active]);

  if (!active) return null;

  return (
    <section className="overflow-hidden rounded-lg border bg-card/40 shadow-soft">
      <header className="flex items-center gap-2 border-b px-4 py-3 sm:px-5">
        <History className="size-4 text-muted-foreground" />
        <div className="min-w-0">
          <h3 className="text-sm font-semibold leading-5">{t("title")}</h3>
          <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
            {t("description")}
          </p>
        </div>
      </header>
      <div className="max-h-56 overflow-y-auto p-2">
        {events.length === 0 ? (
          <p className="px-3 py-4 text-center text-xs text-muted-foreground">
            {t("empty")}
          </p>
        ) : (
          <ul className="flex flex-col gap-1">
            {events.map((event) => {
              const fromLabel = statusLabel(STATUS_KEYS[event.from_status] ?? "pending");
              const toLabel = statusLabel(STATUS_KEYS[event.to_status] ?? "pending");
              const failed = event.to_status === "failed";
              return (
                <li
                  key={`${event.document_id}-${event.updated_at}-${event.to_status}`}
                  className="flex flex-col gap-0.5 rounded-md px-3 py-2 text-xs hover:bg-muted/50"
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="min-w-0 truncate font-medium">
                      {event.filename}
                    </span>
                    <span className="shrink-0 text-muted-foreground">
                      {fromLabel} → {toLabel}
                    </span>
                    {event.to_status === "processing" || event.to_status === "extracting" || event.to_status === "loading" ? (
                      <span className="shrink-0 text-muted-foreground">
                        {t("progress", { progress: event.progress })}
                      </span>
                    ) : null}
                    {failed ? (
                      <span className="shrink-0 rounded bg-destructive/10 px-1.5 py-0.5 text-[10px] text-destructive">
                        {t("failedBadge")}
                      </span>
                    ) : null}
                    <span className="ml-auto shrink-0 font-mono text-[10px] text-muted-foreground">
                      {formatTime(event.updated_at)}
                    </span>
                  </div>
                  {failed && event.error ? (
                    <p className="truncate text-[11px] text-destructive" title={event.error}>
                      {event.error}
                    </p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}
