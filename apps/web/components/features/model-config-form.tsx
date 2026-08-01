"use client";

import * as React from "react";
import { Check, Plug, RotateCcw, RotateCw, Save, Sparkles, X } from "lucide-react";
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
import { Spinner } from "@/components/ui/spinner";
import { api, ApiError } from "@/lib/api";
import type {
  ModelConfig,
  ModelConfigPatch,
  ModelProviderId,
  ModelProviderSpec,
} from "@/lib/types";
import { cn } from "@/lib/utils";

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

function is302Api(url: string | null) {
  try {
    const host = new URL(url ?? "").hostname;
    return host === "api.302.ai" || host === "api.302ai.cn";
  } catch {
    return false;
  }
}

export function ModelConfigForm() {
  const t = useTranslations("ModelConfig");
  const translate: (key: string) => string = React.useCallback(
    (key) => t(key as never),
    [t],
  );
  const { capabilities, refreshCapabilities } = useApp();
  const [cfg, setCfg] = React.useState<ModelConfig | null>(null);
  const [providers, setProviders] = React.useState<ModelProviderSpec[]>([]);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [saving, setSaving] = React.useState(false);
  const [testing, setTesting] = React.useState(false);
  const [testResult, setTestResult] = React.useState<{ ok: boolean; message: string } | null>(null);

  const [llmProvider, setLlmProvider] = React.useState<ModelProviderId>("openai");
  const [llmBaseUrl, setLlmBaseUrl] = React.useState("");
  const [llmKey, setLlmKey] = React.useState("");
  const [llmModel, setLlmModel] = React.useState("");
  const [temperature, setTemperature] = React.useState(0.3);
  const [maxTokens, setMaxTokens] = React.useState(20_000);
  const [timeoutMs, setTimeoutMs] = React.useState(60_000);
  const [maxRetries, setMaxRetries] = React.useState(2);
  const [ctxWindow, setCtxWindow] = React.useState(128000);
  const [embProvider, setEmbProvider] = React.useState<"api" | "local">("api");
  const [embLocalModelFile, setEmbLocalModelFile] = React.useState("bge-m3-Q8_0.gguf");
  const [embLocalNCtx, setEmbLocalNCtx] = React.useState(2048);
  const [embLocalNThreads, setEmbLocalNThreads] = React.useState(0);
  const [embModel, setEmbModel] = React.useState("");
  const [embBaseUrl, setEmbBaseUrl] = React.useState("");
  const [embKey, setEmbKey] = React.useState("");
  const [embDims, setEmbDims] = React.useState("");
  const [documentParser, setDocumentParser] =
    React.useState<ModelConfig["document_parser"]>("auto");
  const [mineruBaseUrl, setMineruBaseUrl] = React.useState("");
  const [mineruVersion, setMineruVersion] =
    React.useState<ModelConfig["mineru_version"]>("2.5");
  const [mineruKey, setMineruKey] = React.useState("");
  const [recommended, setRecommended] = React.useState<
    Record<string, number | boolean | string | null | undefined>
  >({});

  const hydrate = React.useCallback((config: ModelConfig) => {
    setCfg(config);
    setLlmProvider(config.llm_provider);
    setLlmBaseUrl(config.llm_base_url ?? "");
    setLlmModel(config.llm_model);
    setTemperature(config.llm_temperature);
    setMaxTokens(config.llm_max_tokens);
    setTimeoutMs(config.llm_timeout_ms ?? 60_000);
    setMaxRetries(config.llm_max_retries ?? 2);
    setCtxWindow(config.llm_context_window ?? 128000);
    setEmbProvider(config.embedding_provider);
    setEmbLocalModelFile(config.embedding_local_model_file);
    setEmbLocalNCtx(config.embedding_local_n_ctx ?? 2048);
    setEmbLocalNThreads(config.embedding_local_n_threads ?? 0);
    setEmbModel(config.embedding_model);
    setEmbBaseUrl(config.embedding_base_url ?? "");
    setEmbDims(config.embedding_dimensions != null ? String(config.embedding_dimensions) : "");
    setDocumentParser(config.document_parser);
    setMineruBaseUrl(config.mineru_base_url ?? "");
    setMineruVersion(config.mineru_version);
    setLlmKey("");
    setEmbKey("");
    setMineruKey("");
    setRecommended(config.recommended ?? {});
  }, []);

  const load = React.useCallback(async () => {
    setLoadError(null);
    try {
      const [config, providerCatalog] = await Promise.all([
        api.getModelConfig(),
        api.getModelProviders(),
      ]);
      if (!providerCatalog.some((provider) => provider.id === config.llm_provider)) {
        throw new Error("Configured model provider is missing from the provider catalog");
      }
      setProviders(providerCatalog);
      hydrate(config);
    } catch (error) {
      setLoadError(error instanceof ApiError ? error.message : t("loadFailed"));
    }
  }, [hydrate, t]);

  React.useEffect(() => {
    void load();
  }, [load]);

  function currentPatch(): ModelConfigPatch {
    const patch: ModelConfigPatch = {
      llm_provider: llmProvider,
      llm_base_url: llmBaseUrl.trim() || null,
      llm_model: llmModel.trim(),
      llm_temperature: temperature,
      llm_max_tokens: maxTokens,
      llm_timeout_ms: timeoutMs,
      llm_max_retries: maxRetries,
      llm_context_window: ctxWindow,
      embedding_provider: embProvider,
      embedding_local_model_file: embLocalModelFile.trim(),
      embedding_local_n_ctx: embLocalNCtx,
      embedding_local_n_threads: embLocalNThreads,
      embedding_model: embModel.trim(),
      embedding_base_url: embBaseUrl.trim(),
      embedding_dimensions: embDims.trim() ? Number(embDims) : null,
      document_parser: documentParser,
      mineru_base_url: mineruBaseUrl.trim() || null,
      mineru_version: mineruVersion,
    };
    if (llmKey.trim()) patch.llm_api_key = llmKey.trim();
    if (embKey.trim()) patch.embedding_api_key = embKey.trim();
    if (mineruKey.trim()) patch.mineru_api_key = mineruKey.trim();
    return patch;
  }

  async function save() {
    setSaving(true);
    setTestResult(null);
    try {
      const patch = currentPatch();
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
    const rec = recommended;
    if (rec.llm_model === undefined && rec.llm_max_tokens === undefined) {
      toast.error(t("resetUnavailable"));
      return;
    }
    const provider = rec.llm_provider;
    if (
      typeof provider === "string"
      && providers.some((spec) => spec.id === provider)
    ) {
      setLlmProvider(provider as ModelProviderId);
    }
    if (typeof rec.llm_base_url === "string") setLlmBaseUrl(rec.llm_base_url);
    if (typeof rec.llm_model === "string") setLlmModel(rec.llm_model);
    if (typeof rec.llm_temperature === "number") setTemperature(rec.llm_temperature);
    if (typeof rec.llm_max_tokens === "number") setMaxTokens(rec.llm_max_tokens);
    if (typeof rec.llm_timeout_ms === "number") setTimeoutMs(rec.llm_timeout_ms);
    if (typeof rec.llm_max_retries === "number") setMaxRetries(rec.llm_max_retries);
    if (typeof rec.llm_context_window === "number") setCtxWindow(rec.llm_context_window);
    if (rec.embedding_provider === "api" || rec.embedding_provider === "local") {
      setEmbProvider(rec.embedding_provider);
    }
    if (typeof rec.embedding_local_model_file === "string") {
      setEmbLocalModelFile(rec.embedding_local_model_file);
    }
    if (typeof rec.embedding_local_n_ctx === "number") setEmbLocalNCtx(rec.embedding_local_n_ctx);
    if (typeof rec.embedding_local_n_threads === "number") {
      setEmbLocalNThreads(rec.embedding_local_n_threads);
    }
    if (typeof rec.embedding_model === "string") setEmbModel(rec.embedding_model);
    if (typeof rec.embedding_base_url === "string") setEmbBaseUrl(rec.embedding_base_url);
    if (typeof rec.embedding_dimensions === "number") {
      setEmbDims(String(rec.embedding_dimensions));
    } else if (rec.embedding_dimensions === null) {
      setEmbDims("");
    }
    const parser = rec.document_parser;
    if (parser === "auto" || parser === "markitdown" || parser === "mineru") {
      setDocumentParser(parser);
    }
    if (typeof rec.mineru_base_url === "string") setMineruBaseUrl(rec.mineru_base_url);
    const mineruVersion = rec.mineru_version;
    if (mineruVersion === "2.0" || mineruVersion === "2.5") {
      setMineruVersion(mineruVersion);
    }
    setLlmKey("");
    setEmbKey("");
    setMineruKey("");
    setTestResult(null);
    toast.success(t("resetApplied"));
  }

  async function test() {
    setTesting(true);
    setTestResult(null);
    try {
      setTestResult(await api.testModelConfig(currentPatch()));
    } catch (error) {
      setTestResult({
        ok: false,
        message: error instanceof ApiError ? error.message : t("testFailed"),
      });
    } finally {
      setTesting(false);
    }
  }

  function changeProvider(value: string) {
    const next = providers.find((provider) => provider.id === value);
    const current = providers.find((provider) => provider.id === llmProvider);
    if (!next) return;
    const knownUrls = new Set(
      providers.map((provider) => provider.default_base_url).filter(Boolean),
    );
    const knownModels = new Set(providers.map((provider) => provider.default_model));
    const knownContextWindows = new Set(
      providers.map((provider) => provider.default_context_window),
    );
    if (!llmBaseUrl.trim() || knownUrls.has(llmBaseUrl.trim())) {
      setLlmBaseUrl(next.default_base_url ?? "");
    }
    if (!llmModel.trim() || knownModels.has(llmModel.trim())) {
      setLlmModel(next.default_model);
    }
    if (knownContextWindows.has(ctxWindow)) {
      setCtxWindow(next.default_context_window);
    }
    if (
      !next.temperature_configurable ||
      !current ||
      !current.temperature_configurable ||
      temperature === current.default_temperature
    ) {
      setTemperature(next.default_temperature);
    }
    setLlmProvider(next.id);
    setTestResult(null);
  }

  async function setup302MinerU() {
    setSaving(true);
    try {
      const { config } = await api.setup302MinerU();
      hydrate(config);
      await refreshCapabilities();
      toast.success(t("mineruEnabled"));
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : t("mineruFailed"));
    } finally {
      setSaving(false);
    }
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

  if (!cfg || providers.length === 0) {
    return (
      <div className="flex flex-col gap-6">
        {[
          [t("generationTitle"), t("generationLoading")],
          [t("embeddingTitle"), t("embeddingLoading")],
          [t("parserTitle"), t("parserLoading")],
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

  const providerSpec = providers.find((provider) => provider.id === llmProvider)!;

  const keyPlaceholder = (isSet: boolean) => (isSet ? t("keyConfigured") : "sk-…");
  const generationKeyPlaceholder =
    cfg.llm_api_key_set && cfg.llm_provider === llmProvider
      ? t("keyConfigured")
      : providerSpec.api_key_placeholder;
  const canReuse302Key =
    (cfg.llm_api_key_set && is302Api(cfg.llm_base_url)) ||
    (cfg.embedding_api_key_set && is302Api(cfg.embedding_base_url));

  return (
    <div className="flex flex-col gap-6">
      <SettingsSection title={t("generationTitle")} description={t("generationDescription")}>
        <SettingsRow title={t("connectionTitle")} description={t("connectionDescription")}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field>
              <FieldLabel htmlFor="llm-provider">
                  {t("provider")}
                  <RecommendedBadge t={translate} value={recommended.llm_provider} />
                </FieldLabel>
              <Select value={llmProvider} onValueChange={changeProvider}>
                <SelectTrigger id="llm-provider">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {providers.map((provider) => (
                    <SelectItem key={provider.id} value={provider.id}>
                      {provider.display_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <FieldDescription>{t(`providerDescription.${llmProvider}`)}</FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="llm-url">Base URL</FieldLabel>
              <Input
                id="llm-url"
                value={llmBaseUrl}
                onChange={(event) => setLlmBaseUrl(event.target.value)}
                placeholder={providerSpec.default_base_url ?? t("officialEndpoint")}
              />
              <FieldDescription>{t(`baseUrlDescription.${llmProvider}`)}</FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="llm-key">API Key</FieldLabel>
              <Input
                id="llm-key"
                type="password"
                autoComplete="off"
                value={llmKey}
                onChange={(event) => setLlmKey(event.target.value)}
                placeholder={generationKeyPlaceholder}
              />
              <FieldDescription>
                {cfg.llm_provider !== llmProvider && cfg.llm_api_key_set
                  ? t("providerChangedKeyDescription")
                  : t("secretDescription")}
              </FieldDescription>
            </Field>
          </div>
        </SettingsRow>

        <SettingsRow title={t("generationParams")} description={t("generationParamsDescription")}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field>
              <FieldLabel htmlFor="llm-model">
                  {t("model")}
                  <RecommendedBadge t={translate} value={recommended.llm_model} />
                </FieldLabel>
              <Input
                id="llm-model"
                value={llmModel}
                onChange={(event) => setLlmModel(event.target.value)}
                placeholder={providerSpec.default_model}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="llm-ctxwin">
                  {t("contextWindow")}
                  <RecommendedBadge t={translate} value={recommended.llm_context_window} />
                </FieldLabel>
              <Input
                id="llm-ctxwin"
                type="number"
                min={1024}
                max={2000000}
                value={ctxWindow}
                onChange={(event) =>
                  setCtxWindow(Math.max(1024, Number(event.target.value) || 1024))
                }
              />
              <FieldDescription>{t("contextWindowDescription")}</FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="llm-maxtok">
                  {t("maxOutputTokens")}
                  <RecommendedBadge t={translate} value={recommended.llm_max_tokens} />
                </FieldLabel>
              <Input
                id="llm-maxtok"
                type="number"
                min={1}
                max={32768}
                value={maxTokens}
                onChange={(event) =>
                  setMaxTokens(Math.max(1, Number(event.target.value) || 1))
                }
              />
            </Field>
            <Field>
              <FieldLabel>
                {t("temperature", {
                  value: (
                    providerSpec.temperature_configurable
                      ? temperature
                      : providerSpec.default_temperature
                  ).toFixed(1),
                })}
                <RecommendedBadge t={translate} value={recommended.llm_temperature} />
              </FieldLabel>
              <div className="flex h-9 items-center">
                <Slider
                  value={[
                    providerSpec.temperature_configurable
                      ? temperature
                      : providerSpec.default_temperature,
                  ]}
                  min={0}
                  max={2}
                  step={0.1}
                  disabled={!providerSpec.temperature_configurable}
                  onValueChange={([value]) => setTemperature(value)}
                />
              </div>
              <FieldDescription>
                {t(
                  !providerSpec.temperature_configurable
                    ? "fixedTemperatureDescription"
                    : "temperatureDescription",
                )}
              </FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="llm-timeout">
                  {t("timeout")}
                  <RecommendedBadge t={translate} value={recommended.llm_timeout_ms} />
                </FieldLabel>
              <Input
                id="llm-timeout"
                type="number"
                min={1000}
                max={600000}
                step={1000}
                value={timeoutMs}
                onChange={(event) =>
                  setTimeoutMs(
                    Math.min(600000, Math.max(1000, Number(event.target.value) || 1000)),
                  )
                }
              />
              <FieldDescription>{t("timeoutDescription")}</FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="llm-retries">
                  {t("retries")}
                  <RecommendedBadge t={translate} value={recommended.llm_max_retries} />
                </FieldLabel>
              <Input
                id="llm-retries"
                type="number"
                min={0}
                max={10}
                step={1}
                value={maxRetries}
                onChange={(event) =>
                  setMaxRetries(Math.min(10, Math.max(0, Number(event.target.value) || 0)))
                }
              />
              <FieldDescription>{t("retriesDescription")}</FieldDescription>
            </Field>
          </div>
        </SettingsRow>
      </SettingsSection>

      <SettingsSection title={t("embeddingTitle")} description={t("embeddingDescription")}>
        <SettingsRow
          title={t("embeddingSourceTitle")}
          description={
            embProvider === "local"
              ? t("embeddingLocalDescription")
              : t("embeddingApiDescription")
          }
        >
          <Field>
            <FieldLabel htmlFor="emb-provider">
              {t("embeddingSource")}
              <RecommendedBadge t={translate} value={recommended.embedding_provider} />
            </FieldLabel>
            <Select
              value={embProvider}
              onValueChange={(value) => setEmbProvider(value as "api" | "local")}
            >
              <SelectTrigger id="emb-provider">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="local">{t("embeddingSourceLocal")}</SelectItem>
                <SelectItem value="api">{t("embeddingSourceApi")}</SelectItem>
              </SelectContent>
            </Select>
            <FieldDescription>
              {embProvider === "local"
                ? t("embeddingSourceLocalHint")
                : t("embeddingSourceApiHint")}
            </FieldDescription>
          </Field>
        </SettingsRow>

        {embProvider === "local" ? (
          <>
          <SettingsRow title={t("localModelTitle")} description={t("localModelDescription")}>
            <div className="grid gap-3">
              {(() => {
                const local = capabilities?.local_embedding;
                if (!local) {
                  return (
                    <Alert>
                      <AlertTitle>{t("localModelUnknown")}</AlertTitle>
                      <AlertDescription>{t("localModelUnknownHint")}</AlertDescription>
                    </Alert>
                  );
                }
                return (
                  <>
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                      <span className="font-medium text-foreground">
                        {t("localModel")}: bge-m3 (Q8_0)
                      </span>
                      {local.model_size_mb != null && (
                        <span className="text-muted-foreground">
                          {t("localModelSize", { size: local.model_size_mb })}
                        </span>
                      )}
                      <span
                        className={
                          local.ready
                            ? "rounded bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-600 dark:text-emerald-400"
                            : "rounded bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-600 dark:text-amber-400"
                        }
                      >
                        {local.ready ? t("localModelReady") : t("localModelNotReady")}
                      </span>
                    </div>
                    <p className="break-all rounded-md bg-muted/50 px-3 py-2 font-mono text-xs text-muted-foreground">
                      {local.model_path}
                    </p>
                    {local.error && (
                      <Alert variant="destructive">
                        <AlertTitle>{t("localModelError")}</AlertTitle>
                        <AlertDescription>{local.error}</AlertDescription>
                      </Alert>
                    )}
                  </>
                );
              })()}
            </div>
          </SettingsRow>
          <SettingsRow title={t("localParamsTitle")} description={t("localParamsDescription")}>
            <div className="grid gap-4 sm:grid-cols-3">
              <Field>
                <FieldLabel htmlFor="emb-local-file">
                  {t("localModelFile")}
                  <RecommendedBadge t={translate} value={recommended.embedding_local_model_file} />
                </FieldLabel>
                <Input
                  id="emb-local-file"
                  value={embLocalModelFile}
                  onChange={(event) => setEmbLocalModelFile(event.target.value)}
                  placeholder="bge-m3-Q8_0.gguf"
                />
                <FieldDescription>{t("localModelFileHint")}</FieldDescription>
              </Field>
              <Field>
                <FieldLabel htmlFor="emb-local-ctx">
                  {t("localNCtx")}
                  <RecommendedBadge t={translate} value={recommended.embedding_local_n_ctx} />
                </FieldLabel>
                <Input
                  id="emb-local-ctx"
                  type="number"
                  min={256}
                  max={8192}
                  step={256}
                  value={embLocalNCtx}
                  onChange={(event) =>
                    setEmbLocalNCtx(Math.min(8192, Math.max(256, Number(event.target.value) || 2048)))
                  }
                />
                <FieldDescription>{t("localNCtxHint")}</FieldDescription>
              </Field>
              <Field>
                <FieldLabel htmlFor="emb-local-threads">
                  {t("localNThreads")}
                  <RecommendedBadge t={translate} value={recommended.embedding_local_n_threads} />
                </FieldLabel>
                <Input
                  id="emb-local-threads"
                  type="number"
                  min={0}
                  max={128}
                  value={embLocalNThreads}
                  onChange={(event) =>
                    setEmbLocalNThreads(Math.min(128, Math.max(0, Number(event.target.value) || 0)))
                  }
                />
                <FieldDescription>{t("localNThreadsHint")}</FieldDescription>
              </Field>
            </div>
          </SettingsRow>
          </>
        ) : (
          <SettingsRow
            title={t("modelAndConnection")}
            description={t(
              providerSpec.can_reuse_embedding_credentials
                ? "embeddingConnectionDescription"
                : "embeddingNativeConnectionDescription",
            )}
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <Field>
                <FieldLabel htmlFor="emb-model">
                  {t("model")}
                  <RecommendedBadge t={translate} value={recommended.embedding_model} />
                </FieldLabel>
                <Input
                  id="emb-model"
                  value={embModel}
                  onChange={(event) => setEmbModel(event.target.value)}
                  placeholder="bge-large-zh-v1.5"
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="emb-dims">
                  {t("dimensions")}
                  <RecommendedBadge t={translate} value={recommended.embedding_dimensions} />
                </FieldLabel>
                <Input
                  id="emb-dims"
                  type="number"
                  min={1}
                  max={8192}
                  value={embDims}
                  onChange={(event) => setEmbDims(event.target.value)}
                  placeholder={t("modelDefault")}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="emb-url">{t("optionalBaseUrl")}</FieldLabel>
                <Input
                  id="emb-url"
                  value={embBaseUrl}
                  onChange={(event) => setEmbBaseUrl(event.target.value)}
                  placeholder="https://api.302ai.cn/v1"
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="emb-key">{t("optionalApiKey")}</FieldLabel>
                <Input
                  id="emb-key"
                  type="password"
                  autoComplete="off"
                  value={embKey}
                  onChange={(event) => setEmbKey(event.target.value)}
                  placeholder={
                    cfg.embedding_api_key_set
                      ? t("keyConfigured")
                      : providerSpec.can_reuse_embedding_credentials
                        ? t("reuseGeneration")
                        : t("separateEmbeddingKey")
                  }
                />
              </Field>
            </div>
          </SettingsRow>
        )}
      </SettingsSection>

      <SettingsSection
        title={t("parserTitle")}
        description={t("parserDescription")}
      >
        <SettingsRow title={t("parserEngine")} description={t("parserEngineDescription")}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field>
              <FieldLabel htmlFor="document-parser">
                  {t("parserMethod")}
                  <RecommendedBadge t={translate} value={recommended.document_parser} />
                </FieldLabel>
              <Select
                value={documentParser}
                onValueChange={(value) =>
                  setDocumentParser(value as ModelConfig["document_parser"])
                }
              >
                <SelectTrigger id="document-parser">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">{t("autoRecommended")}</SelectItem>
                  <SelectItem value="markitdown">MarkItDown</SelectItem>
                  <SelectItem value="mineru">MinerU</SelectItem>
                </SelectContent>
              </Select>
              <FieldDescription>
                {documentParser === "auto"
                  ? t("autoDescription")
                  : documentParser === "markitdown"
                    ? t("markitdownDescription")
                    : t("mineruDescription")}
              </FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="mineru-version">
                  {t("mineruVersion")}
                  <RecommendedBadge t={translate} value={recommended.mineru_version} />
                </FieldLabel>
              <Select
                value={mineruVersion}
                onValueChange={(value) =>
                  setMineruVersion(value as ModelConfig["mineru_version"])
                }
              >
                <SelectTrigger id="mineru-version">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="2.5">2.5</SelectItem>
                  <SelectItem value="2.0">2.0</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field>
              <FieldLabel htmlFor="mineru-url">MinerU Base URL</FieldLabel>
              <Input
                id="mineru-url"
                value={mineruBaseUrl}
                onChange={(event) => setMineruBaseUrl(event.target.value)}
                placeholder="https://api.302ai.cn"
              />
              <FieldDescription>{t("mineruPricing")}</FieldDescription>
              {canReuse302Key && !cfg.mineru_api_key_set && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={saving || testing}
                  onClick={() => void setup302MinerU()}
                  className="w-fit"
                >
                  <Sparkles />
                  {t("reuse302Key")}
                </Button>
              )}
            </Field>
            <Field>
              <FieldLabel htmlFor="mineru-key">MinerU API Key</FieldLabel>
              <Input
                id="mineru-key"
                type="password"
                autoComplete="off"
                value={mineruKey}
                onChange={(event) => setMineruKey(event.target.value)}
                placeholder={keyPlaceholder(cfg.mineru_api_key_set)}
              />
              <FieldDescription>{t("secretDescription")}</FieldDescription>
            </Field>
          </div>
        </SettingsRow>
      </SettingsSection>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4">
        <div className="min-h-5 min-w-0">
          {testResult && (
            <span
              className={cn(
                "inline-flex items-center gap-1.5 text-sm",
                testResult.ok ? "text-success" : "text-destructive",
              )}
            >
              {testResult.ok ? <Check className="size-4" /> : <X className="size-4" />}
              {testResult.message}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            onClick={resetToDefaults}
            variant="outline"
            disabled={saving || testing || !hasRecommended}
            title={hasRecommended ? t("resetApplied") : t("resetUnavailable")}
          >
            <RotateCcw />
            {t("resetDefault")}
          </Button>
          <Button type="button" onClick={test} variant="outline" disabled={testing || saving}>
            {testing ? <Spinner /> : <Plug />}
            {testing ? t("testing") : t("testGeneration")}
          </Button>
          <Button type="button" onClick={save} disabled={saving || testing}>
            {saving ? <Spinner /> : <Save />}
            {saving ? t("saving") : t("save")}
          </Button>
        </div>
      </div>
    </div>
  );
}
