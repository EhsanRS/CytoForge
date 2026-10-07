let sessionToken: Promise<string> | undefined;
const token = () =>
  (sessionToken ??= fetch("/api/bootstrap")
    .then(async (response) => {
      if (!response.ok)
        throw new Error("The local analysis engine is unavailable");
      return (await response.json()).token as string;
    })
    .catch((error) => {
      sessionToken = undefined;
      throw error;
    }));

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function request(
  path: string,
  init: RequestInit = {},
  retry = true,
): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("X-CytoForge-Token", await token());
  if (init.body && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch(`/api${path}`, { ...init, headers });
  if (response.status === 401 && retry) {
    sessionToken = undefined;
    return request(path, init, false);
  }
  if (!response.ok) {
    const body = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    const detail = Array.isArray(body.detail)
      ? body.detail.map((d: { msg: string }) => d.msg).join("; ")
      : body.detail;
    throw new ApiError(detail || "Request failed", response.status);
  }
  return response;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const value = await (await request(path, init)).json();
  if (init?.method && !["GET", "HEAD"].includes(init.method)) {
    const workspace = value?.workspace ?? value;
    if (
      /^[a-f0-9]{32}$/.test(workspace?.id ?? "") &&
      Number.isSafeInteger(workspace.revision) &&
      Array.isArray(workspace.samples) &&
      Array.isArray(workspace.gates)
    )
      window.cytoforgeDesktop?.notifyWorkspaceChanged(workspace.id);
  }
  return value as T;
}
export async function binaryApi(path: string, init?: RequestInit) {
  const response = await request(path, init);
  return { buffer: await response.arrayBuffer(), headers: response.headers };
}
export const post = <T>(path: string, body: unknown, method = "POST") =>
  api<T>(path, { method, body: JSON.stringify(body) });
export function params(
  values: Record<string, string | number | boolean | null | undefined>,
) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(values))
    if (value != null && value !== "") query.set(key, String(value));
  return query.toString();
}
export async function download(
  path: string,
  filename: string,
  init?: RequestInit,
) {
  const blob = await (await request(path, init)).blob();
  saveBlob(blob, filename);
}
export function saveBlob(blob: Blob, filename: string) {
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(href), 30000);
}
