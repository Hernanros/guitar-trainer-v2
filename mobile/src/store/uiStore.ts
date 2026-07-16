// mobile/src/store/uiStore.ts
// Zustand UI state store — placeholder for Phase 1.
// Phase 2 will add practice session state, streak tracking, etc.
import { create } from 'zustand';

interface UIState {
  // Placeholder — no UI state needed in Phase 1.
  // Phase 2: add sessionActive, currentSongId, streakCount, etc.
}

export const useUIStore = create<UIState>()(() => ({
  // empty state — extended in Phase 2
}));
