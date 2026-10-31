let token = "";
export function setToken(value: string) {
  token = value;
}
export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.method && options.method !== "GET") {
    headers.set("X-Mingjian-Session", token);
    if (!(options.body instanceof FormData))
      headers.set("Content-Type", "application/json");
  }
  const response = await fetch("/api" + path, { ...options, headers });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "请求未完成，请重试" }));
    throw new Error(
      typeof error.detail === "string"
        ? error.detail
        : "请求参数无效，请刷新后重试",
    );
  }
  return response.json();
}
export const post = <T = any>(path: string, body: unknown = {}) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });

export interface Doc {
  id: string;
  display_name: string;
  format: string;
  size: number;
  parse_status: string;
  review_status: string;
  error: string | null;
  imported_at: string;
  ocr: boolean;
  requires_ocr: boolean;
  coverage_ack: boolean;
  ocr_engine?: string;
  counts: { total: number; groups: number; pending: number | null };
}
export interface Block {
  id: string;
  text: string;
  kind: string;
  locator: {
    page?: number;
    paragraph?: number;
    line?: number;
    story?: string;
    row?: number;
    column?: number;
    ocr?: boolean;
  };
}
export interface Finding {
  id: string;
  block_id: string;
  start_offset: number;
  end_offset: number;
  category: string;
  rule_id: string;
  risk: string;
  status: string;
  revision: number;
  evidence: string;
  suggestion: string;
  note: string;
  reason: string;
  strategy: { mode: string; prefix?: number; suffix?: number; text?: string };
}
export interface Detail {
  document: Doc;
  blocks: Block[];
  findings: Finding[];
  warnings: string[];
  metadata: {
    ocr?: boolean;
    requires_ocr?: boolean;
    pages?: number;
    engine?: string;
  };
  coverage_ack: boolean;
}
export interface Job {
  id: string;
  title: string;
  stage: string;
  status: string;
  done: number;
  total: number;
  error: string | null;
  result: any;
}
