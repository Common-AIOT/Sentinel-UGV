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

  /**
   * 메시지를 끝까지 읽었는지 확인한다. 남은 것이 정렬 패딩이면 정상이다.
   *
   * **정확히 일치를 요구하면 안 된다** (S15P11A301-347). CDR 은 메시지 끝을
   * 4바이트 경계로 정렬하므로 본문 길이가 4의 배수가 아니면 패딩이 붙는다.
   * 관제의 지도가 그것 때문에 통째로 버려졌다 — `64274 소비 / 64276 전체`
   * 에서 남은 2바이트는 오류가 아니라 `64274 % 4 = 2` 의 패딩이었다.
   *
   * 그렇다고 검사를 없애면 안 된다. 필드가 하나 추가됐을 때 그 뒤가 전부 밀려
   * 읽히는 것을 잡아 주는 것이 이 검사이고, 그때는 **틀린 값이 맞는 것처럼
   * 보이므로** 예외보다 나쁘다. 그래서 「패딩만큼만」 허용한다.
   *
   * 패딩은 0 으로 채워지므로 값도 확인한다. 0 이 아닌 바이트가 남았다면
   * 패딩이 아니라 우리가 읽지 않은 필드일 가능성이 있다.
   */
  expectFullyConsumed(): void {
    const remaining = this.view.byteLength - this.offset;
    if (remaining === 0) {
      return;
    }
    if (remaining >= 4) {
      throw new CdrError(
        `바이트가 남았습니다: ${this.offset} 소비 / ${this.view.byteLength} 전체 ` +
          `(${remaining}바이트 남음 — 정렬 패딩은 4바이트 미만이므로 ` +
          `레이아웃 이해가 틀렸을 수 있습니다)`,
      );
    }
    for (let i = this.offset; i < this.view.byteLength; i += 1) {
      if (this.view.getUint8(i) !== 0) {
        throw new CdrError(
          `정렬 패딩이어야 할 ${remaining}바이트에 0 이 아닌 값이 있습니다 ` +
            `(offset ${i}) — 읽지 않은 필드가 있을 수 있습니다`,
        );
      }
    }
  }
}
