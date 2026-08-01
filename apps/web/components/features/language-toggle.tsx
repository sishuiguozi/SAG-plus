"use client";

import * as React from "react";
import { Languages } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { persistLocale } from "@/i18n/client";
import type { AppLocale } from "@/i18n/config";

export function LanguageToggle({ className }: { className?: string }) {
  const locale = useLocale();
  const t = useTranslations("Language");
  const router = useRouter();
  const [pending, startTransition] = React.useTransition();
  const nextLocale: AppLocale = locale === "zh-CN" ? "en-US" : "zh-CN";
  const targetLabel =
    nextLocale === "zh-CN" ? t("switchToChinese") : t("switchToEnglish");

  const switchLocale = () => {
    if (pending) return;
    persistLocale(nextLocale);
    startTransition(() => router.refresh());
  };

  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className={className}
            aria-label={pending ? t("switching") : targetLabel}
            aria-busy={pending}
            disabled={pending}
            onClick={switchLocale}
          >
            <Languages className="size-4" />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          {pending ? t("switching") : targetLabel}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
