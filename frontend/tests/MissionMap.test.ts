/**
 * 임무 지도 파싱·좌표 변환 시험 (S15P11A301-223).
 *
 * 대상은 S15P11A301-203이 만든 `MissionMap`의 순수 함수들이다. 시험을 붙이는
 * 이유는 이 로직이 틀렸을 때 **화면이 정상으로 보인다**는 것이다.
 *
 * * y를 뒤집지 않으면 궤적이 지도 위에 그려지는데 위아래가 반대다. 벽을 통과한
 *   것처럼 보일 뿐 그림이 깨지지 않는다.
 * * 미탐사 값 205를 임계값으로 판정하면 미탐사 영역 전체가 탐사된 바닥으로
 *   그려진다. 벽은 그대로 맞다.
 * * 헤더의 공백 하나를 더/덜 건너뛰면 이미지가 한 픽셀씩 밀린다.
 *
 * `tsc`와 `next build`는 이 중 어느 것도 잡지 못한다.
 */

import { describe, expect, it } from "vitest";
import {
  cellColor,
  parsePgm,
  parseYaml,
  worldToPixel,
} from "@/features/mapping/MissionMap";

/** 헤더 문자열과 픽셀 바이트로 P5 파일을 만든다. */
function pgm(header: string, pixels: number[]): ArrayBuffer {
  const head = new TextEncoder().encode(header);
  const out = new Uint8Array(head.length + pixels.length);
  out.set(head, 0);
  out.set(Uint8Array.from(pixels), head.length);
  return out.buffer;
}

describe("parsePgm", () => {
  it("map_saver가 쓰는 최소 헤더를 읽는다", () => {
    // 실서버 지도의 실제 헤더 형태다: "P5\n251 294\n255\n"
    const image = parsePgm(pgm("P5\n3 2\n255\n", [1, 2, 3, 4, 5, 6]));
    expect(image.width).toBe(3);
    expect(image.height).toBe(2);
    expect(Array.from(image.pixels)).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it("주석과 여러 공백을 건너뛴다", () => {
    const image = parsePgm(
      pgm("P5\n# CREATOR: map_saver\n  2   1  \n255\n", [7, 8]),
    );
    expect([image.width, image.height]).toEqual([2, 1]);
    expect(Array.from(image.pixels)).toEqual([7, 8]);
  });

  it("maxval 뒤 공백 하나만 건너뛴다", () => {
    // 두 개를 건너뛰면 첫 픽셀이 사라지고 이미지가 한 칸 밀린다.
    const image = parsePgm(pgm("P5\n2 1\n255\n", [10, 20]));
    expect(image.pixels[0]).toBe(10);
  });

  it("P5가 아니면 거부한다", () => {
    // P2(ASCII)를 이진으로 읽으면 쓰레기 이미지가 조용히 나온다.
    expect(() => parsePgm(pgm("P2\n2 1\n255\n", [1, 2]))).toThrow(/P5/);
  });

  it("데이터가 부족하면 거부한다", () => {
    // 잘린 파일을 그리면 아래쪽이 미탐사처럼 남는다.
    expect(() => parsePgm(pgm("P5\n3 2\n255\n", [1, 2, 3]))).toThrow(/부족/);
  });
});

describe("cellColor", () => {
  // map_saver 는 trinary 모드에서 이 세 값만 쓴다.
  const OCCUPIED = cellColor(0);
  const FREE = cellColor(254);
  const UNKNOWN = cellColor(205);

  it("점유·자유·미탐사를 서로 다른 색으로 나눈다", () => {
    expect(OCCUPIED).not.toEqual(FREE);
    expect(FREE).not.toEqual(UNKNOWN);
    expect(OCCUPIED).not.toEqual(UNKNOWN);
  });

  it("205를 임계값으로 판정하면 free 가 되는 것을 막는다", () => {
    // (255 - 205) / 255 = 0.196 이고 map_saver 가 저장하는 free_thresh 는 0.25 다.
    // 임계값만 쓰면 미탐사 영역 전체가 바닥으로 그려진다.
    const occupancy = (255 - 205) / 255;
    expect(occupancy).toBeLessThan(0.25);
    expect(cellColor(205)).toEqual(UNKNOWN);
    expect(cellColor(205)).not.toEqual(FREE);
  });

  it("아는 세 값 외에는 미탐사로 떨어뜨린다", () => {
    // mode 가 trinary 에서 바뀌어도 없는 값을 벽이나 바닥으로 단정하지 않는다.
    for (const v of [1, 100, 128, 204, 206, 253, 255]) {
      expect(cellColor(v)).toEqual(UNKNOWN);
    }
  });
});

describe("worldToPixel", () => {
  // 실서버 지도와 같은 형태: 0.05 m/셀, origin [-5.15, -9.84], 높이 294
  const meta = { resolution: 0.05, originX: -5.15, originY: -9.84 };
  const height = 294;

  it("origin 은 격자의 아래쪽이다", () => {
    const { col, row } = worldToPixel(meta.originX, meta.originY, meta, height);
    expect(col).toBeCloseTo(0);
    // 아래쪽이므로 마지막 행이다. 0 이 나오면 y 를 뒤집지 않은 것이다.
    expect(row).toBeCloseTo(height);
  });

  it("y 가 커지면 행 번호가 작아진다", () => {
    const low = worldToPixel(0, 0, meta, height);
    const high = worldToPixel(0, 1, meta, height);
    expect(high.row).toBeLessThan(low.row);
    // 1m 이동은 0.05m/셀에서 정확히 20셀이다.
    expect(low.row - high.row).toBeCloseTo(20);
  });

  it("x 가 커지면 열 번호가 커진다", () => {
    const left = worldToPixel(0, 0, meta, height);
    const right = worldToPixel(1, 0, meta, height);
    expect(right.col - left.col).toBeCloseTo(20);
  });

  it("정수로 자르지 않는다", () => {
    // 궤적을 선으로 그릴 때 소수 좌표가 필요하다. 자르면 계단처럼 보인다.
    const { col } = worldToPixel(meta.originX + 0.025, meta.originY, meta, height);
    expect(col).toBeCloseTo(0.5);
  });
});

describe("parseYaml", () => {
  // 실서버 지도의 실제 yaml 이다.
  const yaml = [
    "image: map.pgm",
    "mode: trinary",
    "resolution: 0.05",
    "origin: [-5.15, -9.84, 0]",
    "negate: 0",
    "occupied_thresh: 0.65",
    "free_thresh: 0.25",
  ].join("\n");

  it("resolution 과 origin 의 x·y 를 읽는다", () => {
    expect(parseYaml(yaml)).toEqual({
      resolution: 0.05,
      originX: -5.15,
      originY: -9.84,
    });
  });

  it("origin 의 세 번째 값(yaw)에 속지 않는다", () => {
    const parsed = parseYaml("resolution: 0.05\norigin: [1.5, 2.5, 3.5]\n");
    expect(parsed?.originX).toBe(1.5);
    expect(parsed?.originY).toBe(2.5);
  });

  it("지수 표기도 읽는다", () => {
    const parsed = parseYaml("resolution: 5.0e-2\norigin: [-1.0e1, 2, 0]\n");
    expect(parsed?.resolution).toBeCloseTo(0.05);
    expect(parsed?.originX).toBeCloseTo(-10);
  });

  it("필드가 없으면 null 이다", () => {
    // 조용히 0 을 쓰면 지도가 원점에 붙어 그려지고 궤적만 엉뚱한 곳에 남는다.
    expect(parseYaml("image: map.pgm\n")).toBeNull();
  });
});
