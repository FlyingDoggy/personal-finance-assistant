export class ApiError extends Error {
  constructor(
    message: string,
    public code: string,
    public status: number,
  ) {
    super(message);
  }
}
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...init,
      headers,
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(
      "暂时无法连接账本。请检查本机服务后重试。",
      "NETWORK",
      0,
    );
  }
  let body: any;
  try {
    body = await response.json();
  } catch {
    throw new ApiError(
      "服务返回了无法读取的内容，请重试。",
      "INVALID_RESPONSE",
      response.status,
    );
  }
  if (!response.ok)
    throw new ApiError(
      body.error?.message || "操作没有完成，请重试。",
      body.error?.code || "UNKNOWN",
      response.status,
    );
  return body as T;
}
export const requestId = () => crypto.randomUUID();
export function toMinor(value: string): string {
  const cleaned = value.trim();
  if (!/^-?\d+(\.\d{1,2})?$/.test(cleaned))
    throw new Error("金额请填数字，最多保留两位小数。");
  const negative = cleaned.startsWith("-"),
    [whole, fraction = ""] = cleaned.replace("-", "").split(".");
  return (
    (BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0"))) *
    (negative ? -1n : 1n)
  ).toString();
}
export function toDecimal(value: string | null | undefined): string {
  if (value == null) return "";
  const n = BigInt(value),
    abs = n < 0n ? -n : n;
  return `${n < 0n ? "-" : ""}${abs / 100n}.${String(abs % 100n).padStart(2, "0")}`;
}
export function money(value: string | null | undefined, sign = false): string {
  if (value == null) return "待确认";
  const decimal = toDecimal(value),
    [whole, fraction] = decimal.split(".");
  return `${sign && BigInt(value) > 0n ? "+" : ""}${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${fraction}`;
}
export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "操作没有完成，请重试。";
}

/** Reuse a key only for an unchanged financial mutation awaiting confirmation. */
export function createPendingRequest() {
  let pending: { signature: string; key: string } | null = null;
  return {
    key(payload: unknown) {
      const signature = JSON.stringify(payload);
      if (!pending || pending.signature !== signature)
        pending = { signature, key: requestId() };
      return pending.key;
    },
    clear() {
      pending = null;
    },
  };
}
