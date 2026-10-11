import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { Audience, Insight, Viewer } from "./api";

/** Browser storage can be missing or blocked (private windows, previews); the app works without it. */
const safeStorage = createJSONStorage(() => {
  try {
    const s = window.localStorage;
    s.getItem("ff-probe");
    return s;
  } catch {
    const mem = new Map<string, string>();
    return {
      getItem: (k: string) => mem.get(k) ?? null,
      setItem: (k: string, v: string) => void mem.set(k, v),
      removeItem: (k: string) => void mem.delete(k),
    };
  }
});

interface ViewerState extends Viewer {
  userId: string;
  matchId: string | null;
  setAudience: (a: Audience) => void;
  setLanguage: (l: string) => void;
  setClub: (c: string | null) => void;
  setPlayer: (p: string | null) => void;
  setMatch: (m: string | null) => void;
}

/** Per-viewer settings, remembered between visits. */
export const useViewer = create<ViewerState>()(
  persist(
    (set) => ({
      userId: crypto.randomUUID(),
      audience: "casual",
      language: (navigator.language || "en").split("-")[0],
      clubId: null,
      playerId: null,
      matchId: null,
      setAudience: (audience) => set({ audience }),
      setLanguage: (language) => set({ language }),
      setClub: (clubId) => set({ clubId }),
      setPlayer: (playerId) => set({ playerId }),
      setMatch: (matchId) => set({ matchId, playerId: null, clubId: null }),
    }),
    { name: "ff-viewer", storage: safeStorage },
  ),
);

/** Where playback is. The chat sends `appliedSeq` as `as_of`; insights wait until playback reaches them. */
interface PlaybackState {
  appliedSeq: number;
  appliedT: number;
  clock: string;
  setPosition: (seq: number, t: number, clock: string) => void;
  overlay: Insight | null;
  showOverlay: (i: Insight | null) => void;
}

export const usePlayback = create<PlaybackState>()((set) => ({
  appliedSeq: 0,
  appliedT: 0,
  clock: "0'",
  setPosition: (appliedSeq, appliedT, clock) => set({ appliedSeq, appliedT, clock }),
  overlay: null,
  showOverlay: (overlay) => set({ overlay }),
}));
