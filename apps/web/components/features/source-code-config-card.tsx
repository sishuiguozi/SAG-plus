"use client";

import * as React from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import type { CodeLlmExtractionMode, SourceCodeConfig } from "@/lib/types";

const MODES: Array<{ id: CodeLlmExtractionMode; label: string; hint?: string }> = [
  { id: "off", label: "关闭" },
  { id: "comments", label: "仅注释", hint: "推荐" },
  { id: "all", label: "全部子块" },
];

export function SourceCodeConfigCard({ sourceId }: { sourceId: string }) {
  const t = useTranslations("SourceCodeConfig");
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [config, setConfig] = React.useState<SourceCodeConfig>({
    llm_extraction_mode: "comments",
  });
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.getSourceCodeConfig(sourceId);
        if (!cancelled) {
          setConfig(data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          const message =
            err instanceof ApiError ? err.message : "加载代码抽取配置失败";
          setError(message);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sourceId]);

  async function save(mode: CodeLlmExtractionMode) {
    setSaving(true);
    try {
      const next = await api.updateSourceCodeConfig(sourceId, {
        llm_extraction_mode: mode,
      });
      setConfig(next);
      setError(null);
      toast.success(t("saved"));
    } catch (err) {
      const message = err instanceof ApiError ? err.message : t("saveFailed");
      setError(message);
      toast.error(message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-lg border border-border bg-card p-4 shadow-sm">
      <div className="mb-1 text-sm font-semibold">代码抽取策略</div>
      <p className="mb-3 text-xs leading-5 text-muted-foreground">
        仅影响后续代码入库与重处理，不会追溯修改已有事件。默认“仅注释”。
      </p>
      <div className="flex flex-wrap gap-2">
        {MODES.map((mode) => {
          const active = config.llm_extraction_mode === mode.id;
          return (
            <Button
              key={mode.id}
              type="button"
              size="sm"
              variant={active ? "default" : "outline"}
              disabled={loading || saving}
              onClick={() => void save(mode.id)}
            >
              {mode.label}
              {mode.hint ? `（${mode.hint}）` : ""}
            </Button>
          );
        })}
      </div>
      {error ? <p className="mt-2 text-xs text-destructive">{error}</p> : null}
      {!loading && !error ? (
        <p className="mt-2 text-[11px] text-muted-foreground">
          当前：{MODES.find((m) => m.id === config.llm_extraction_mode)?.label || config.llm_extraction_mode}
        </p>
      ) : null}
    </div>
  );
}
