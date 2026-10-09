export type History<T> = { past: T[]; present: T; future: T[] };

const MAX_PAST = 200;

export const initHistory = <T>(present: T): History<T> => ({ past: [], present, future: [] });

// ponytail: snapshots instead of command objects; switch to command diffs if memory matters on huge drawings
export const push = <T>(h: History<T>, next: T): History<T> => ({
  past: [...h.past, h.present].slice(-MAX_PAST),
  present: next,
  future: [],
});

export const undo = <T>(h: History<T>): History<T> =>
  h.past.length ? { past: h.past.slice(0, -1), present: h.past[h.past.length - 1], future: [h.present, ...h.future] } : h;

export const redo = <T>(h: History<T>): History<T> =>
  h.future.length ? { past: [...h.past, h.present], present: h.future[0], future: h.future.slice(1) } : h;
