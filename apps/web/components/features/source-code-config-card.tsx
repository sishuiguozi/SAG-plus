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
  const [config, setConfig] = React.useState<SourceCodeConfig>({ llm_extraction_mode: "comments" });

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.getSourceCodeConfig(sourceId);
        if (!cancelled) setConfig(data);
      } catch (err) {
        if (!cancelled) toast.error(err instanceof ApiError ? err.message : t("loadFailed"));
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
      const next = await api.updateSourceCodeConfig(sourceId, { llm_extraction_mode: mode });
      setConfig(next);
      toast.success(t("saved"));
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("saveFailed"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-lg border bg-card/40 p-3">
      <div className="mb-1 text-sm font-medium">{t("title")}</div>
      <p className="mb-3 text-xs text-muted-foreground">{t("description")}</p>
      <div className="flex flex-wrap gap-2">
        {MODES.map((mode) => (
          <Button
            key={mode}
            type="button"
            size="sm"
            variant={config.llm_extraction_mode === mode ? "default" : "outline"}
            disabled={loading || saving}
            onClick={() => void save(mode)}
          >
            {t(`mode.${mode}`)}
            {mode === "comments" ? ` (${t("recommended")})` : ""}
          </Button>
        ))}
      </div>
    </div>
  );
}
