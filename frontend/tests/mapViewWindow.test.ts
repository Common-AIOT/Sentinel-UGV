import { describe, expect, it } from "vitest";
import {
  expandView,
  fitView,
  gridWorldBounds,
  worldToScreen,
} from "@/lib/gridGeometry";

/**
 * 세계 좌표 고정 렌더링 (S15P11A301-367).
 *
 * 종전에는 「지금 받은 격자를 화면에 꽉 채우기」였다. SLAM 이 지도를 넓히면 격자
 * 크기와 origin 이 함께 바뀌므로 배율·위치가 통째로 달라졌고, 이미 그린 벽이
 * 화면에서 미끄러졌다 — 실기동에서 「차는 가만있는데 지도가 움직인다」로 보였다.
 *
 * 그래서 **같은 세계 좌표는 같은 화면 좌표**여야 한다는 것을 여기서 못박는다.
 */
describe("세계 좌표 창", () => {
  const meta = { resolution: 0.05, originX: -1.0, originY: -2.0 };

  it("격자 범위는 origin 에 크기를 더한 값이다", () => {
    const b = gridWorldBounds(meta, 40, 20); // 2m x 1m
    expect(b.minX).toBeCloseTo(-1.0, 9);
    expect(b.minY).toBeCloseTo(-2.0, 9);
    expect(b.maxX).toBeCloseTo(1.0, 9);
    expect(b.maxY).toBeCloseTo(-1.0, 9);
  });

  it("창은 넓어지기만 한다 — 줄면 지나간 영역이 사라져 화면이 출렁인다", () => {
    const first = { minX: 0, minY: 0, maxX: 4, maxY: 4 };
    const shrunk = { minX: 1, minY: 1, maxX: 3, maxY: 3 };
    expect(expandView(first, shrunk)).toEqual(first);

    const grown = { minX: -2, minY: 0, maxX: 4, maxY: 6 };
    expect(expandView(first, grown)).toEqual({
      minX: -2,
      minY: 0,
      maxX: 4,
      maxY: 6,
    });
  });

  it("지도가 넓어져도 같은 세계 좌표는 같은 화면 좌표에 남는다", () => {
    // 실기동 재현: 처음 4x4m 를 보다가 SLAM 이 왼쪽·위로 넓혀 8x8m 가 된다.
    const box = { w: 400, h: 400 };
    const before = { minX: 0, minY: 0, maxX: 4, maxY: 4 };
    const after = expandView(before, { minX: -4, minY: 0, maxX: 4, maxY: 8 });

    const probe = { x: 2, y: 2 }; // 이미 그려 둔 벽 한 점
    const s1 = worldToScreen(
      probe.x,
      probe.y,
      before,
      fitView(before, box.w, box.h),
    );
    const s2 = worldToScreen(
      probe.x,
      probe.y,
      after,
      fitView(after, box.w, box.h),
    );

    // 창이 두 배가 됐으니 축척은 절반이다 — 그만큼 화면 좌표도 예측 가능하게
    // 바뀌어야 하고, 종전처럼 격자 인덱스에 따라 제멋대로 튀면 안 된다.
    const f1 = fitView(before, box.w, box.h);
    const f2 = fitView(after, box.w, box.h);
    expect(f1.pxPerMeter).toBeCloseTo(100, 9);
    expect(f2.pxPerMeter).toBeCloseTo(50, 9);
    // 세계 좌표 → 화면 좌표가 창과 축척만으로 결정된다(격자 크기와 무관).
    expect(s1.x).toBeCloseTo(200, 6);
    expect(s2.x).toBeCloseTo(300, 6);
  });

  it("y 를 뒤집는다 — map 의 위쪽이 화면 위쪽이다", () => {
    const view = { minX: 0, minY: 0, maxX: 10, maxY: 10 };
    const fit = fitView(view, 100, 100);
    const top = worldToScreen(5, 9, view, fit);
    const bottom = worldToScreen(5, 1, view, fit);
    expect(top.y).toBeLessThan(bottom.y); // 화면 y 는 아래로 증가
  });

  it("종횡비를 지킨다 — 늘리면 벽 두께가 방향에 따라 달라진다", () => {
    const view = { minX: 0, minY: 0, maxX: 10, maxY: 5 };
    const fit = fitView(view, 200, 200);
    expect(fit.pxPerMeter).toBeCloseTo(20, 9); // 폭 기준으로 맞춰진다
    expect(fit.offsetY).toBeCloseTo(50, 9); // 남는 세로는 여백
  });
});
