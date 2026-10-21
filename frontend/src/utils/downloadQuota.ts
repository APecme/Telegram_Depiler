export type QuotaLimit = {
  id?: number;
  period: "hour" | "day" | "week" | "month" | "custom";
  custom_value?: number | null;
  custom_unit?: "hours" | "days" | null;
  limit_bytes: number;
};

export type QuotaDraft = {
  id?: number;
  period: QuotaLimit["period"];
  custom_value?: string;
  custom_unit?: "hours" | "days";
  value: string;
  unit: "MB" | "GB" | "TB";
};

export type QuotaUsage = QuotaLimit & {
  period_label: string;
  used_bytes: number;
  reserved_bytes: number;
  remaining_bytes: number;
  next_reset_at_local: string;
};

const factors = { MB: 1024 ** 2, GB: 1024 ** 3, TB: 1024 ** 4 };

export function quotaDraft(limit?: QuotaLimit): QuotaDraft {
  if (!limit) return { period: "day", custom_value: "1", custom_unit: "hours", value: "1", unit: "GB" };
  const unit = limit.limit_bytes >= factors.TB ? "TB" : limit.limit_bytes >= factors.GB ? "GB" : "MB";
  return { id: limit.id, period: limit.period, custom_value: String(limit.custom_value || 1),
    custom_unit: limit.custom_unit || "hours", value: String(limit.limit_bytes / factors[unit]), unit };
}

export function quotaPayload(drafts: QuotaDraft[]): QuotaLimit[] {
  if (!drafts.length) throw new Error("开启下载限额至少需要一条限额");
  return drafts.map(draft => {
    const value = Number(draft.value);
    const bytes = Math.round(value * factors[draft.unit]);
    if (!Number.isFinite(value) || value <= 0 || !Number.isSafeInteger(bytes) || bytes <= 0)
      throw new Error("请填写有效的限额容量，范围为 1 字节至 8192 TB");
    const result: QuotaLimit = { ...(draft.id ? { id: draft.id } : {}), period: draft.period, limit_bytes: bytes };
    if (draft.period === "custom") {
      const interval = Number(draft.custom_value);
      if (!Number.isInteger(interval) || interval < 1 || interval > 100000)
        throw new Error("自定义周期必须为 1 至 100000 的整数");
      result.custom_value = interval;
      result.custom_unit = draft.custom_unit || "hours";
    }
    return result;
  });
}
