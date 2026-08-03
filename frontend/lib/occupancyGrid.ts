/**
 * `nav_msgs/OccupancyGrid` CDR 디코더 (S15P11A301-227).
 *
 * `foxglove_bridge`가 `/map`을 `encoding=cdr`로 보낸다. 브라우저가 직접 풀어야
 * 한다 — 이 파일에 DOM이 없으므로 시험할 수 있고, 여기서 틀리면 **화면에는
 * 그럴싸한 지도가 그려지는데 값이 틀린다.**
 *
 * ## 레이아웃은 실측으로 확정했다
 *
 * 실제 메시지 54312바이트를 캡처해 `ros2 topic echo`의 값과 대조했다. 소수점
 * 15자리까지 맞았고 바이트가 정확히 소진됐다.
 *
 * ```text
 * 0    encapsulation 00 01 00 00   little-endian CDR
 * --- 이후 오프셋은 본문(4) 기준 ---
 * 0    stamp.sec int32,  stamp.nanosec uint32
 * 8    frame_id  uint32 길이 + 바이트(널 포함)
 * 16   map_load_time  int32 + uint32
 * 24   resolution float32
 * 28   width uint32,  height uint32
 * 36   → 40  8바이트 정렬 패딩
 * 40   origin.position     3 × float64
 * 64   origin.orientation  4 × float64
 * 96   data  uint32 길이 + int8 × (width × height)
 * ```
 *
 * ## 8바이트 정렬을 빼먹으면 조용히 틀린다
 *
 * 36에서 40으로 미는 패딩이 그것이다. 빼먹으면 `origin`이 4바이트 밀려 읽히는데,
 * float64 비트 패턴이 여전히 유효한 수라서 예외가 나지 않는다. 지도는 정상으로
 * 그려지고 로봇 위치만 엉뚱한 곳에 찍힌다.
 *
 * CDR은 각 필드를 **자기 크기에 맞춰** 정렬하며, 기준은 파일 처음이 아니라
 * encapsulation 4바이트를 뺀 본문 시작이다. 그 둘을 혼동하면 오프셋이 4씩
 * 어긋난다.
 */

/** 좌표계. `worldToPixel`이 요구하는 형태와 같다. */
export interface GridGeometry {
  resolution: number;
  originX: number;
  originY: number;
}

export interface OccupancyGrid extends GridGeometry {
  frameId: string;
  width: number;
  height: number;
  /** 행 우선, **첫 행이 아래**(nav2 규약). 길이는 width × height. */
  data: Int8Array;
}

/** 값이 하나뿐인 예외. 호출자가 메시지로 구분할 필요가 없다. */
export class CdrError extends Error {}

/**
 * CDR 바이트를 읽는 커서.
 *
 * 정렬 계산을 한 곳에 모은다. 각 `read*`가 스스로 정렬하므로 호출부에서
 * 패딩을 세지 않는다 — 세는 순간 틀린다.
 */
class CdrReader {
  private offset: number;
  private readonly view: DataView;
  private readonly bodyStart: number;
  private readonly little: boolean;

  constructor(buffer: ArrayBuffer) {
    if (buffer.byteLength < 4) {
      throw new CdrError("CDR 이 encapsulation 헤더보다 짧습니다");
    }
    this.view = new DataView(buffer);
    // 두 번째 바이트가 엔디안이다. 0x01 = little, 0x00 = big.
    // ROS 2 는 사실상 항상 little 이지만, big 을 little 로 읽으면 숫자가 전부
    // 말이 되지 않는 값이 되므로 여기서 갈라 두는 편이 낫다.
    this.little = this.view.getUint8(1) === 1;
    this.bodyStart = 4;
    this.offset = 4;
  }

  /** 본문 시작 기준으로 `size` 배수에 맞춘다. */
  private align(size: number): void {
    const rel = this.offset - this.bodyStart;
    this.offset += (size - (rel % size)) % size;
  }

  private need(bytes: number): void {
    if (this.offset + bytes > this.view.byteLength) {
      throw new CdrError(
        `CDR 이 중간에 끊겼습니다: ${this.offset + bytes}바이트 필요, ` +
          `${this.view.byteLength}바이트 있음`,
      );
    }
  }

  int32(): number {
    this.align(4);
    this.need(4);
    const value = this.view.getInt32(this.offset, this.little);
    this.offset += 4;
    return value;
  }

  uint32(): number {
    this.align(4);
    this.need(4);
    const value = this.view.getUint32(this.offset, this.little);
    this.offset += 4;
    return value;
  }

  float32(): number {
    this.align(4);
    this.need(4);
    const value = this.view.getFloat32(this.offset, this.little);
    this.offset += 4;
    return value;
  }

  float64(): number {
    this.align(8);
    this.need(8);
    const value = this.view.getFloat64(this.offset, this.little);
    this.offset += 8;
    return value;
  }

  /** 길이 접두 문자열. 길이에 널 종단이 포함된다. */
  string(): string {
    const length = this.uint32();
    this.need(length);
    const bytes = new Uint8Array(this.view.buffer, this.offset, Math.max(0, length - 1));
    this.offset += length;
    return new TextDecoder().decode(bytes);
  }

  /** 길이 접두 int8 시퀀스. 복사하지 않고 뷰를 돌려준다. */
  int8Sequence(): Int8Array {
    const length = this.uint32();
    this.need(length);
    const seq = new Int8Array(this.view.buffer, this.offset, length);
    this.offset += length;
    return seq;
  }

  /** 지금까지 읽은 바이트 수. 남는 바이트 검사에 쓴다. */
  get consumed(): number {
    return this.offset;
  }
}

/**
 * `/map` 페이로드를 푼다.
 *
 * `width × height`와 `data` 길이가 다르면 거부한다. 그 상태로 그리면 아래쪽이
 * 잘리거나 옆줄로 밀려 보이는데, 원인이 화면에서 드러나지 않는다.
 */
export function decodeOccupancyGrid(buffer: ArrayBuffer): OccupancyGrid {
  const reader = new CdrReader(buffer);

  reader.int32(); // stamp.sec — 화면에서 쓰지 않는다
  reader.uint32(); // stamp.nanosec
  const frameId = reader.string();

  reader.int32(); // map_load_time.sec
  reader.uint32(); // map_load_time.nanosec
  const resolution = reader.float32();
  const width = reader.uint32();
  const height = reader.uint32();

  const originX = reader.float64();
  const originY = reader.float64();
  reader.float64(); // position.z — 2D 지도라 쓰지 않는다
  reader.float64(); // orientation.x
  reader.float64(); // orientation.y
  reader.float64(); // orientation.z
  reader.float64(); // orientation.w — slam_toolbox 는 회전 격자를 만들지 않는다

  const data = reader.int8Sequence();

  if (!(resolution > 0)) {
    throw new CdrError(`resolution 이 올바르지 않습니다: ${resolution}`);
  }
  if (data.length !== width * height) {
    throw new CdrError(
      `격자 크기가 맞지 않습니다: ${width}×${height}=${width * height} 인데 ` +
        `데이터가 ${data.length}바이트입니다`,
    );
  }
  if (reader.consumed !== buffer.byteLength) {
    // 남거나 모자라면 레이아웃 이해가 틀린 것이다. 조용히 넘기면 다음 필드가
    // 추가됐을 때 origin 이 밀려 읽히는 것을 못 잡는다.
    throw new CdrError(
      `바이트가 남았습니다: ${reader.consumed} 소비 / ${buffer.byteLength} 전체`,
    );
  }

  return { frameId, resolution, originX, originY, width, height, data };
}

export type GridCell = "occupied" | "free" | "unknown";

/**
 * `int8` 셀 값을 세 종류로 나눈다.
 *
 * **PGM 과 값 체계가 다르다.** 저장된 지도(S15P11A301-203)는 0=점유, 254=자유,
 * 205=미탐색인 바이트를 읽는다. 라이브 격자는 nav2 규약대로다.
 *
 * ```text
 * -1        미탐색
 * 0 ~ 100   점유 확률(%)
 * ```
 *
 * 캡처한 실제 표본의 분포가 그것을 확인해 준다 — `-1` 44651, `0` 8889,
 * `100` 668 셋뿐이었다.
 *
 * **음수를 먼저 걸러야 한다.** `-1`을 확률로 취급하면 임계값 아래라 자유로
 * 분류되고, 그러면 **미탐색 영역 전체가 탐사된 바닥으로 그려진다.** 벽은 그대로
 * 맞으므로 눈으로 알 수 없다. PGM 205 에서 겪은 것과 같은 부류다.
 */
export function classifyGridCell(value: number, occupiedThreshold = 65): GridCell {
  if (value < 0) return "unknown";
  return value >= occupiedThreshold ? "occupied" : "free";
}
