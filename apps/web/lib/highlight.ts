import * as React from "react";

/**
 * Split `text` into segments, wrapping whole-term matches of `query` in <mark>.
 * Terms are matched case-insensitively; terms shorter than 2 chars are ignored
 * to avoid noise. Output is plain React nodes (no dangerouslySetInnerHTML), so
 * it is safe by construction against the document/chunk content it renders.
 */
export function highlightMatches(text: string, query: string): React.ReactNode[] {
  const terms = Array.from(
    new Set(
      (query || "")
        .toLowerCase()
        .split(/\s+/)
        .map((term) => term.trim())
        .filter((term) => term.length >= 2),
    ),
  );
  if (terms.length === 0 || !text) return [text];
  const escaped = terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const re = new RegExp(`(${escaped.join("|")})`, "gi");
  const lower = new Set(terms);
  return text.split(re).map((part, i) => {
    if (part && lower.has(part.toLowerCase())) {
      return React.createElement(
        "mark",
        { key: i, className: "rounded-sm bg-primary/25 px-0.5 text-foreground" },
        part,
      );
    }
    return part;
  });
}
