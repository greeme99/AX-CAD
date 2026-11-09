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
};
