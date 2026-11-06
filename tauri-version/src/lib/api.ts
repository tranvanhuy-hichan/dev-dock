import { invoke } from "@tauri-apps/api/core";

export const inTauri = () => typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export async function call<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  if (!inTauri()) throw new Error("Chức năng này cần chạy trong app desktop (npm run tauri dev).");
  try {
    return await invoke<T>(cmd, args);
  } catch (e) {
    throw new Error(typeof e === "string" ? e : (e as Error)?.message ?? String(e));
  }
}

// ---- SysMonitor ----
export interface DiskInfo { name: string; mount: string; fs: string; total: number; available: number }
export interface GpuInfo { name: string; util: number; mem_used: number; mem_total: number; temp: number }
export interface NetIface { name: string; rx_total: number; tx_total: number; rx_rate: number; tx_rate: number }
export interface Snapshot {
  host: string; os: string; uptime: number; cpu_brand: string; cpu_total: number; cpu_cores: number[];
  mem_used: number; mem_total: number; swap_used: number; swap_total: number; load: [number, number, number];
  net: NetIface[]; net_rx_rate: number; net_tx_rate: number; disks: DiskInfo[]; gpus: GpuInfo[];
}
export interface ProcInfo { pid: number; name: string; cpu: number; mem: number; status: string; user: string | null; cmd: string }
export interface PortInfo { port: number; protocol: string; address: string; pid: number; process: string }

// ---- Docker ----
export interface ContainerInfo { id: string; name: string; image: string; state: string; status: string; ports: string[]; created: number; project: string | null }
export interface ImageInfo { id: string; tags: string[]; size: number; created: number; containers: number }
export interface ContainerStats { id: string; name: string; cpu: number; mem_used: number; mem_limit: number; net_rx: number; net_tx: number }
export interface DockerInfo { version: string; os: string; cpus: number; mem_total: number; containers: number; running: number; images: number }
export interface DockerStatus { available: boolean; error: string | null; info: DockerInfo | null }

// ---- Vault ----
export interface Snippet {
  id: number; title: string; language: string; code: string; description: string; tags: string[];
  folder: string; favorite: boolean; gist_id: string | null; created: number; updated: number;
}
export interface SnippetInput {
  id?: number | null; title: string; language: string; code: string; description: string;
  tags: string[]; folder: string; favorite: boolean;
}
export interface Filter { query?: string; folder?: string; tag?: string; language?: string; favorites_only?: boolean }
export interface Facets {
  folders: [string, number][]; tags: [string, number][]; languages: [string, number][]; total: number; favorites: number;
}

// ---- Disk Cleaner ----
export type CGroup = "project" | "cache" | "docker" | "large" | "dupe" | "system";
export interface CItem {
  id: string; group: CGroup; kind: string; paths: string[]; title: string; size: number;
  level: "safe" | "review"; note: string; rebuild: string | null; docker_type: string | null; sys_type: string | null; cmd: string | null;
}
export interface CleanSettings {
  trash_large: boolean; min_mb: number; large_mb: number; scan_large: boolean; dup_scan: boolean; dup_mb: number;
  ignored: string[]; ignored_titles: Record<string, string>; total_freed: number; clean_count: number;
}
export interface ScanResult { items: CItem[]; disk: { total: number; free: number }; scanned_at: number }
export interface TreeData { path: string; total: number; entries: { name: string; size: number; dir: boolean }[] }
export interface CleanReport { done: string[]; errors: string[]; freed: number; trashed: number }
