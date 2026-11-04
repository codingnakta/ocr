import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatUsd(v: number | null | undefined, digits = 3) {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return `$${v.toFixed(v < 0.01 && v > 0 ? 4 : digits)}`;
}

export function formatBytes(n?: number | null) {
  if (!n) return "-";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function formatTokens(n?: number | null) {
  if (!n) return "0";
  return n >= 10000 ? `${(n / 1000).toFixed(1)}k` : n.toLocaleString("ko-KR");
}
