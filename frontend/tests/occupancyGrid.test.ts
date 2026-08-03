/**
 * `/map` CDR 디코더 시험 (S15P11A301-227).
 *
 * **픽스처가 실제로 캡처한 바이트다.** 젯슨의 `foxglove_bridge`에 붙어 `/map`
 * 메시지 하나를 받아 헤더 104바이트를 그대로 옮겼다. 합성 버퍼만으로는 정렬
 * 실수를 못 잡는다 — 내가 만든 버퍼는 내가 이해한 대로 만들어지므로 이해가
 * 틀렸으면 시험도 같이 틀린다.
 *
 * 기대값은 같은 순간 `ros2 topic echo /map --field info`가 출력한 값이다.
 *
 * ```text
 * resolution 0.05000000074505806
 * width 224   height 242
 * origin (-6.630760719071444, -5.717658369106661)
 * data 길이 54208 = 224 × 242
 * ```
 *
 * 데이터 본문은 길이 필드 뒤의 단순 복사라 정렬 위험이 없다. 그래서 픽스처는
 * 헤더만 두고 본문은 시험에서 만든다 — 저장소에 54KB 이진 파일을 넣지 않는다.
 */

import { describe, expect, it } from "vitest";
import {
  CdrError,
  classifyGridCell,
  decodeOccupancyGrid,
} from "@/lib/occupancyGrid";

/**
 * 젯슨에서 캡처한 `/map` CDR 헤더 104바이트.
 *
 * ```text
 * 00010000                    encapsulation (little-endian)
 * e447706a f84a0521           stamp
 * 04000000 6d617000           frame_id 길이 4 + "map\0"
 * 00000000 00000000           map_load_time
 * cdcc4c3d                    resolution 0.05
 * e0000000                    width 224
 * f2000000                    height 242
 * 00000000                    ← 8바이트 정렬 패딩. 이것을 빼먹으면 origin 이 밀린다
 * 9d0d5023e6851ac0            origin.x -6.6307...
 * 230ee4d5e1de16c0            origin.y -5.7176...
 * ...                         z, orientation
 * 0000f03f                    orientation.w = 1.0
 * c0d30000                    data 길이 54208
 * ```
 */
const HEADER_HEX =
  "00010000e447706af84a0521040000006d6170000000000000000000cdcc4c3d" +
  "e0000000f2000000000000009d0d5023e6851ac0230ee4d5e1de16c000000000" +
  "0000000000000000000000000000000000000000000000000000000000000000" +
  "0000f03fc0d30000";

const EXPECTED = {
  frameId: "map",
  resolution: 0.05000000074505806,
  width: 224,
  height: 242,
  originX: -6.630760719071444,
  originY: -5.717658369106661,
  cells: 224 * 242,
};

function hexToBytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i += 1) {
    out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

/** 캡처한 헤더 + 지정한 셀 값으로 완전한 메시지를 만든다. */
function buildMessage(fill: (index: number) => number = () => -1): ArrayBuffer {
  const header = hexToBytes(HEADER_HEX);
  const out = new Uint8Array(header.length + EXPECTED.cells);
  out.set(header, 0);
  const body = new Int8Array(out.buffer, header.length, EXPECTED.cells);
  for (let i = 0; i < EXPECTED.cells; i += 1) body[i] = fill(i);
  return out.buffer;
}

describe("decodeOccupancyGrid — 실제 캡처 헤더", () => {
  it("ros2 topic echo 와 같은 값을 읽는다", () => {
    const grid = decodeOccupancyGrid(buildMessage());
    expect(grid.frameId).toBe(EXPECTED.frameId);
    expect(grid.resolution).toBe(EXPECTED.resolution);
    expect(grid.width).toBe(EXPECTED.width);
    expect(grid.height).toBe(EXPECTED.height);
    expect(grid.data.length).toBe(EXPECTED.cells);
  });

  it("origin 을 소수점까지 정확히 읽는다 — 8바이트 정렬이 맞아야 한다", () => {
    // 정렬을 빼먹으면 origin 이 4바이트 밀려 읽힌다. float64 비트 패턴이 여전히
    // 유효한 수라서 예외가 나지 않고, 지도는 정상으로 그려지며 위치만 틀린다.
    const grid = decodeOccupancyGrid(buildMessage());
    expect(grid.originX).toBe(EXPECTED.originX);
    expect(grid.originY).toBe(EXPECTED.originY);
  });

  it("4바이트 밀린 origin 과 구별된다", () => {
    // 정렬 버그가 생기면 어떤 값이 나오는지 못박아 둔다. 위 기대값과 이 값이
    // 다르다는 것이 시험의 의미다.
    const bytes = hexToBytes(HEADER_HEX);
    const wrong = new DataView(bytes.buffer).getFloat64(40, true); // 44 가 정답
    expect(wrong).not.toBe(EXPECTED.originX);
  });

  it("셀 값을 부호 있는 정수로 읽는다", () => {
    // Uint8Array 로 읽으면 -1 이 255 가 되고, 그러면 미탐색이 점유로 그려진다.
    const grid = decodeOccupancyGrid(buildMessage(i => (i === 0 ? -1 : 100)));
    expect(grid.data[0]).toBe(-1);
    expect(grid.data[1]).toBe(100);
  });
});

describe("decodeOccupancyGrid — 거부해야 하는 입력", () => {
  it("encapsulation 보다 짧으면 거부한다", () => {
    expect(() => decodeOccupancyGrid(new ArrayBuffer(2))).toThrow(CdrError);
  });

  it("데이터가 모자라면 거부한다", () => {
    // 잘린 메시지를 그리면 아래쪽이 미탐색처럼 남는다.
    const full = new Uint8Array(buildMessage());
    expect(() => decodeOccupancyGrid(full.slice(0, full.length - 10).buffer)).toThrow(
      CdrError,
    );
  });

  it("바이트가 남으면 거부한다", () => {
    // 레이아웃 이해가 틀렸다는 신호다. 조용히 넘기면 필드가 추가됐을 때
    // origin 이 밀려 읽히는 것을 못 잡는다.
    const full = new Uint8Array(buildMessage());
    const padded = new Uint8Array(full.length + 8);
    padded.set(full, 0);
    expect(() => decodeOccupancyGrid(padded.buffer)).toThrow(/남았습니다/);
  });

  it("격자 크기와 데이터 길이가 어긋나면 거부한다", () => {
    const full = new Uint8Array(buildMessage());
    // data 길이 필드(파일 오프셋 100)를 1 줄인다. 뒤 바이트는 그대로 두므로
    // "남은 바이트" 로도 걸리지만, 크기 불일치가 먼저 잡혀야 한다.
    new DataView(full.buffer).setUint32(100, EXPECTED.cells - 1, true);
    expect(() => decodeOccupancyGrid(full.buffer)).toThrow(/격자 크기/);
  });
});

describe("classifyGridCell", () => {
  it("nav2 규약대로 나눈다", () => {
    expect(classifyGridCell(-1)).toBe("unknown");
    expect(classifyGridCell(0)).toBe("free");
    expect(classifyGridCell(100)).toBe("occupied");
  });

  it("음수를 확률로 취급하지 않는다", () => {
    // -1 을 임계값에 넣으면 65 미만이라 free 가 된다. 그러면 미탐색 영역 전체가
    // 탐사된 바닥으로 그려지고, 벽은 그대로 맞아서 눈으로 알 수 없다.
    expect(classifyGridCell(-1)).not.toBe("free");
    for (const v of [-1, -2, -128]) {
      expect(classifyGridCell(v)).toBe("unknown");
    }
  });

  it("임계값 경계를 지킨다", () => {
    expect(classifyGridCell(64)).toBe("free");
    expect(classifyGridCell(65)).toBe("occupied");
  });

  it("임계값을 바꿀 수 있다", () => {
    // map_saver 의 occupied_thresh(0.65)와 같은 뜻이다. 설정이 바뀌면 여기도 맞춘다.
    expect(classifyGridCell(50, 50)).toBe("occupied");
    expect(classifyGridCell(49, 50)).toBe("free");
  });

  it("실제 캡처 표본의 세 값을 모두 다루다", () => {
    // -1 44651, 0 8889, 100 668 — 그 셋만 나왔다.
    const seen = new Set([-1, 0, 100].map(v => classifyGridCell(v)));
    expect(seen).toEqual(new Set(["unknown", "free", "occupied"]));
  });
});
