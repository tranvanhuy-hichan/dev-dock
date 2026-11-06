import { useCallback, useEffect, useRef, useState } from "react";

/** Gọi `fn` ngay rồi lặp lại mỗi `ms`. Dừng khi unmount hoặc `enabled=false`. Bỏ qua kết quả cũ. */
export function usePoll<T>(fn: () => Promise<T>, ms: number, enabled = true, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const tick = useRef<() => void>(() => {});

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const run = async () => {
      try {
        const r = await fnRef.current();
        if (alive) { setData(r); setError(null); }
      } catch (e) {
        if (alive) setError((e as Error).message);
      } finally {
        if (alive) { setLoading(false); timer = setTimeout(run, ms); }
      }
    };
    tick.current = () => { clearTimeout(timer); run(); };
    run();
    return () => { alive = false; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ms, enabled, ...deps]);

  const refresh = useCallback(() => tick.current(), []);
  return { data, error, loading, refresh };
}

export function useLocalState<T>(key: string, initial: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => {
    try { const s = localStorage.getItem(key); return s ? (JSON.parse(s) as T) : initial; } catch { return initial; }
  });
  const set = useCallback((n: T) => { setV(n); try { localStorage.setItem(key, JSON.stringify(n)); } catch { /* ignore */ } }, [key]);
  return [v, set];
}
