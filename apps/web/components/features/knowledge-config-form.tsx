"use client";

import * as React from "react";
import { RotateCcw, RotateCw, Save } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { useApp } from "@/components/features/app-shell";
import { SettingsRow, SettingsSection } from "@/components/features/settings-section";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Spinner } from "@/components/ui/spinner";
import { api, ApiError } from "@/lib/api";
import { SEARCH_STRATEGIES } from "@/lib/retrieval-config";
import type { ModelConfig, ModelConfigPatch } from "@/lib/types";

function RecommendedBadge({
  value,
  t,
}: {
  value: number | boolean | string | null | undefined;
  t: (key: string) => string;
}) {
  if (value === undefined || value === null) return null;
  return (
    <span className="ml-2 rounded bg-emerald-500/10 px-1.5 py-0.5 text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
      {t("recommendedPrefix")}
      {String(value)}
    </span>
  );
}

export function KnowledgeConfigForm() {
  const t = useTranslations("KnowledgeConfig");
  const translate: (key: string) => string = React.useCallback(
    (key) => t(key as never),
    [t],
  );
  const strategies = useTranslations("SearchStrategies");
  const { refreshCapabilities } = useApp();
  const [loaded, setLoaded] = React.useState(false);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [saving, setSaving] = React.useState(false);
  const [chunkMaxTokens, setChunkMaxTokens] = React.useState(1_000);
  const [chunkMode, setChunkMode] =
    React.useState<ModelConfig["document_chunk_mode"]>("standard");
  const [chunkRegex, setChunkRegex] = React.useState("");
  const [parentChunkMaxTokens, setParentChunkMaxTokens] = React.useState(1_024);
  const [parentChunkVectorize, setParentChunkVectorize] = React.useState(true);
  const [extractConcurrency, setExtractConcurrency] = React.useState(5);
  const [jobConcurrency, setJobConcurrency] = React.useState(2);
  const [strategy, setStrategy] = React.useState<ModelConfig["search_strategy"]>("multi");
  const [topK, setTopK] = React.useState(8);
  const [cacheTtl, setCacheTtl] = React.useState(30);
  const [language, setLanguage] = React.useState<ModelConfig["sag_language"]>("zh");
  const [recommended, setRecommended] = React.useState<
    Record<string, number | boolean | string | null | undefined>
  >({});

  const hydrate = React.useCallback((config: ModelConfig) => {
    setChunkMaxTokens(config.document_chunk_max_tokens ?? 1_000);
    setChunkMode(config.document_chunk_mode ?? "standard");
    setChunkRegex(config.document_chunk_regex ?? "");
    setParentChunkMaxTokens(config.parent_chunk_max_tokens ?? 1_024);
    setParentChunkVectorize(config.parent_chunk_vectorize ?? true);
    setExtractConcurrency(config.document_extract_concurrency ?? 5);
    setJobConcurrency(config.job_concurrency ?? 2);
    setStrategy(config.search_strategy);
    setTopK(config.search_top_k);
    setCacheTtl(config.search_cache_ttl_seconds);
    setLanguage(config.sag_language);
    setRecommended(config.recommended ?? {});
    setLoaded(true);
  }, []);

  const load = React.useCallback(async () => {
    setLoadError(null);
    try {
      hydrate(await api.getModelConfig());
    } catch (error) {
      setLoadError(error instanceof ApiError ? error.message : t("loadFailed"));
    }
  }, [hydrate, t]);

  React.useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    setSaving(true);
    try {
      const patch: ModelConfigPatch = {
        document_chunk_max_tokens: chunkMaxTokens,
        document_chunk_mode: chunkMode,
        document_chunk_regex: chunkRegex.trim() || null,
        parent_chunk_max_tokens: parentChunkMaxTokens,
        parent_chunk_vectorize: parentChunkVectorize,
        document_extract_concurrency: extractConcurrency,
        job_concurrency: jobConcurrency,
        search_strategy: strategy,
        search_top_k: topK,
        search_cache_ttl_seconds: cacheTtl,
        sag_language: language,
      };
      const { config } = await api.saveModelConfig(patch);
      hydrate(config);
      await refreshCapabilities();
      toast.success(t("saved"));
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : t("saveFailed"));
    } finally {
      setSaving(false);
    }
  }

  const hasRecommended = Object.keys(recommended).length > 0;

  function resetToDefaults() {
    const chunkMax = recommended.document_chunk_max_tokens;
    const chunkModeValue = recommended.document_chunk_mode;
    const extract = recommended.document_extract_concurrency;
    const job = recommended.job_concurrency;
    const strategyValue = recommended.search_strategy;
    const top = recommended.search_top_k;
    const cacheTtlRec = recommended.search_cache_ttl_seconds;
    const chunkRegexRec = recommended.document_chunk_regex;
    const parentMaxRec = recommended.parent_chunk_max_tokens;
    const parentVecRec = recommended.parent_chunk_vectorize;
    const langValue = recommended.sag_language;
    if (chunkMax === undefined && chunkModeValue === undefined && top === undefined) {
      toast.error(t("resetUnavailable"));
      return;
    }
    if (typeof chunkMax === "number") setChunkMaxTokens(chunkMax);
    if (
      chunkModeValue === "standard"
        || chunkModeValue === "heading_strict"
        || chunkModeValue === "regex"
      ) {
        setChunkMode(chunkModeValue);
      }
    if (typeof chunkRegexRec === "string") setChunkRegex(chunkRegexRec);
    if (typeof parentMaxRec === "number") setParentChunkMaxTokens(parentMaxRec);
    if (typeof parentVecRec === "boolean") setParentChunkVectorize(parentVecRec);
    if (typeof extract === "number") setExtractConcurrency(extract);
    if (typeof job === "number") setJobConcurrency(job);
    if (typeof strategyValue === "string") {
      setStrategy(strategyValue as ModelConfig["search_strategy"]);
    }
    if (typeof top === "number") setTopK(top);
    if (typeof cacheTtlRec === "number") setCacheTtl(cacheTtlRec);
    if (langValue === "zh" || langValue === "en") {
      setLanguage(langValue);
    }
    toast.success(t("resetApplied"));
  }

  if (loadError) {
    return (
      <SettingsSection title={t("title")} description={t("description")}>
        <div className="p-4 sm:p-5">
          <Alert variant="destructive">
            <AlertTitle>{t("loadErrorTitle")}</AlertTitle>
            <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
              <span>{loadError}</span>
              <Button type="button" variant="outline" size="sm" onClick={() => void load()}>
                <RotateCw />
                {t("retry")}
              </Button>
            </AlertDescription>
          </Alert>
        </div>
      </SettingsSection>
    );
  }

  if (!loaded) {
    return (
      <div className="flex flex-col gap-6">
        {[
          [t("parsingTitle"), t("parsingLoading")],
          [t("retrievalTitle"), t("retrievalLoading")],
        ].map(([title, description]) => (
          <SettingsSection key={title} title={title} description={description}>
            <div className="grid gap-3 p-4 sm:grid-cols-2 sm:p-5">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          </SettingsSection>
        ))}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <SettingsSection title={t("parsingTitle")} description={t("parsingDescription")}>
        <SettingsRow title={t("chunkSettings")} description={t("chunkDescription")}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field>
              <FieldLabel htmlFor="kb-chunk-mode">
                {t("chunkMode")}
                <RecommendedBadge t={translate} value={recommended.document_chunk_mode} />
              </FieldLabel>
              <Select
                value={chunkMode}
                onValueChange={(value) =>
                  setChunkMode(value as ModelConfig["document_chunk_mode"])
                }
              >
                <SelectTrigger id="kb-chunk-mode">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="standard">{t("smartChunking")}</SelectItem>
                  <SelectItem value="heading_strict">{t("strictHeadings")}</SelectItem>
                  <SelectItem value="regex">{t("regexChunking")}</SelectItem>
                  <SelectItem value="parent_child">{t("parentChildChunking")}</SelectItem>
                </SelectContent>
              </Select>
              <FieldDescription>
                {chunkMode === "heading_strict"
                  ? t("strictHeadingsDescription")
                  : chunkMode === "regex"
                    ? t("regexChunkingDescription")
                    : chunkMode === "parent_child"
                      ? t("parentChildChunkingDescription")
                      : t("smartChunkingDescription")}
              </FieldDescription>
            </Field>
            {chunkMode === "regex" && (
              <Field className="sm:col-span-2">
                <FieldLabel htmlFor="kb-chunk-regex">{t("regexChunkingPattern")}</FieldLabel>
                <Input
                  id="kb-chunk-regex"
                  value={chunkRegex}
                  onChange={(event) => setChunkRegex(event.target.value)}
                  placeholder="^== .+? ==$"
                />
                <FieldDescription>{t("regexChunkingPatternHint")}</FieldDescription>
              </Field>
            )}
            {chunkMode === "parent_child" && (
              <>
                <Field>
                  <FieldLabel htmlFor="kb-parent-chunk-max-tokens">
                    {t("parentChunkMaxTokens")}
                    <RecommendedBadge t={translate} value={recommended.parent_chunk_max_tokens} />
                  </FieldLabel>
                  <Input
                    id="kb-parent-chunk-max-tokens"
                    type="number"
                    min={200}
                    max={20000}
                    step={100}
                    value={parentChunkMaxTokens}
                    onChange={(event) =>
                      setParentChunkMaxTokens(
                        Math.min(20000, Math.max(200, Number(event.target.value) || 200)),
                      )
                    }
                  />
                  <FieldDescription>{t("parentChunkMaxTokensDescription")}</FieldDescription>
                </Field>
                <Field>
                  <FieldLabel htmlFor="kb-parent-chunk-vectorize">
                    {t("parentChunkVectorize")}
                    <RecommendedBadge t={translate} value={recommended.parent_chunk_vectorize} />
                  </FieldLabel>
                  <div className="flex items-center gap-2">
                    <Switch
                      id="kb-parent-chunk-vectorize"
                      checked={parentChunkVectorize}
                      onCheckedChange={setParentChunkVectorize}
                      aria-label={t("parentChunkVectorize")}
                    />
                    <span className="text-sm text-muted-foreground">
                      {parentChunkVectorize
                        ? t("parentChunkVectorizeOn")
                        : t("parentChunkVectorizeOff")}
                    </span>
                  </div>
                  <FieldDescription>{t("parentChunkVectorizeDescription")}</FieldDescription>
                </Field>
              </>
            )}
            <Field>
              <FieldLabel htmlFor="kb-chunk-max-tokens">
                {t("maxTokens")}
                <RecommendedBadge t={translate} value={recommended.document_chunk_max_tokens} />
              </FieldLabel>
              <Input
                id="kb-chunk-max-tokens"
                type="number"
                min={100}
                max={100000}
                step={100}
                value={chunkMaxTokens}
                onChange={(event) =>
                  setChunkMaxTokens(
                    Math.min(100000, Math.max(100, Number(event.target.value) || 100)),
                  )
                }
              />
              <FieldDescription>{t("maxTokensDescription")}</FieldDescription>
            </Field>
          </div>
        </SettingsRow>

        <SettingsRow title={t("extractionSettings")} description={t("extractionDescription")}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field>
              <FieldLabel htmlFor="kb-extract-concurrency">
                {t("concurrency")}
                <RecommendedBadge t={translate} value={recommended.document_extract_concurrency} />
              </FieldLabel>
              <Input
                id="kb-extract-concurrency"
                type="number"
                min={1}
                max={50}
                value={extractConcurrency}
                onChange={(event) =>
                  setExtractConcurrency(
                    Math.min(50, Math.max(1, Number(event.target.value) || 1)),
                  )
                }
              />
              <FieldDescription>{t("concurrencyDescription")}</FieldDescription>
            </Field>
          </div>
        </SettingsRow>

        <SettingsRow title={t("ingestConcurrencyTitle")} description={t("ingestConcurrencyDescription")}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field>
              <FieldLabel htmlFor="kb-job-concurrency">
                {t("jobConcurrency")}
                <RecommendedBadge t={translate} value={recommended.job_concurrency} />
              </FieldLabel>
              <Input
                id="kb-job-concurrency"
                type="number"
                min={1}
                max={16}
                value={jobConcurrency}
                onChange={(event) =>
                  setJobConcurrency(
                    Math.min(16, Math.max(1, Number(event.target.value) || 1)),
                  )
                }
              />
              <FieldDescription>{t("jobConcurrencyDescription")}</FieldDescription>
            </Field>
          </div>
        </SettingsRow>
      </SettingsSection>

      <SettingsSection title={t("retrievalTitle")} description={t("retrievalDescription")}>
        <SettingsRow title={t("retrievalRules")} description={t("retrievalRulesDescription")}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field>
              <FieldLabel htmlFor="kb-search-strategy">
                {t("retrievalStrategy")}
                <RecommendedBadge t={translate} value={recommended.search_strategy} />
              </FieldLabel>
              <Select
                value={strategy}
                onValueChange={(value) =>
                  setStrategy(value as ModelConfig["search_strategy"])
                }
              >
                <SelectTrigger id="kb-search-strategy">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SEARCH_STRATEGIES.map(({ value, labelKey }) => (
                    <SelectItem key={value} value={value}>
                      {strategies(labelKey)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field>
              <FieldLabel htmlFor="kb-language">
                {t("extractionLanguage")}
                <RecommendedBadge t={translate} value={recommended.sag_language} />
              </FieldLabel>
              <Select
                value={language}
                onValueChange={(value) => setLanguage(value as ModelConfig["sag_language"])}
              >
                <SelectTrigger id="kb-language">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="zh">{t("chinese")}</SelectItem>
                  <SelectItem value="en">{t("english")}</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field className="sm:col-span-2">
              <FieldLabel>
                {t("topK", { count: topK })}
                <RecommendedBadge t={translate} value={recommended.search_top_k} />
              </FieldLabel>
              <div className="flex h-9 items-center">
                <Slider
                  aria-label={t("topKAria")}
                  value={[topK]}
                  min={1}
                  max={50}
                  step={1}
                  onValueChange={([value]) => setTopK(value)}
                />
              </div>
            </Field>
            <Field>
              <FieldLabel htmlFor="kb-search-cache-ttl">
                {t("cacheTtl")}
                <RecommendedBadge t={translate} value={recommended.search_cache_ttl_seconds} />
              </FieldLabel>
              <Input
                id="kb-search-cache-ttl"
                type="number"
                min={0}
                max={600}
                value={cacheTtl}
                onChange={(event) =>
                  setCacheTtl(Math.min(600, Math.max(0, Number(event.target.value) || 0)))
                }
              />
              <FieldDescription>{t("cacheTtlHint")}</FieldDescription>
            </Field>
          </div>
        </SettingsRow>
      </SettingsSection>

      <div className="flex flex-wrap justify-end gap-2 border-t pt-4">
        <Button
          type="button"
          variant="outline"
          onClick={resetToDefaults}
          disabled={saving || !hasRecommended}
          title={hasRecommended ? t("resetApplied") : t("resetUnavailable")}
        >
          <RotateCcw />
          {t("resetDefault")}
        </Button>
        <Button type="button" onClick={save} disabled={saving}>
          {saving ? <Spinner /> : <Save />}
          {saving ? t("saving") : t("save")}
        </Button>
      </div>
    </div>
  );
}
