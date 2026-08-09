/**
 * 격자 좌표 변환 (S15P11A301-227에서 공용으로 분리).
 *
 * 임무 이력 지도(PGM)와 실시간 SLAM 지도(라이브 격자)가 **같은 규칙**을 써야 한다.
 * 한쪽만 y를 뒤집으면 두 화면의 로봇 위치가 서로 반대로 찍히는데, 각 화면만 보면
 * 둘 다 그럴싸하다. 그래서 규칙을 한 곳에 둔다.
 */

/** 좌표계. `OccupancyGrid`와 PGM의 yaml 메타데이터가 모두 이 모양이다. */
export interface GridGeometry {
  resolution: number;
  originX: number;
  originY: number;
}

/**
 * map 좌표(미터) → 격자 픽셀 좌표.
 *
 * **y를 뒤집는다.** nav2에서 origin은 격자의 **좌하단** 셀이고 화면(PGM·캔버스)은
 * 첫 행이 위다. 뒤집지 않으면 궤적이나 로봇이 지도 위에 그려지기는 하는데 위아래가
 * 반대가 된다 — 벽을 통과한 것처럼 보일 뿐 그림이 깨지지 않아 눈으로 알기 어렵다.
 * 실측 99.5% 일치로 확정된 규칙이다(S15P11A301-171).
 *
 * `gridHeight`는 **화면에 그려질 격자의 행 수**다. PGM이면 pgm.height, 라이브
 * 격자면 grid.height다.
 *
 * 그리기용 연속 좌표이므로 정수로 자르지 않는다.
 */
export function worldToPixel(
  x: number,
  y: number,
  meta: GridGeometry,
  gridHeight: number,
): { col: number; row: number } {
  return {
    col: (x - meta.originX) / meta.resolution,
    row: gridHeight - (y - meta.originY) / meta.resolution,
  };
}

/**
 * 화면에 보여줄 **세계 좌표 창**(미터) (S15P11A301-367).
 *
 * 종전 렌더링은 「지금 받은 격자를 화면에 꽉 채우기」였다. 그러면 SLAM 이 지도를
 * 넓힐 때마다 격자 크기와 origin 이 함께 바뀌므로 **배율과 위치가 통째로 달라진다**
 * — 이미 그려 둔 벽이 화면에서 미끄러지고, 로봇은 지도 중앙 부근에 머무니 「차는
 * 가만히 있는데 지도가 움직이는」 것으로 보인다.
 *
 * 그래서 세계 좌표 창을 따로 들고 **단조 증가**로만 넓힌다. 한 번 자리를 잡은
 * 지점은 창이 넓어져 축척이 줄어들 때 말고는 화면에서 움직이지 않는다.
 */
export interface ViewWindow {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

/** 격자가 덮는 세계 범위. origin 은 좌하단이므로 거기에 크기를 더한다. */
export function gridWorldBounds(
  meta: GridGeometry,
  width: number,
  height: number,
): ViewWindow {
  return {
    minX: meta.originX,
    minY: meta.originY,
    maxX: meta.originX + width * meta.resolution,
    maxY: meta.originY + height * meta.resolution,
  };
}

/** 창을 넓히기만 한다. 줄이면 지나간 영역이 사라져 화면이 다시 출렁인다. */
export function expandView(
  current: ViewWindow | null,
  next: ViewWindow,
): ViewWindow {
  if (!current) return next;
  return {
    minX: Math.min(current.minX, next.minX),
    minY: Math.min(current.minY, next.minY),
    maxX: Math.max(current.maxX, next.maxX),
    maxY: Math.max(current.maxY, next.maxY),
  };
}

/** 세계 좌표 창을 상자에 맞출 때의 축척(픽셀/미터)과 여백. 종횡비를 지킨다. */
export function fitView(
  view: ViewWindow,
  boxWidth: number,
  boxHeight: number,
): { pxPerMeter: number; offsetX: number; offsetY: number } {
  const spanX = Math.max(1e-6, view.maxX - view.minX);
  const spanY = Math.max(1e-6, view.maxY - view.minY);
  const pxPerMeter = Math.min(boxWidth / spanX, boxHeight / spanY);
  return {
    pxPerMeter,
    offsetX: (boxWidth - spanX * pxPerMeter) / 2,
    offsetY: (boxHeight - spanY * pxPerMeter) / 2,
  };
}

/**
 * 세계 좌표(미터) → 화면 픽셀.
 *
 * y 를 뒤집는다 — 캔버스는 y 가 아래로 증가하고 map 은 위로 증가한다. 화살표
 * 회전이 `-yaw` 인 것도 같은 이유이며 근거는 `mapHeading.ts` 에 있다.
 */
export function worldToScreen(
  x: number,
  y: number,
  view: ViewWindow,
  fit: { pxPerMeter: number; offsetX: number; offsetY: number },
): { x: number; y: number } {
  return {
    x: fit.offsetX + (x - view.minX) * fit.pxPerMeter,
    y: fit.offsetY + (view.maxY - y) * fit.pxPerMeter,
  };
}
