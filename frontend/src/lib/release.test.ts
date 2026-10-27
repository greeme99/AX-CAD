import { describe, expect, it } from "vitest";
import { outcome } from "./release";

describe("outcome", () => {
  it("says nothing before or during an update", () => {
    expect(outcome(null)).toBeNull();
    expect(outcome({ status: "RUNNING", from: "1.0.0", to: "1.1.0" })).toBeNull();
  });
  it("reports success and failures", () => {
    expect(outcome({ status: "OK", from: "1.0.0", to: "1.1.0" })).toEqual({ ok: true, text: "업데이트 완료: 1.0.0 → 1.1.0" });
    expect(outcome({ status: "ROLLED_BACK", message: "업데이트 실패, 이전 버전 1.0.0 로 되돌렸습니다" })?.ok).toBe(false);
  });
});
