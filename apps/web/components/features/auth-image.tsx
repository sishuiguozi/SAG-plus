"use client";

import * as React from "react";
import { useLocale, useTranslations } from "next-intl";

import { api } from "@/lib/api";
import { getToken } from "@/lib/auth";
import { cn } from "@/lib/utils";

// 附件需带 Bearer 访问：blob URL 会话级缓存（图片数量有限，不主动回收）
const cache = new Map<string, string>();

/** 鉴权图片 —— 本地预览用 url 直渲；服务端附件按 id 经 Bearer 拉取为 blob。 */
export function AuthImage({
  id,
  url,
  alt,
  className,
}: {
  id?: string;
  url?: string;
  alt?: string;
  className?: string;
}) {
  const t = useTranslations("Markdown");
  const locale = useLocale();
  const [src, setSrc] = React.useState<string | null>(url ?? (id ? cache.get(id) ?? null : null));

  React.useEffect(() => {
    if (url) {
      setSrc(url);
      return;
    }
    if (!id) return;
    const hit = cache.get(id);
    if (hit) {
      setSrc(hit);
      return;
    }
    let alive = true;
    fetch(api.attachmentUrl(id), {
      headers: {
        Authorization: `Bearer ${getToken() ?? ""}`,
        "Accept-Language": locale,
      },
    })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      .then((b) => {
        const obj = URL.createObjectURL(b);
        cache.set(id, obj);
        if (alive) setSrc(obj);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [id, locale, url]);

  if (!src) return <div className={cn("animate-pulse rounded-md bg-muted", className)} />;
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt={alt ?? t("image")} className={className} />;
}
