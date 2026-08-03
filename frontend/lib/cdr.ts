/**
 * CDR(Common Data Representation) 읽기 (S15P11A301-227).
 *
 * `foxglove_bridge`가 ROS 메시지를 `encoding=cdr`로 보낸다. 두 곳에서 쓴다 —
 * `/map`의 `OccupancyGrid`와 `/pose`의 `PoseWithCovarianceStamped`. 정렬 계산을
 * 두 벌 두면 한쪽만 틀리므로 여기 한 곳에 둔다.
 *
 * ## 정렬을 호출부에서 세지 않는다
 *
 * CDR은 각 필드를 **자기 크기에 맞춰** 정렬하고, 기준은 파일 처음이 아니라
 * encapsulation 4바이트를 뺀 **본문 시작**이다. 그 둘을 혼동하면 오프셋이 4씩
 * 어긋난다.
 *
 * 각 `read*`가 스스로 정렬하므로 호출부는 필드를 순서대로 읽기만 한다. 패딩을
 * 손으로 세는 순간 틀린다 — `OccupancyGrid`는 width·height 뒤에 4바이트 패딩이
 * 있고 `PoseWithCovarianceStamped`는 같은 자리에 없다. 실측으로 확인했다.
 *
 * 빼먹으면 `float64`가 4바이트 밀려 읽히는데 비트 패턴이 여전히 유효한 수라서
 * **예외가 나지 않는다.** 값만 조용히 틀린다.
 */

/** 값이 하나뿐인 예외. 호출자가 메시지로 구분할 필요가 없다. */
export class CdrError extends Error {}

/**
 * CDR 바이트를 읽는 커서.
 *
 * 정렬 계산을 한 곳에 모은다. 각 `read*`가 스스로 정렬하므로 호출부에서
 * 패딩을 세지 않는다 — 세는 순간 틀린다.
 */
export class CdrReader {
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
