/**
 * Jest manual mock for `react-native-mmkv` — FLE-47.
 *
 * MMKV v4 is a NitroModules package: `createMMKV()` reaches straight for a JSI
 * binding that does not exist in a Node test process, so *importing* it throws
 * before a single assertion runs. Any module that touches identity or the query
 * cache pulls it in transitively (`src/api/mmkv.ts` → `apiClient.ts` →
 * `todaySong.ts` → the Today screen), which is why the screen suites failed at
 * require time rather than on a test.
 *
 * This mock is a real in-memory store rather than a bag of `jest.fn()` stubs,
 * because the things worth testing around MMKV are read-after-write: the user id
 * is generated once and reused, the wizard resumes on the section it last wrote,
 * and `clearOnboardedAt()` must actually make `getOnboardedAt()` return null. A
 * stub whose `getString` returns a constant passes those tests no matter what
 * the code does.
 *
 * Stores are keyed by instance id so two `createMMKV({ id })` calls with the
 * same id share state — matching the real package, and the reason
 * `src/api/mmkv.ts` and `queryClient.ts` can safely use separate ids.
 *
 * Type drift is caught by `tsc`, which resolves the real package in
 * node_modules rather than this file.
 */

const stores = new Map<string, Map<string, string>>();

function storeFor(id: string): Map<string, string> {
  let store = stores.get(id);
  if (!store) {
    store = new Map<string, string>();
    stores.set(id, store);
  }
  return store;
}

export function createMMKV({ id }: { id: string }) {
  const store = storeFor(id);
  return {
    set(key: string, value: string | number | boolean): void {
      store.set(key, String(value));
    },
    getString(key: string): string | undefined {
      return store.get(key);
    },
    getNumber(key: string): number | undefined {
      const raw = store.get(key);
      return raw === undefined ? undefined : Number(raw);
    },
    getBoolean(key: string): boolean | undefined {
      const raw = store.get(key);
      return raw === undefined ? undefined : raw === 'true';
    },
    remove(key: string): void {
      store.delete(key);
    },
    clearAll(): void {
      store.clear();
    },
    getAllKeys(): string[] {
      return [...store.keys()];
    },
    contains(key: string): boolean {
      return store.has(key);
    },
  };
}

/**
 * Wipes every instance's storage.
 *
 * MMKV is process-global and this mock matches that, so state written by one
 * test is visible to the next one in the same file. Call from `beforeEach` in
 * any suite where that matters.
 */
export function __resetAllStores(): void {
  stores.clear();
}
