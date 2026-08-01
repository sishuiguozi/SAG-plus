/** 行内代码块 —— 等宽、可横向滚动、柔和底色；配 CopyButton 使用。 */
export function CodeBlock({ children }: { children: string }) {
  return (
    <pre className="max-w-full overflow-x-auto rounded-md border bg-muted/40 p-3 font-mono text-xs leading-relaxed">
      {children}
    </pre>
  );
}
