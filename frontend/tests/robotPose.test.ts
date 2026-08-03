/**
 * `/pose` CDR 디코더 시험 (S15P11A301-227).
 *
 * 픽스처는 **돌고 있는 bridge 에서 그대로 받은 364바이트**다. 손으로 만든 것이
 * 아니므로 레이아웃 이해가 틀렸다면 여기서 걸린다. `ros2 topic echo`가 같은
 * 순간에 보고한 값과 대조해 소수점 17자리까지 맞는 것을 확인했다.
 *
 * ```text
 * frame_id     map
 * position     -0.16833333333328968, -0.021666666666634336
 * orientation  z=-0.005234976088965129  w=0.9999862974187936
 * yaw          -0.010469999999982355 rad  (-0.5999°)
 * ```
 *
 * 이 디코더가 틀리면 **지도는 정상인데 로봇 화살표만 엉뚱한 곳을 본다.** 화면에서
 * 알아채기 어려운 종류라 값으로 못박는다.
 */

import { describe, expect, it } from "vitest";
import { decodeRobotPose } from "@/lib/robotPose";
import { CdrError } from "@/lib/cdr";

/** 실측 캡처 원본. 자리를 옮기거나 줄이지 말 것 — 값 시험의 근거가 사라진다. */
const POSE_HEX =
  "000100003f4d706ae0c7d536040000006d617000671fbf58f28bc5bfc971fc62" +
  "c92f96bf000000000000000000000000000000000000000000000000d0248f31" +
  "457175bf42947b43e3ffef3fa651a2fda7954d3fc309bca68eab2e3f00000000" +
  "00000000000000000000000000000000000000000000000000000000c309bca6" +
  "8eab2e3f59929d428afc4f3f0000000000000000000000000000000000000000" +
  "0000000000000000000000000000000000000000000000000000000000000000" +
  "0000000000000000000000000000000000000000000000000000000000000000" +
  "0000000000000000000000000000000000000000000000000000000000000000" +
  "0000000000000000000000000000000000000000000000000000000000000000" +
  "0000000000000000000000000000000000000000000000000000000000000000" +
  "0000000000000000000000000000000000000000000000000000000000000000" +
  "000000001c6feb1524df1f3f";

function hexToBuffer(hex: string): ArrayBuffer {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < bytes.length; i += 1) {
    bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return bytes.buffer;
}

describe("decodeRobotPose — 실측 표본", () => {
  const pose = decodeRobotPose(hexToBuffer(POSE_HEX));

  it("픽스처가 364바이트다", () => {
    // 길이가 바뀌었다면 픽스처를 잘못 편집한 것이다. 아래 값 시험이 전부
    // 무의미해지므로 먼저 확인한다.
    expect(POSE_HEX.length / 2).toBe(364);
  });

  it("frame_id 는 map 이다", () => {
    // odom 이면 SLAM 이 아니라 정적 TF 를 보고 있다는 뜻이다.
    expect(pose.frameId).toBe("map");
  });

  it("위치를 echo 값과 똑같이 읽는다", () => {
    expect(pose.x).toBe(-0.16833333333328968);
    expect(pose.y).toBe(-0.021666666666634336);
  });

  it("yaw 를 쿼터니언에서 뽑는다", () => {
    expect(pose.yaw).toBeCloseTo(-0.010469999999982355, 15);
  });

  it("공분산 대각에서 표준편차를 낸다", () => {
    // covariance 를 시퀀스로 읽으면(고정 배열인데 길이 접두가 있다고 착각) 값이
    // 8바이트 밀려 여기가 깨진다.
    expect(pose.stdDevX).toBeCloseTo(0.030047465393285858, 15);
    expect(pose.stdDevY).toBeCloseTo(0.031243400027403549, 15);
    expect(pose.stdDevYaw).toBeCloseTo(0.011026362711263879, 15);
  });

  it("바이트를 정확히 소진한다", () => {
    // 남으면 던지므로 위에서 이미 통과한 것이지만, 의도를 남긴다. 이 검사가
    // covariance 길이(36)를 틀리게 세는 실수를 잡는 유일한 장치다.
    expect(() => decodeRobotPose(hexToBuffer(POSE_HEX))).not.toThrow();
  });
});

describe("decodeRobotPose — 잘못된 입력", () => {
  it("잘린 메시지를 거부한다", () => {
    const short = new Uint8Array(hexToBuffer(POSE_HEX)).slice(0, 200).buffer;
    expect(() => decodeRobotPose(short)).toThrow(CdrError);
  });

  it("뒤에 바이트가 붙으면 거부한다", () => {
    // 메시지 타입이 바뀌었는데 눈치채지 못하는 경우다. 조용히 넘기면 필드가
    // 밀려 읽힌다.
    const bytes = new Uint8Array(hexToBuffer(POSE_HEX));
    const padded = new Uint8Array(bytes.length + 8);
    padded.set(bytes, 0);
    expect(() => decodeRobotPose(padded.buffer)).toThrow(/바이트가 남았습니다/);
  });

  it("빈 버퍼를 거부한다", () => {
    expect(() => decodeRobotPose(new ArrayBuffer(0))).toThrow(CdrError);
  });
});

describe("decodeRobotPose — yaw 부호", () => {
  /** 픽스처의 orientation 만 갈아끼운다. 나머지 바이트는 실측 그대로다. */
  function withYaw(yaw: number): ArrayBuffer {
    const buffer = hexToBuffer(POSE_HEX);
    const view = new DataView(buffer);
    // 본문 기준 40 = 파일 기준 44. position(16..40) 바로 뒤다.
    view.setFloat64(44, 0, true); // x
    view.setFloat64(52, 0, true); // y
    view.setFloat64(60, Math.sin(yaw / 2), true); // z
    view.setFloat64(68, Math.cos(yaw / 2), true); // w
    return buffer;
  }

  it("반시계가 양수다 (REP-103)", () => {
    // 부호가 뒤집히면 화살표가 거울처럼 돈다. 로봇이 제자리에서 돌 때만 눈에
    // 보이는 오류다.
    expect(decodeRobotPose(withYaw(Math.PI / 2)).yaw).toBeCloseTo(Math.PI / 2, 12);
    expect(decodeRobotPose(withYaw(-Math.PI / 2)).yaw).toBeCloseTo(-Math.PI / 2, 12);
  });

  it("정면은 0 이다", () => {
    expect(decodeRobotPose(withYaw(0)).yaw).toBeCloseTo(0, 12);
  });

  it("±π 근처에서 감긴다", () => {
    // atan2 이므로 (-π, π] 로 접힌다. 화살표를 그리는 쪽은 이 범위만 받는다.
    const wrapped = decodeRobotPose(withYaw(Math.PI * 0.99)).yaw;
    expect(Math.abs(wrapped)).toBeLessThanOrEqual(Math.PI);
    expect(wrapped).toBeCloseTo(Math.PI * 0.99, 12);
  });
});
