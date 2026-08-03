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
