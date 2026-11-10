# 자동화 커버리지 측정 (plan §22 W0)

Autodesk 공식 레퍼런스의 항목 목록(이름과 한 줄 요약만)을 `ref/`에 두고, AX-CAD 구현과 이름으로 대조한다.

| 스크립트 | 비교 대상 | 레퍼런스 |
|---|---|---|
| `lisp_coverage.py` | `frontend/src/lib/cad/lisp.ts` | `ref/lisp.json` (AutoLISP 함수, AutoCAD 2025 도움말) |
| `activex_coverage.py` | `objmodel.ts`(공통 객체 모델) + `vba.ts`(Utility) | `ref/activex.json` (ActiveX 객체·메서드·속성·이벤트, 2026 도움말) |
| `dotnet_coverage.py` | `sdk/dotnet/AxCad/*.cs` | `ref/dotnet.json` (Managed .NET 네임스페이스별 타입, 2024 도움말) |

```bash
python3 -I tools/coverage/lisp_coverage.py
python3 -I tools/coverage/activex_coverage.py
python3 -I tools/coverage/dotnet_coverage.py
python3 -I tools/coverage/report.py   # REPORT.md 다시 만들기
```

- 수집일 2026-11-10. 레퍼런스는 공개 도움말의 **사실(이름·한 줄 요약)**만 담고 본문·예제는 담지 않는다.
- ActiveX는 **이름 기준**(어느 객체의 멤버든 한 번 구현되면 구현으로 셈)이라 멤버 %는 상한값이다.
- 의도적 제외(샌드박스) 패턴은 각 스크립트의 `EXCLUDED*` 상수와 plan §22.5가 기준이다.
