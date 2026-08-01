"use client";

import * as React from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import type { CodeLlmExtractionMode, SourceCodeConfig } from "@/lib/types";

const MODES: CodeLlmExtractionMode[] = ["off", "comments", "all"];

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
          setError(err instanceof ApiError ? err.message : t("loadFailed"));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sourceId, t]);

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
      <div className="mb-1 text-sm font-semibold">{t("title")}</div>
      <p className="mb-3 text-xs leading-5 text-muted-foreground">{t("description")}</p>
      <div className="flex flex-wrap gap-2">
        {MODES.map((mode) => {
          const active = config.llm_extraction_mode === mode;
          return (
            <Button
              key={mode}
              type="button"
              size="sm"
              variant={active ? "default" : "outline"}
              disabled={loading || saving}
              onClick={() => void save(mode)}
            >
              {t(`mode.${mode}`)}
              {mode === "comments" ? ` (${t("recommended")})` : ""}
            </Button>
          );
        })}
      </div>
      {error ? <p className="mt-2 text-xs text-destructive">{error}</p> : null}
      {!loading && !error ? (
        <p className="mt-2 text-[11px] text-muted-foreground">
          {t("current", { mode: t(`mode.${config.llm_extraction_mode}`) })}
        </p>
      ) : null}
    </div>
  );
}
