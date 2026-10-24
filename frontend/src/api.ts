export type LoaderKind = 'none' | 'fabric' | 'forge' | 'neoforge' | 'optifine';

export interface VersionEntry {
  id: string;
  type: string;
  phase: 'release' | 'snapshot' | 'pre' | 'rc' | 'old_beta' | 'old_alpha' | string;
  releaseTime: string;
}

export interface InstalledEntry {
  id: string;
  loader: string;
}

export interface JavaEntry {
  path: string;
  version: string;
  major: number;
}

export interface StatePayload {
  version: string;
  game_dir: string;
  installed: InstalledEntry[];
  javas: JavaEntry[];
  workers: number;
  busy: boolean;
  job: string | null;
}

export interface LoaderVersion {
  value: string;
  label: string;
  note?: string;
}

export interface LoaderVersionsPayload {
  loader: string;
  version: string;
  default: string;
  versions: LoaderVersion[];
  error?: string;
}

export interface JobEvent {
  kind: 'log' | 'progress' | 'status';
  t?: number;
  text?: string;
  done?: number;
  total?: number;
  files?: number;
  files_total?: number;
  status?: 'running' | 'done' | 'error';
  error?: string;
}

export interface JobPayload {
  events: JobEvent[];
  status: 'running' | 'done' | 'error';
  error: string;
  next: number;
}

export interface InstallRequest {
  version: string;
  loader: LoaderKind;
  loader_version: string;
  optifine_mode: 'none' | 'forge';
  skip_assets: boolean;
  mirror: string;
  dir: string;
  workers: number;
  java: string;
  quiet: boolean;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error((payload as { error?: string }).error || `HTTP ${response.status}`);
  }
  return payload as T;
}

export const getState = () => request<StatePayload>('/api/state');

export const getVersions = () =>
  request<{ latest: { release: string; snapshot: string }; versions: VersionEntry[]; error?: string }>(
    '/api/versions',
  );

export const getLoaderVersions = (loader: LoaderKind, version: string) =>
  request<LoaderVersionsPayload>(
    `/api/loader-versions?loader=${encodeURIComponent(loader)}&version=${encodeURIComponent(version)}`,
  );

export const pickDirectory = () => request<{ path: string | null }>('/api/pick-dir');

export const startInstall = (body: InstallRequest) =>
  request<{ job: string }>('/api/install', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

export const getJob = (id: string, since: number) =>
  request<JobPayload>(`/api/jobs/${id}?since=${since}`);
