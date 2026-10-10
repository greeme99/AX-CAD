"""NFR-02 measurements. Opt-in (timings depend on the machine): AXCAD_PERF=1 pytest -s tests/test_perf.py"""

import io
import os
import statistics
import time

import ezdxf
import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("AXCAD_PERF"), reason="set AXCAD_PERF=1")


def drawing(target_bytes, holes_every):
    """Lines, polylines and text of about target_bytes; a hole + bend arc every holes_every cells."""
    d = ezdxf.new(setup=True, units=ezdxf.units.MM)  # ezdxf defaults to metres
    msp = d.modelspace()
    for layer in ("CUT", "BEND", "HOLE", "TEXT"):
        d.layers.add(layer)
    i = 0
    while True:
        for _ in range(1000):
            x, y = (i % 500) * 20.0, (i // 500) * 20.0
            msp.add_line((x, y), (x + 15, y), dxfattribs={"layer": "CUT"})
            if i % holes_every == 0:
                msp.add_circle((x + 5, y + 5), 2.5, dxfattribs={"layer": "HOLE"})
                msp.add_arc((x, y), 4, 0, 90, dxfattribs={"layer": "BEND"})
            msp.add_lwpolyline([(x, y), (x + 8, y), (x + 8, y + 8)], dxfattribs={"layer": "CUT"})
            msp.add_text(f"P{i}", dxfattribs={"layer": "TEXT", "height": 2.5}).set_placement((x, y))
            i += 1
        buf = io.StringIO()
        d.write(buf)
        data = buf.getvalue().encode()
        if len(data) >= target_bytes:
            return data, len(msp)


@pytest.mark.parametrize("holes_every", [10**9, 4, 2], ids=["no-holes", "holes-1-4", "holes-1-2"])
def test_nfr02_10mb_upload_under_10s(client, world, holes_every):
    data, n = drawing(10 * 1024**2, holes_every)
    t = time.perf_counter()
    r = client.post(
        f"/api/documents/{world.doc}/dxf",
        files={"file": ("big.dxf", data)},
        headers=world.h["designer"],
    )
    took = time.perf_counter() - t
    print(
        f"\nNFR-02 upload {len(data) / 1024**2:.1f} MB, {n} entities: {r.status_code} {took:.2f} s"
    )
    assert r.status_code == 200 and r.json()["data"]["entity_count"] == n
    assert took < 10


def test_nfr02_api_p95_under_500ms(client, world, dxf):
    client.post(
        f"/api/documents/{world.doc}/dxf",
        files={"file": ("a.dxf", dxf(50))},
        headers=world.h["designer"],
    )
    h = world.h["designer"]
    urls = (
        "/api/auth/me",
        "/api/projects",
        f"/api/projects/{world.pid}",
        f"/api/projects/{world.pid}/documents",
        f"/api/documents/{world.doc}",
    )
    for url in urls:
        times = []
        for _ in range(100):
            t = time.perf_counter()
            assert client.get(url, headers=h).status_code == 200, url
            times.append((time.perf_counter() - t) * 1000)
        p95 = statistics.quantiles(times, n=20)[-1]
        print(f"\nNFR-02 {url}: p50 {statistics.median(times):.1f} ms, p95 {p95:.1f} ms")
        assert p95 < 500, url
