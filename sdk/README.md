# AX-CAD Python SDK

`axcad.py` 한 파일(표준 라이브러리만)로 AX-CAD를 앱 밖에서 자동화합니다. 화면과 같은 REST API를 쓰므로 권한·감사 로그가 그대로 적용되고, 앱 안에서 코드를 실행하지 않습니다.

```python
from axcad import Client

cad = Client.local()  # 이 PC 설치본 (설치 토큰 자동)
doc = cad.create_document(1, "BRK-01", "PART", "브래킷")
```

| 예제 | 내용 |
|---|---|
| `examples/sheet_flat.py` | 판금 브래킷 → 전개 수치·전개도 DXF |
| `examples/box_quote.py` | 3D 블록 → SS400 견적 → Excel |
| `examples/assembly_check.py` | 판 2장 조립 → 일치 메이트 → 간섭 검사 |

실행: `python examples/sheet_flat.py <프로젝트 ID>` (PC 설치본이 켜져 있어야 함). 서버 모드는 `Client(url, token=Client.login_token(url, id, pw))`.
