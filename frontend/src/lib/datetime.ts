/**
 * 后端历史时间来自 PostgreSQL，按 UTC 存储并在 JSON 中去掉时区。
 * 前端如果直接 new Date("2026-11-16 20:48:29") 会按本地时间解析，
 * 在东八区会凭空多出 8 小时，所以这里统一补成 UTC 再展示。
 */
export function parseBackendDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  const hasTimezone = /Z|[+-]\d{2}:\d{2}$/.test(trimmed);
  const normalized = hasTimezone
    ? trimmed
    : `${trimmed.replace(" ", "T")}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(value: string | null | undefined) {
  const date = parseBackendDate(value);
  if (!date) return "";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatFullDateTime(value: string | null | undefined) {
  const date = parseBackendDate(value);
  if (!date) return "";
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatRelativeTime(value: string | null | undefined) {
  const date = parseBackendDate(value);
  if (!date) return "";
  const diff = Math.max(0, Date.now() - date.getTime());
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  return `${Math.floor(diff / 86_400_000)} 天前`;
}
