import { Check, CircleDashed, Loader2, Pause, XCircle } from "lucide-react";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { DocumentStatus } from "@/lib/types";

const MAP: Record<
  DocumentStatus,
  {
    labelKey: "pending" | "loading" | "extracting" | "paused" | "ready" | "failed";
    variant: "outline" | "secondary" | "success" | "destructive";
    icon: typeof Check;
    spin?: boolean;
  }
> = {
  pending: { labelKey: "pending", variant: "outline", icon: CircleDashed },
  loading: { labelKey: "loading", variant: "secondary", icon: Loader2, spin: true },
  extracting: { labelKey: "extracting", variant: "secondary", icon: Loader2, spin: true },
  paused: { labelKey: "paused", variant: "outline", icon: Pause },
  ready: { labelKey: "ready", variant: "success", icon: Check },
  failed: { labelKey: "failed", variant: "destructive", icon: XCircle },
};

export function DocStatusBadge({ status }: { status: DocumentStatus }) {
  const t = useTranslations("DocumentStatus");
  const c = MAP[status] ?? MAP.pending;
  const Icon = c.icon;
  return (
    <Badge variant={c.variant}>
      <Icon className={cn("size-3", c.spin && "animate-spin")} />
      {t(c.labelKey)}
    </Badge>
  );
}
