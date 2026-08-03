/**
 * 점유격자 색 (S15P11A301-227).
 *
 * 두 화면이 같은 격자를 그린다 — 임무 이력의 저장된 지도(S15P11A301-203)와 메인
 * 화면의 실시간 지도. 값 체계가 서로 달라서(PGM 바이트 대 int8) 판정 함수는
 * 따로지만, **색은 같아야 한다.** 상수를 두 곳에 복사하면 언젠가 한쪽만 바뀐다.
 *
 * 미탐색을 패널 배경(#12171f)에 가깝게 두는 것이 의도다. 아직 아무것도 모르는
 * 영역이 눈에 띄면 그것을 정보로 읽게 된다.
 */

/** 벽 — 밝게 도드라진다. */
export const COLOR_OCCUPIED = [226, 232, 240] as const;

/** 탐사된 빈 공간. */
export const COLOR_FREE = [42, 53, 66] as const;

/** 미탐사 — 패널 배경에 동화된다. */
export const COLOR_UNKNOWN = [18, 23, 31] as const;

export type CellColor = typeof COLOR_OCCUPIED | typeof COLOR_FREE | typeof COLOR_UNKNOWN;
