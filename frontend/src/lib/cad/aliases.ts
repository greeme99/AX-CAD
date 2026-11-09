// 2D automation A5: command aliases from a .pgp file of the PC library (cadcraft's ALIASEDIT idea):
// "AL, *LINE" makes AL start LINE. Lines that would start an external program (no *) and names
// or commands we do not have are skipped and reported; aliases are settings, never part of a drawing.
import { ALIAS, USER_ALIAS, type Tool } from "./tools";

export const ALIAS_LIMITS = { aliases: 1000, bytes: 64 * 1024 };
export type AliasReport = { count: number; skipped: string[] };

/** Parses pgp text into aliases (not applied). */
export function parsePgp(text: string): { aliases: Map<string, Tool>; skipped: string[] } {
  const aliases = new Map<string, Tool>();
  const skipped: string[] = [];
  if (text.length > ALIAS_LIMITS.bytes) return { aliases, skipped: ["별칭 파일이 너무 큽니다 (64 KB 이하)"] };
  text.replace(/^﻿/, "").split(/\r?\n/).forEach((raw, i) => {
    const line = raw.replace(/;.*$/, "").trim();
    if (!line) return;
    const m = /^([^,\s]+)\s*,\s*(\S.*)$/.exec(line);
    if (!m) return void skipped.push(`${i + 1}번째 줄: 형식이 아닙니다`);
    const name = m[1].toUpperCase();
    const cmd = m[2].trim();
    if (!cmd.startsWith("*")) return void skipped.push(`${i + 1}번째 줄: 외부 프로그램은 실행하지 않습니다 (${name})`);
    if (!/^[A-Z][A-Z0-9_-]{0,19}$/.test(name)) return void skipped.push(`${i + 1}번째 줄: 쓸 수 없는 별칭 이름 ${name}`);
    const target = cmd.slice(1).trim().toUpperCase().replace(/^[_.]+/, "");
    const tool = ALIAS[target];
    if (!tool) return void skipped.push(`${i + 1}번째 줄: 없는 명령 ${target}`);
    if (aliases.size >= ALIAS_LIMITS.aliases) return void skipped.push(`별칭은 ${ALIAS_LIMITS.aliases}개까지입니다`);
    aliases.set(name, tool);
  });
  return { aliases, skipped };
}

/** Replaces the user aliases with the ones of this file (ALIASEDIT: "its aliases replace the ones you had"). */
export function applyPgp(text: string): AliasReport {
  const { aliases, skipped } = parsePgp(text);
  USER_ALIAS.clear();
  aliases.forEach((t, k) => USER_ALIAS.set(k, t));
  return { count: aliases.size, skipped: skipped.slice(0, 20) };
}
