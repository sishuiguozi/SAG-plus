"use client";

import * as React from "react";
import { ArrowUpRight } from "lucide-react";
import { useTranslations } from "next-intl";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

import type { Citation } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

type MdNode = {
  type?: string;
  value?: string;
  url?: string;
  title?: string | null;
  children?: MdNode[];
  data?: Record<string, unknown>;
};

function remarkCitationLinks(validNumbers: ReadonlySet<string>) {
  return () => {
    const visit = (node: MdNode) => {
      if (node.type === "link" || node.type === "code" || node.type === "inlineCode") return;
      if (!node.children) return;
      node.children = node.children.flatMap((child) => {
        if (child.type !== "text" || typeof child.value !== "string") {
          visit(child);
          return [child];
        }

        const parts: MdNode[] = [];
        const re = /\[(\d+)\]/g;
        let last = 0;
        let match: RegExpExecArray | null;
        while ((match = re.exec(child.value))) {
          // A bracketed number is only interactive when the backend supplied
          // traceable metadata for that exact number. Never manufacture a
          // disabled "citation" control for model-invented references.
          if (!validNumbers.has(match[1])) continue;
          if (match.index > last) {
            parts.push({ type: "text", value: child.value.slice(last, match.index) });
          }
          parts.push({
            type: "link",
            // Hash URLs survive react-markdown's URL sanitizer; the renderer below
            // replaces them with buttons, so citation clicks never navigate.
            url: `#citation-${match[1]}`,
            title: null,
            children: [{ type: "text", value: match[1] }],
            data: { hProperties: { "data-citation": match[1] } },
          });
          last = match.index + match[0].length;
        }
        if (!parts.length) return [child];
        if (last < child.value.length) {
          parts.push({ type: "text", value: child.value.slice(last) });
        }
        return parts;
      });
    };
    return visit;
  };
}

function MdImage(props: React.ImgHTMLAttributes<HTMLImageElement>) {
  const t = useTranslations("Markdown");
  const [broken, setBroken] = React.useState(false);
  const src = typeof props.src === "string" ? props.src : "";
  const external = /^(https?:|data:|blob:)/.test(src);
  if (broken || !external) {
    return (
      <span className="my-1 inline-flex max-w-full items-center gap-1.5 rounded-md border border-dashed bg-muted/40 px-2 py-1 text-xs text-muted-foreground">
        {t("imageUnavailable", { alt: props.alt ?? "" })}
      </span>
    );
  }
  // eslint-disable-next-line @next/next/no-img-element
  return (
    <img
      {...props}
      alt={props.alt ?? t("image")}
      onError={() => setBroken(true)}
      className="my-2 max-h-80 max-w-full rounded-md border"
    />
  );
}

function CitationChip({
  number,
  citation,
  onCitationClick,
  openLabel,
  sourceLabel,
}: {
  number: string;
  citation: Citation | undefined;
  onCitationClick?: (citation: Citation) => void;
  openLabel: string;
  sourceLabel: string;
}) {
  const [open, setOpen] = React.useState(false);
  const chipClass = cn(
    "relative -top-px mx-0.5 inline-flex size-[18px] items-center justify-center rounded-full bg-muted font-mono text-[10px] font-semibold leading-none text-muted-foreground no-underline outline-none transition-colors align-baseline focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
    citation
      ? "cursor-pointer hover:bg-muted-foreground/20 hover:text-foreground"
      : "cursor-default opacity-60",
  );
  const button = (
    <button type="button" disabled={!citation} className={chipClass} aria-label={sourceLabel}>
      {number}
    </button>
  );
  if (!citation) return button;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>{button}</PopoverTrigger>
      <PopoverContent
        side="top"
        align="start"
        sideOffset={6}
        className="w-64 max-w-[80vw] p-3 text-xs"
        onOpenAutoFocus={(e) => e.preventDefault()}
      >
        <div className="space-y-1.5">
          {citation.source_name && (
            <div className="truncate text-[10px] font-medium text-muted-foreground">
              {citation.source_name}
            </div>
          )}
          {citation.heading && (
            <div className="line-clamp-2 font-medium text-foreground">{citation.heading}</div>
          )}
          {citation.snippet && (
            <p className="line-clamp-3 leading-relaxed text-muted-foreground">{citation.snippet}</p>
          )}
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onCitationClick?.(citation);
            }}
            className="mt-1 inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline"
          >
            {openLabel}
            <ArrowUpRight className="size-3" aria-hidden="true" />
          </button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

export const MarkdownContent = React.memo(function MarkdownContent({
  content,
  citations,
  onCitationClick,
  streaming = false,
}: {
  content: string;
  citations?: Citation[];
  onCitationClick?: (citation: Citation) => void;
  streaming?: boolean;
}) {
  const t = useTranslations("Markdown");
  const citationByNumber = React.useMemo(() => {
    return new Map(
      (citations ?? [])
        .filter(
          (citation) => citation.kind !== "external"
            && Number.isInteger(citation.n)
            && citation.n > 0
            && Boolean(citation.chunk_id && citation.source_id),
        )
        .map((citation) => [String(citation.n), citation]),
    );
  }, [citations]);
  const citationPlugin = React.useMemo(
    () => remarkCitationLinks(new Set(citationByNumber.keys())),
    [citationByNumber],
  );

  return (
    <div
      className={cn("answer-prose text-foreground", streaming && "answer-prose--streaming")}
      aria-busy={streaming || undefined}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, citationPlugin, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          img: MdImage,
          a: ({ href, children, ...props }) => {
            const citationMatch = href?.match(/^#citation-(\d+)$/);
            if (citationMatch) {
              const n = citationMatch[1];
              const citation = citationByNumber.get(n);
              return (
                <CitationChip
                  number={n}
                  citation={citation}
                  onCitationClick={onCitationClick}
                  openLabel={t("openSource", { number: n })}
                  sourceLabel={t("source", { number: n })}
                />
              );
            }
            return (
              <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
                {children}
              </a>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});
