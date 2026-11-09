import { api } from "@/lib/api";

// 2D automation A5: the PC library (GET/PUT /api/lisp) also keeps recorded or written command
// scripts (.axs) and the alias file (acad.pgp): the server stores text and never runs it.
export type LibraryFile = { name: string; source: string };

export const libraryList = async (): Promise<LibraryFile[]> => (await api<{ items: LibraryFile[] }>("/lisp")).items.map(({ name, source }) => ({ name, source }));

const NAME = /^[\w가-힣][\w가-힣 .-]{0,59}$/;

/** A library file name for a script: ".axs" added when missing. */
export function scriptName(name: string): string | null {
  const n = name.trim().replace(/^.*[\\/]/, "");
  const full = /\.axs$/i.test(n) ? n : `${n}.axs`;
  return NAME.test(full) ? full : null;
}

/** Adds or replaces one file (same name, any case) and stores the whole library. */
export async function libraryPut(file: LibraryFile): Promise<void> {
  const files = await libraryList();
  const next = [...files.filter((f) => f.name.toLowerCase() !== file.name.toLowerCase()), file];
  await api("/lisp", { method: "PUT", json: { files: next } });
}

/** The .axs script of the library by name (with or without the extension), or undefined. */
export async function libraryScript(name: string): Promise<LibraryFile | undefined> {
  const want = scriptName(name)?.toLowerCase();
  if (!want) return undefined;
  return (await libraryList()).find((f) => f.name.toLowerCase() === want);
}
