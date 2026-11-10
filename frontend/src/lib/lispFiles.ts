import { api, ApiError } from "@/lib/api";
import type { Host } from "@/lib/cad/lisp";

// 2D automation A1: AX-LISP open/close/findfile/load_dialog read and write one file of the PC LISP
// library at a time (GET/PUT /api/lisp/files/{name}). The server writes data files only (.txt,
// .csv, .dat, .log, .dcl) under its lock: a program can never replace a LISP program.
type File = { name: string; source: string };
const path = (name: string) => `/lisp/files/${encodeURIComponent(name)}`;

export const libraryFiles: NonNullable<Host["files"]> = {
  read: async (name) => {
    try {
      return (await api<File>(path(name))).source;
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  },
  write: async (name, text) => {
    await api(path(name), { method: "PUT", json: { source: text } });
  },
  // W3: vl-directory-files / vl-file-systime, vl-file-delete (data files only, the server checks)
  list: async () => (await api<{ items: { name: string; modified: string | null }[] }>("/lisp/names")).items,
  remove: async (name) => {
    try {
      await api(path(name), { method: "DELETE" });
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return false;
      throw e;
    }
  },
};

/** W3: getenv/setenv/getcfg/setcfg — this PC's browser keeps them (one key per setting). */
export const lispSettings: NonNullable<Host["settings"]> = {
  get: (key) => {
    try {
      return localStorage.getItem(`axcad.lisp.${key}`);
    } catch {
      return null;
    }
  },
  set: (key, value) => {
    try {
      localStorage.setItem(`axcad.lisp.${key}`, value);
    } catch {
      /* storage full or blocked: the setting lasts this run only */
    }
  },
};
