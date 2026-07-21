// mobile/src/store/uiStore.ts
// Zustand UI state store.
//
// Phase 1: empty placeholder.
// Phase 2 (02-04): loaderMessageIndex slice for Fletcher loader rotation.
//   The preferences.tsx Complete tap triggers a Sonnet call (~5-15s).
//   Instead of a static "Fletcher is listening..." spinner, we rotate through
//   three copy variants at elapsed-time thresholds:
//     0-3s  → index 0: "Fletcher is listening..."
//     3-8s  → index 1: "Working on your first lesson plan..."
//     8+s   → index 2: "Almost there..."
//   preferences.tsx manages the timers via setTimeout/clearTimeout in a useEffect.
//   uiStore holds the index so the component re-renders when it changes.
//   resetLoader() is called on unmount or when isPending becomes false.
import { create } from 'zustand';

interface UIState {
  loaderMessageIndex: number;
  setLoaderMessageIndex: (n: number) => void;
  resetLoader: () => void;
}

export const useUIStore = create<UIState>()((set) => ({
  loaderMessageIndex: 0,
  setLoaderMessageIndex: (n: number) => set({ loaderMessageIndex: n }),
  resetLoader: () => set({ loaderMessageIndex: 0 }),
}));
