"use client";

import * as React from "react";
import { Copy, RotateCcw, RotateCw, Save, Wrench } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { SettingsRow, SettingsSection } from "@/components/features/settings-section";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { useApp } from "@/components/features/app-shell";
import { api, ApiError } from "@/lib/api";
import type {
  EnvOnlyConfig,
  LancedbMaintenanceStatus,
  ModelConfig,
  ModelConfigPatch,
} from "@/lib/types";

type FieldValue = number | boolean | string;

type EditableConfigKey = keyof ModelConfig & keyof ModelConfigPatch;

type FieldSpec =
  | { key: EditableConfigKey; kind: "bool"; restart?: boolean }
  | {
      key: EditableConfigKey;
      kind: "int" | "float";
      min: number;
      max: number;
      step?: number;
      restart?: boolean;
    }
  | {
      key: EditableConfigKey;
      kind: "enum";
      options: readonly string[];
      restart?: boolean;
    };

type NumberFieldSpec = Extract<FieldSpec, { kind: "int" | "float" }>;

type SectionSpec = {
  sectionKey: string;
  restart?: boolean;
  fields: FieldSpec[];
};

const SECTIONS: SectionSpec[] = [
  {
    sectionKey: "maintenance",
    fields: [
      { key: "lancedb_maintenance_enabled", kind: "bool" },
      {
        key: "lancedb_maintenance_interval_days",
        kind: "enum",
        options: ["1", "7", "14", "30"],
      },
      { key: "lancedb_maintenance_delete_unverified", kind: "bool" },
    ],
  },
  {
    sectionKey: "vector",
    fields: [
      { key: "lancedb_ann_enabled", kind: "bool" },
      { key: "lancedb_fts_enabled", kind: "bool" },
      { key: "lancedb_search_refine_factor", kind: "int", min: 0, max: 100 },
      { key: "lancedb_search_nprobes", kind: "int", min: 0, max: 1024 },
      { key: "vector_write_job_batch_size", kind: "int", min: 100, max: 500 },
      { key: "vector_write_tail_flush_seconds", kind: "float", min: 0, max: 5, step: 0.1 },
      { key: "vector_append_new_enabled", kind: "bool" },
      { key: "vector_append_lookup_chunk_size", kind: "int", min: 100, max: 5000 },
      { key: "aux_vector_deferred_enabled", kind: "bool" },
      { key: "source_chunk_vector_embedding_batch_size", kind: "int", min: 1, max: 100 },
      { key: "source_chunk_vector_index_batch_size", kind: "int", min: 1, max: 200 },
    ],
  },
  {
    sectionKey: "disk",
    fields: [
      { key: "disk_guard_enabled", kind: "bool" },
      { key: "disk_warn_gb", kind: "float", min: 0.1, max: 1_000_000, step: 0.5 },
      { key: "disk_pause_aux_gb", kind: "float", min: 0.1, max: 1_000_000, step: 0.5 },
      { key: "disk_pause_vector_gb", kind: "float", min: 0.1, max: 1_000_000, step: 0.5 },
      { key: "disk_pause_ingest_gb", kind: "float", min: 0.1, max: 1_000_000, step: 0.5 },
      { key: "disk_check_interval_seconds", kind: "int", min: 5, max: 3600 },
    ],
  },
  {
    sectionKey: "performance",
    fields: [
      { key: "performance_slow_threshold_ms", kind: "int", min: 100, max: 600_000 },
      { key: "performance_window", kind: "int", min: 64, max: 65_536 },
    ],
  },
  {
    sectionKey: "engine",
    fields: [
      { key: "engine_cache_size", kind: "int", min: 1, max: 128 },
      { key: "engine_warmup_count", kind: "int", min: 0, max: 64 },
      { key: "job_max_attempts", kind: "int", min: 1, max: 10 },
      { key: "document_strict_filtering", kind: "bool" },
    ],
  },
  {
    sectionKey: "search",
    fields: [
      { key: "search_source_candidate_limit", kind: "int", min: 1, max: 256 },
      { key: "search_source_concurrency", kind: "int", min: 1, max: 32 },
      { key: "search_source_timeout", kind: "float", min: 1, max: 120, step: 0.5 },
      { key: "search_fallback_vector", kind: "bool" },
    ],
  },
  {
    sectionKey: "sqlite",
    restart: true,
    fields: [
      { key: "database_sqlite_pragma_tuning_enabled", kind: "bool" },
      {
        key: "database_sqlite_synchronous",
        kind: "enum",
        options: ["OFF", "NORMAL", "FULL", "EXTRA"],
      },
      { key: "database_sqlite_cache_size", kind: "int", min: -1_048_576, max: 0 },
      { key: "database_sqlite_mmap_size", kind: "int", min: 0, max: 2 ** 40 },
      {
        key: "database_sqlite_temp_store",
        kind: "enum",
        options: ["DEFAULT", "FILE", "MEMORY"],
      },
    ],
  },
  {
    sectionKey: "mineru",
    fields: [
      {
        key: "mineru_parse_method",
        kind: "enum",
        options: ["auto", "txt", "ocr"],
      },
      { key: "mineru_request_timeout", kind: "float", min: 1, max: 600, step: 1 },
      { key: "mineru_poll_interval", kind: "float", min: 0.5, max: 60, step: 0.5 },
      { key: "mineru_poll_timeout", kind: "float", min: 10, max: 7200, step: 10 },
      { key: "mineru_result_max_mb", kind: "int", min: 1, max: 2048 },
    ],
  },
  {
    sectionKey: "universe",
    fields: [
      { key: "universe_manifest_source_limit", kind: "int", min: 16, max: 2048 },
      { key: "universe_timeline_event_page_size", kind: "int", min: 10, max: 50 },
      { key: "universe_event_entity_limit", kind: "int", min: 4, max: 8 },
      { key: "universe_lod_orbit_px", kind: "int", min: 24, max: 240 },
      { key: "universe_lod_near_px", kind: "int", min: 64, max: 640 },
      { key: "universe_lod_deep_px", kind: "int", min: 120, max: 1200 },
      { key: "universe_lod_hysteresis_px", kind: "int", min: 4, max: 120 },
      { key: "universe_lod_debounce_ms", kind: "int", min: 50, max: 2000 },
      { key: "universe_proxy_budget_desktop", kind: "int", min: 256, max: 16000 },
      { key: "universe_proxy_budget_mobile", kind: "int", min: 128, max: 4800 },
      { key: "universe_node_budget_desktop", kind: "int", min: 450, max: 1200 },
      { key: "universe_node_budget_mobile", kind: "int", min: 450, max: 800 },
      { key: "universe_edge_budget_desktop", kind: "int", min: 600, max: 1800 },
      { key: "universe_edge_budget_mobile", kind: "int", min: 600, max: 1200 },
      { key: "universe_planet_radius_min", kind: "float", min: 12, max: 160, step: 1 },
      { key: "universe_planet_radius_max", kind: "float", min: 48, max: 360, step: 1 },
      { key: "universe_planet_radius_scale", kind: "float", min: 2, max: 80, step: 0.5 },
    ],
  },
];

function clampNumber(value: FieldValue | undefined, spec: NumberFieldSpec): number {
  const numeric = typeof value === "number" ? value : Number(value ?? 0);
  if (Number.isNaN(numeric)) return spec.min;
  return Math.min(spec.max, Math.max(spec.min, numeric));
}

function FieldControl({
  spec,
  value,
  onChange,
  t,
  fieldId,
}: {
  spec: FieldSpec;
  value: FieldValue | undefined;
  onChange: (value: FieldValue) => void;
  t: (key: string) => string;
  fieldId: string;
}) {
  if (spec.kind === "bool") {
    return (
      <Switch
        id={fieldId}
        checked={Boolean(value)}
        onCheckedChange={(checked) => onChange(checked)}
        aria-label={t(`fields.${spec.key}.label`)}
      />
    );
  }
  if (spec.kind === "enum") {
    return (
      <Select value={String(value ?? "")} onValueChange={onChange}>
        <SelectTrigger id={fieldId}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {spec.options.map((option) => (
            <SelectItem key={option} value={option}>
              {t(`enums.${spec.key}.${option}`)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  }
  return (
    <Input
      id={fieldId}
      type="number"
      min={spec.min}
      max={spec.max}
      step={spec.step ?? (spec.kind === "float" ? 0.1 : 1)}
      value={value === undefined || value === "" ? "" : Number(value)}
      onChange={(event) => {
        const raw = event.target.value;
        if (raw === "") {
          onChange(spec.min ?? 0);
          return;
        }
        const parsed = Number(raw);
        onChange(
          spec.kind === "int" ? Math.round(parsed) : parsed,
        );
      }}
    />
  );
}

function RecommendedHint({
  spec,
  recommended,
  t,
}: {
  spec: FieldSpec;
  recommended: Record<string, FieldValue | null | undefined>;
  t: (key: string) => string;
}) {
  const value = recommended[spec.key];
  if (value === undefined || value === null) return null;
  let text: string;
  if (spec.kind === "bool") {
    text = t(value ? "recommendedBool.on" : "recommendedBool.off");
  } else if (spec.kind === "enum") {
    text = t(`enums.${spec.key}.${String(value)}`);
  } else {
    text = String(value);
  }
  return (
    <span className="ml-2 rounded bg-emerald-500/10 px-1.5 py-0.5 text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
      {t("recommendedPrefix")} {text}
    </span>
  );
}

function MaintenanceSection() {
  const t = useTranslations("SystemConfig");
  const [status, setStatus] = React.useState<LancedbMaintenanceStatus | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [copied, setCopied] = React.useState(false);

  const load = React.useCallback(async () => {
    try {
      setStatus(await api.getMaintenanceStatus());
    } catch {
      setStatus(null);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  async function runNow() {
    setBusy(true);
    try {
      await api.triggerMaintenance();
      toast.success(t("maintenanceRunNowScheduled"));
      await load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("maintenanceRunNowFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function copyCommand() {
    if (!status) return;
    try {
      await navigator.clipboard.writeText(status.task_command);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error(t("maintenanceCopyFailed"));
    }
  }

  const sizeLabel = (bytes: number): string => {
    const gb = bytes / 1024 ** 3;
    if (gb >= 1) return `${gb.toFixed(2)} GB`;
    return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  };

  return (
    <SettingsSection
      title={t("maintenanceStatusTitle")}
      description={t("maintenanceStatusDescription")}
    >
      <div className="space-y-3 p-4 sm:p-5">
        {!status ? (
          <Skeleton className="h-20 w-full" />
        ) : (
          <>
            <div className="grid gap-3 text-sm sm:grid-cols-2">
              <div className="rounded-lg border p-3">
                <div className="text-muted-foreground">{t("maintenanceLastRun")}</div>
                <div className="mt-0.5 font-medium text-foreground">
                  {status.last_success_at
                    ? new Date(status.last_success_at).toLocaleString()
                    : t("maintenanceNever")}
                </div>
              </div>
              <div className="rounded-lg border p-3">
                <div className="text-muted-foreground">{t("maintenanceNextDue")}</div>
                <div className="mt-0.5 font-medium text-foreground">
                  {status.next_due_at
                    ? new Date(status.next_due_at).toLocaleString()
                    : t("maintenancePendingFirstRun")}
                </div>
              </div>
            </div>

            {status.pending_restart && (
              <Alert variant="default">
                <AlertTitle>{t("maintenancePendingTitle")}</AlertTitle>
                <AlertDescription>{t("maintenancePendingDescription")}</AlertDescription>
              </Alert>
            )}

            <div className="rounded-lg border p-3">
              <div className="mb-2 text-sm font-medium text-foreground">
                {t("maintenanceTablesTitle")}
              </div>
              {Object.keys(status.tables).length === 0 ? (
                <div className="text-sm text-muted-foreground">{t("maintenanceNoTables")}</div>
              ) : (
                <div className="grid gap-1.5 text-sm">
                  {Object.entries(status.tables).map(([name, table]) => (
                    <div
                      key={name}
                      className="flex flex-wrap items-center justify-between gap-2"
                    >
                      <span className="font-mono text-xs text-foreground">{name}</span>
                      <span className="text-muted-foreground">
                        {sizeLabel(table.directory_bytes)}
                        {" · "}
                        {table.fragments.toLocaleString()}
                        {" "}
                        {t("maintenanceFragments")}
                        {table.reason !== "ok" && (
                          <span className="ml-1 text-amber-600 dark:text-amber-400">
                            {table.reason}
                          </span>
                        )}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="rounded-lg border p-3">
              <div className="mb-1 text-sm text-muted-foreground">{t("maintenanceBackupHint")}</div>
              <div className="font-mono text-xs text-foreground">{status.backup_hint}</div>
            </div>

            <div className="rounded-lg border p-3">
              <div className="mb-2 text-sm font-medium text-foreground">
                {t("maintenanceTaskCommandTitle")}
              </div>
              <div className="flex flex-col gap-2 sm:flex-row">
                <code className="min-w-0 flex-1 break-all rounded-md bg-muted px-3 py-2 font-mono text-xs text-foreground">
                  {status.task_command}
                </code>
                <Button type="button" variant="outline" size="sm" onClick={() => void copyCommand()}>
                  <Copy />
                  {copied ? t("maintenanceCopied") : t("maintenanceCopy")}
                </Button>
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-end gap-2 border-t pt-3">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void runNow()}
                disabled={busy}
              >
                {busy ? <Spinner /> : <Wrench />}
                {t("maintenanceRunNow")}
              </Button>
            </div>
          </>
        )}
      </div>
    </SettingsSection>
  );
}

function DataRootSection() {
  const t = useTranslations("SystemConfig");
  const [info, setInfo] = React.useState<{
    root: string;
    dataDir: string;
    uploadDir: string;
    modelsDir: string;
    source: string;
  } | null>(null);
  const [draft, setDraft] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const desktop = typeof window !== "undefined" ? window.sagDesktop : undefined;

  React.useEffect(() => {
    if (!desktop?.getDataRoot) return;
    void desktop.getDataRoot().then((next) => {
      setInfo(next);
      setDraft(next.root);
    }).catch(() => {
      setInfo(null);
    });
  }, [desktop]);

  if (!desktop?.getDataRoot) return null;

  async function choose() {
    if (!desktop?.chooseDataRoot) return;
    setBusy(true);
    try {
      const result = await desktop.chooseDataRoot();
      if (!result.canceled) {
        setInfo(result.dataRoot);
        setDraft(result.dataRoot.root);
        toast.success(t("dataRootSaved"));
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("dataRootSaveFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function savePath() {
    if (!desktop?.setDataRoot) return;
    const value = draft.trim();
    if (!value) {
      toast.error(t("dataRootRequired"));
      return;
    }
    setBusy(true);
    try {
      const next = await desktop.setDataRoot(value);
      setInfo(next);
      setDraft(next.root);
      toast.success(t("dataRootSaved"));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("dataRootSaveFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <SettingsSection title={t("dataRootTitle")} description={t("dataRootDescription")}>
      <div className="space-y-3 p-4 sm:p-5">
        <Field>
          <FieldLabel htmlFor="data-root-path">{t("dataRootPath")}</FieldLabel>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              id="data-root-path"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder={info?.root || "D:\\SAG-data"}
              disabled={busy}
            />
            <div className="flex gap-2">
              <Button type="button" variant="outline" disabled={busy} onClick={() => void choose()}>
                {t("dataRootBrowse")}
              </Button>
              <Button type="button" disabled={busy} onClick={() => void savePath()}>
                {busy ? <Spinner /> : null}
                {t("dataRootSave")}
              </Button>
            </div>
          </div>
        </Field>
        {info ? (
          <div className="grid gap-1 rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
            <div>{t("dataRootCurrent")}: <span className="font-mono text-foreground">{info.root}</span></div>
            <div>engine: <span className="font-mono">{info.dataDir}</span></div>
            <div>uploads: <span className="font-mono">{info.uploadDir}</span></div>
            <div>models: <span className="font-mono">{info.modelsDir}</span></div>
            <div className="text-amber-600 dark:text-amber-400">{t("dataRootRestartHint")}</div>
          </div>
        ) : null}
      </div>
    </SettingsSection>
  );
}

function EnvOnlySection({ config }: { config: EnvOnlyConfig }) {
  const t = useTranslations("SystemConfig");
  const translate: (key: string) => string = React.useCallback(
    (key) => t(key as never),
    [t],
  );
  return (
    <SettingsSection
      title={translate("envOnlyTitle")}
      description={translate("envOnlyDescription")}
    >
      {config.groups.map((group) => (
        <div key={group.key} className="border-t p-4 first:border-t-0 sm:p-5">
          <div className="mb-2 text-sm font-medium leading-5">
            {translate(`envGroups.${group.key}`)}
          </div>
          <div className="grid gap-2">
            {group.items.map((item) => (
              <div
                key={item.key}
                className="grid gap-1 rounded-md border bg-muted/30 px-3 py-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
              >
                <div className="min-w-0">
                  <div className="text-xs font-mono text-muted-foreground">
                    {item.env}
                  </div>
                  <div className="truncate text-sm" title={String(item.value ?? "—")}>
                    {String(item.value ?? "—")}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </SettingsSection>
  );
}

export function SystemConfigForm() {
  const t = useTranslations("SystemConfig");
  const translate: (key: string) => string = React.useCallback(
    (key) => t(key as never),
    [t],
  );
  const { refreshCapabilities } = useApp();
  const [loaded, setLoaded] = React.useState(false);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [saving, setSaving] = React.useState(false);
  const [values, setValues] = React.useState<Record<string, FieldValue>>({});
  const [recommended, setRecommended] = React.useState<
    Record<string, FieldValue | null | undefined>
  >({});
  const [envOnly, setEnvOnly] = React.useState<EnvOnlyConfig | null>(null);

  const hydrate = React.useCallback((config: ModelConfig) => {
    const next: Record<string, FieldValue> = {};
    for (const section of SECTIONS) {
      for (const spec of section.fields) {
        const current = config[spec.key as keyof ModelConfig];
        if (typeof current === "boolean" || typeof current === "string" || typeof current === "number") {
          next[spec.key] = current;
        }
      }
    }
    setValues(next);
    setRecommended(config.recommended ?? {});
    setLoaded(true);
  }, []);

  const load = React.useCallback(async () => {
    setLoadError(null);
    try {
      hydrate(await api.getModelConfig());
    } catch (error) {
      setLoadError(error instanceof ApiError ? error.message : translate("loadFailed"));
      return;
    }
    // env 清单是只读补充，加载失败不影响可编辑表单。
    try {
      setEnvOnly(await api.getEnvOnlyConfig());
    } catch {
      setEnvOnly(null);
    }
  }, [hydrate, translate]);

  React.useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    setSaving(true);
    try {
      const patch: Record<string, FieldValue> = {};
      for (const section of SECTIONS) {
        for (const spec of section.fields) {
          const value = values[spec.key];
          if (value === undefined) continue;
          patch[spec.key] =
            spec.key === "lancedb_maintenance_interval_days"
              ? Number(value)
              : spec.kind === "int" || spec.kind === "float"
                ? clampNumber(value, spec)
                : value;
        }
      }
      const { config } = await api.saveModelConfig(patch as ModelConfigPatch);
      hydrate(config);
      await refreshCapabilities();
      toast.success(translate("saved"));
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : translate("saveFailed"));
    } finally {
      setSaving(false);
    }
  }

  const hasRecommended = Object.keys(recommended).length > 0;

  function resetToDefaults() {
    const next: Record<string, FieldValue> = {};
    for (const section of SECTIONS) {
      for (const spec of section.fields) {
        const value = recommended[spec.key];
        if (value === undefined || value === null) continue;
        if (typeof value === "boolean" || typeof value === "string" || typeof value === "number") {
          next[spec.key] = value;
        }
      }
    }
    if (Object.keys(next).length === 0) {
      toast.error(translate("resetUnavailable"));
      return;
    }
    setValues(next);
    toast.success(translate("resetApplied"));
  }

  if (loadError) {
    return (
      <SettingsSection title={translate("title")} description={translate("description")}>
        <div className="p-4 sm:p-5">
          <Alert variant="destructive">
            <AlertTitle>{translate("loadErrorTitle")}</AlertTitle>
            <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
              <span>{loadError}</span>
              <Button type="button" variant="outline" size="sm" onClick={() => void load()}>
                <RotateCw />
                {translate("retry")}
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
        {SECTIONS.slice(0, 3).map((section) => (
          <SettingsSection
            key={section.sectionKey}
            title={translate(`sections.${section.sectionKey}.title`)}
            description={translate(`sections.${section.sectionKey}.description`)}
          >
            <div className="grid gap-3 p-4 sm:grid-cols-2 sm:p-5">
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
            </div>
          </SettingsSection>
        ))}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      {SECTIONS.map((section) => {
        const restart = section.restart ?? false;
        return (
          <SettingsSection
            key={section.sectionKey}
            title={translate(`sections.${section.sectionKey}.title`)}
            description={translate(`sections.${section.sectionKey}.description`)}
          >
            {section.fields.map((spec, index) => {
              const fieldId = `sys-${spec.key}-${index}`;
              if (spec.kind === "bool") {
                return (
                  <SettingsRow
                    key={spec.key}
                    layout="inline"
                    title={translate(`fields.${spec.key}.label`)}
                    description={(
                      <>
                        {translate(`fields.${spec.key}.description`)}
                        <RecommendedHint
                          spec={spec}
                          recommended={recommended}
                          t={translate}
                        />
                      </>
                    )}
                    contentClassName="flex justify-end"
                  >
                    <FieldControl
                      spec={spec}
                      value={values[spec.key]}
                      onChange={(value) =>
                        setValues((prev) => ({ ...prev, [spec.key]: value }))
                      }
                      t={translate}
                      fieldId={fieldId}
                    />
                  </SettingsRow>
                );
              }
              return (
                <SettingsRow
                  key={spec.key}
                  title={translate(`fields.${spec.key}.label`)}
                  description={translate(`fields.${spec.key}.description`)}
                >
                  <Field>
                    <FieldLabel htmlFor={fieldId}>
                      {translate(`fields.${spec.key}.label`)}
                      <RecommendedHint
                        spec={spec}
                        recommended={recommended}
                        t={translate}
                      />
                      {restart && (
                        <span className="ml-2 rounded bg-amber-500/15 px-1.5 py-0.5 text-[11px] font-medium text-amber-600 dark:text-amber-400">
                          {translate("restartBadge")}
                        </span>
                      )}
                    </FieldLabel>
                    <FieldControl
                      spec={spec}
                      value={values[spec.key]}
                      onChange={(value) =>
                        setValues((prev) => ({ ...prev, [spec.key]: value }))
                      }
                      t={translate}
                      fieldId={fieldId}
                    />
                  </Field>
                </SettingsRow>
              );
            })}
          </SettingsSection>
        );
      })}

      <MaintenanceSection />
      <DataRootSection />
      {envOnly && <EnvOnlySection config={envOnly} />}

      <div className="flex flex-wrap justify-end gap-2 border-t pt-4">
        <Button
          type="button"
          variant="outline"
          onClick={resetToDefaults}
          disabled={saving || !hasRecommended}
          title={hasRecommended ? translate("resetApplied") : translate("resetUnavailable")}
        >
          <RotateCcw />
          {translate("resetDefault")}
        </Button>
        <Button type="button" onClick={save} disabled={saving}>
          {saving ? <Spinner /> : <Save />}
          {saving ? translate("saving") : translate("save")}
        </Button>
      </div>
    </div>
  );
}
