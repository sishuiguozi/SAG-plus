// 轻量令牌存储：cookie（供中间件路由守卫）+ 本地读取（供 API 客户端加 Bearer 头）。
// MVP 方案；生产可升级为 httpOnly cookie + route handler 代理。
const TOKEN_KEY = "sag_token";

export function setToken(token: string) {
  if (typeof document === "undefined") return;
  // 7 天，SameSite=Lax
  document.cookie = `${TOKEN_KEY}=${token}; path=/; max-age=${60 * 60 * 24 * 7}; SameSite=Lax`;
}

export function getToken(): string | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.match(new RegExp(`(?:^|; )${TOKEN_KEY}=([^;]*)`));
  return m ? decodeURIComponent(m[1]) : null;
}

export function clearToken() {
  if (typeof document === "undefined") return;
  document.cookie = `${TOKEN_KEY}=; path=/; max-age=0`;
}

export const TOKEN_COOKIE = TOKEN_KEY;
