"use client";

import * as React from "react";
import { Check, Copy, Languages } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Spinner } from "@/components/ui/spinner";
import { api, ApiError } from "@/lib/api";

const MAX_TRANSLATE_CHARS = 5000;

function targetLangFromLocale(locale: string): "zh" | "en" {
  return locale.toLowerCase().startsWith("zh") ? "zh" : "en";
}

/**
 * 选中文本翻译工具条：在容器内选中一段文字后，选中区域下方浮现「翻译」按钮，
 * 点击调用后端 LLM 翻译，Popover 展示译文（可复制）。
 */
export function SelectionToolbar({
  containerRef,
}: {
  containerRef: React.RefObject<HTMLElement | null>;
}) {
  const t = useTranslations("Translate");
  const locale = useLocale();
  const [anchor, setAnchor] = React.useState<{ x: number; y: number } | null>(null);
  const [text, setText] = React.useState("");
  const [open, setOpen] = React.useState(false);
  const [translating, setTranslating] = React.useState(false);
  const [result, setResult] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [copied, setCopied] = React.useState(false);

  React.useEffect(() => {
    const onSelectionChange = () => {
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed) {
        setAnchor(null);
        setOpen(false);
        return;
      }
      const selectedText = selection.toString().trim();
      if (!selectedText || selectedText.length > MAX_TRANSLATE_CHARS) {
        setAnchor(null);
        setOpen(false);
        return;
      }
      // 只响应容器内的选中（避免聊天/侧栏选中也弹工具条）
      const node = selection.anchorNode;
      const element = node instanceof Element ? node : node?.parentElement;
      if (!containerRef.current || !element || !containerRef.current.contains(element)) {
        setAnchor(null);
        setOpen(false);
        return;
      }
      const range = selection.getRangeAt(0);
      const rect = range.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) {
        setAnchor(null);
        return;
      }
      setText(selectedText);
      setAnchor({
        x: Math.min(window.innerWidth - 40, Math.max(60, rect.left + rect.width / 2)),
        y: Math.max(8, rect.bottom + 4),
      });
      setOpen(false);
      setResult(null);
      setError(null);
      setCopied(false);
    };
    const onPointerUp = () => {
      // 稍等 selection 稳定后再计算
      window.setTimeout(onSelectionChange, 0);
    };
    document.addEventListener("selectionchange", onSelectionChange);
    document.addEventListener("pointerup", onPointerUp);
    return () => {
      document.removeEventListener("selectionchange", onSelectionChange);
      document.removeEventListener("pointerup", onPointerUp);
    };
  }, [containerRef]);

  async function runTranslate() {
    setTranslating(true);
    setError(null);
    setResult(null);
    try {
      const response = await api.translate({
        text,
        target_lang: targetLangFromLocale(locale),
      });
      setResult(response.translated);
      setOpen(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("failed"));
      setOpen(true);
    } finally {
      setTranslating(false);
    }
  }

  async function copyResult() {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // 剪贴板不可用时忽略
    }
  }

  if (!anchor) return null;

  return (
    <div
      className="pointer-events-auto fixed z-50 -translate-x-1/2"
      style={{ left: anchor.x, top: anchor.y }}
    >
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-7 gap-1.5 bg-background/95 shadow-md backdrop-blur"
            onClick={runTranslate}
            disabled={translating}
          >
            {translating ? <Spinner className="size-3.5" /> : <Languages className="size-3.5" />}
            {translating ? t("translating") : t("button")}
          </Button>
        </PopoverTrigger>
        <PopoverContent
          side="bottom"
          align="center"
          sideOffset={6}
          className="w-80 max-w-[85vw] p-3 text-xs"
          onOpenAutoFocus={(e) => e.preventDefault()}
        >
          {translating ? (
            <div className="flex items-center gap-2 py-2 text-muted-foreground">
              <Spinner className="size-3.5" />
              {t("translating")}
            </div>
          ) : error ? (
            <p className="text-destructive">{error}</p>
          ) : result ? (
            <div className="flex flex-col gap-2">
              <p className="max-h-56 overflow-y-auto whitespace-pre-wrap leading-relaxed text-foreground">
                {result}
              </p>
              <div className="flex justify-end">
                <Button type="button" size="sm" variant="ghost" onClick={copyResult}>
                  {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
                  {copied ? t("copied") : t("copy")}
                </Button>
              </div>
            </div>
          ) : (
            <p className="text-muted-foreground">{t("emptyHint")}</p>
          )}
        </PopoverContent>
      </Popover>
    </div>
  );
}
