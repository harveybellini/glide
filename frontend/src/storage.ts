// localStorage access can throw: Safari private mode, browsers with cookies or
// site data blocked, and partitioned third-party contexts all raise a
// SecurityError on `window.localStorage` itself. Reads at first render would
// otherwise take the whole app down, so every access goes through these
// helpers and a blocked store quietly degrades to in-memory storage.
type Store = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
};

const memoryStore: Store = (() => {
  const values = new Map<string, string>();
  return {
    getItem: (key) => (values.has(key) ? (values.get(key) as string) : null),
    setItem: (key, value) => {
      values.set(key, value);
    },
    removeItem: (key) => {
      values.delete(key);
    },
  };
})();

let resolved: Store | null = null;

function activeStore(): Store {
  if (resolved) {
    return resolved;
  }
  try {
    const candidate = window.localStorage;
    const probe = "glide-storage-probe";
    candidate.setItem(probe, "1");
    candidate.removeItem(probe);
    resolved = candidate;
  } catch {
    resolved = memoryStore;
  }
  return resolved;
}

export function readStored(key: string): string | null {
  try {
    return activeStore().getItem(key);
  } catch {
    resolved = memoryStore;
    return memoryStore.getItem(key);
  }
}

export function writeStored(key: string, value: string): void {
  try {
    activeStore().setItem(key, value);
  } catch {
    resolved = memoryStore;
    memoryStore.setItem(key, value);
  }
}

export function removeStored(key: string): void {
  try {
    activeStore().removeItem(key);
  } catch {
    resolved = memoryStore;
    memoryStore.removeItem(key);
  }
}
