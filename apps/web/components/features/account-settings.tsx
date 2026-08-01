"use client";

import * as React from "react";
import { KeyRound, LogOut } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { useApp } from "@/components/features/app-shell";
import { ArchivedThreadsCard } from "@/components/features/archived-threads-card";
import { SettingsRow, SettingsSection } from "@/components/features/settings-section";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { api, ApiError } from "@/lib/api";

function PasswordSection() {
  const t = useTranslations("AccountSettings");
  const [current, setCurrent] = React.useState("");
  const [next, setNext] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  async function savePassword(event: React.FormEvent) {
    event.preventDefault();
    if (next.length < 8) {
      toast.error(t("passwordTooShort"));
      return;
    }
    if (next !== confirm) {
      toast.error(t("passwordMismatch"));
      return;
    }
    setBusy(true);
    try {
      await api.changePassword(current, next);
      toast.success(t("passwordChanged"));
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("passwordChangeFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <SettingsSection title={t("passwordTitle")} description={t("passwordDescription")}>
      <form onSubmit={savePassword} className="p-4 sm:p-5">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field>
            <FieldLabel htmlFor="account-current-password">{t("currentPassword")}</FieldLabel>
            <Input
              id="account-current-password"
              type="password"
              value={current}
              onChange={(event) => setCurrent(event.target.value)}
              autoComplete="current-password"
              disabled={busy}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor="account-new-password">{t("newPassword")}</FieldLabel>
            <Input
              id="account-new-password"
              type="password"
              value={next}
              onChange={(event) => setNext(event.target.value)}
              autoComplete="new-password"
              disabled={busy}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor="account-confirm-password">{t("confirmPassword")}</FieldLabel>
            <Input
              id="account-confirm-password"
              type="password"
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
              autoComplete="new-password"
              disabled={busy}
            />
          </Field>
        </div>
        <div className="mt-4 flex justify-end">
          <Button type="submit" disabled={busy}>
            {busy ? <Spinner /> : <KeyRound />}
            {busy ? t("savingPassword") : t("savePassword")}
          </Button>
        </div>
      </form>
    </SettingsSection>
  );
}

export function AccountSettings() {
  const t = useTranslations("AccountSettings");
  const { user, logout } = useApp();
  const initial =
    user?.name.trim().slice(0, 1).toUpperCase() ||
    user?.email.trim().slice(0, 1).toUpperCase() ||
    "?";

  return (
    <div className="flex flex-col gap-6">
      <SettingsSection title={t("identityTitle")} description={t("identityDescription")}>
        <div className="flex items-center justify-between gap-4 p-4 sm:p-5">
          <div className="flex min-w-0 items-center gap-3">
            <Avatar className="size-10">
              <AvatarFallback className="text-sm">{initial}</AvatarFallback>
            </Avatar>
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-foreground">
                {user?.name || t("nameMissing")}
              </div>
              <div className="mt-0.5 truncate text-sm text-muted-foreground">
                {user?.email || t("emailMissing")}
              </div>
            </div>
          </div>
          <Badge variant="success" className="shrink-0">
            {t("local")}
          </Badge>
        </div>
        <SettingsRow
          title={t("signOutTitle")}
          description={t("signOutDescription")}
          layout="inline"
        >
          <Button variant="outline" size="sm" onClick={logout}>
            <LogOut />
            {t("signOut")}
          </Button>
        </SettingsRow>
      </SettingsSection>

      <PasswordSection />

      <ArchivedThreadsCard />
    </div>
  );
}
