import { Inter, JetBrains_Mono } from "next/font/google";

// 正文与标题统一无衬线（Notion/Codex 风）；标题用紧字距 .font-display 区分
export const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

// 代码 / 数据
export const jbmono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jbmono",
  display: "swap",
});

export const fontVars = `${inter.variable} ${jbmono.variable}`;
