"use client";

import { ShieldAlert } from "lucide-react";
import { useRobot } from "@/features/robot/RobotContext";
import type { MissionState } from "@/features/robot/mockData";

/**
 * 임무 상태 패널.
 *
 * 표시 기준은 명세 26.2의 임무 상태 12개와 common/schemas/state.schema.json이다.
 *
 * **임무 상태 표시와 명령 버튼은 하단 명령 바로 옮겼다 (S15P11A301-303).**
 * 여기 남은 것은 연결 상태와, 명령과 무관한 경고뿐이다. 어휘(라벨·톤·단계 목록)는
 * 이 파일이 계속 소유하고 CommandBar 가 가져다 쓴다 — 두 곳이 갈라지면 같은 상태가
 * 화면에서 다른 이름으로 보인다.
 */

export const MISSION_LABEL: Record<MissionState, string> = {
  SAFE_IDLE: "대기",
  EXPLORING: "탐사 중",
  PERSON_APPROACHING: "접근 중",
  INTERACTING: "음성 확인",
  POST_RECORDING: "사후 녹화",
  REPORTING: "보고 중",
  PAUSED: "일시정지",
  MANUAL: "수동 조종",
  RETURNING: "복귀 중",
  // "임무 완료"가 아니라 "대기"다 (S15P11A301-274). 종료는 알람으로 이미 알렸고
  // 완료 이력은 관제 DB(missions.status·ended_at)에 남는다. 상태창은 "지금 로봇이
  // 어떤가"를 보여주는 자리이므로, 임무를 끝낸 로봇의 현재 조건은 대기다.
  // SAFE_IDLE 과 같은 표시가 되는 것이 맞다 — 조작자에게 둘은 같은 상황이고
  // (다음 임무를 시작할 수 있다) 구별은 내부 사정이다.
  COMPLETED: "대기",
  ESTOP: "비상 정지",
  ERROR: "오류",
};

/** 관제자가 개입해야 하는 단계는 앰버, 정상 진행은 초록, 위험은 레드로 나눈다. */
export const MISSION_TONE: Record<MissionState, string> = {
  SAFE_IDLE: "text-muted-foreground border-border bg-muted",
  EXPLORING: "text-primary border-primary/30 bg-primary/10",
  PERSON_APPROACHING: "text-accent border-accent/30 bg-accent/10",
  INTERACTING: "text-accent border-accent/30 bg-accent/10",
  POST_RECORDING: "text-accent border-accent/30 bg-accent/10",
  REPORTING: "text-accent border-accent/30 bg-accent/10",
  PAUSED: "text-accent border-accent/40 bg-accent/10",
  MANUAL: "text-info border-info/30 bg-info/10",
  RETURNING: "text-accent border-accent/30 bg-accent/10",
  // 표시가 "대기"이므로 톤도 SAFE_IDLE 과 같이 중립이다. 종전에는 초록(정상
  // 진행)이라 대기 중인데 활동 중처럼 보였다 (S15P11A301-274).
  COMPLETED: "text-muted-foreground border-border bg-muted",
  ESTOP: "text-destructive border-destructive/40 bg-destructive/15",
  ERROR: "text-destructive border-destructive/40 bg-destructive/15",
};

/**
 * 글자 전용 상태 톤 (S15P11A301-303). 하단 명령 바가 쓴다.
 *
 * MISSION_TONE 은 테두리·배경까지 포함하는데, 명령 버튼 옆에서는 상태가 버튼처럼
 * 보인다. 색 의미는 같게 두고 글자색만 뽑는다 — 두 곳이 다른 색으로 같은 상태를
 * 말하면 안 되므로 이 파일이 함께 소유한다.
 */
export const MISSION_TEXT_TONE: Record<MissionState, string> = {
  SAFE_IDLE: "text-muted-foreground",
  EXPLORING: "text-primary",
  PERSON_APPROACHING: "text-accent",
  INTERACTING: "text-accent",
  POST_RECORDING: "text-accent",
  REPORTING: "text-accent",
  PAUSED: "text-accent",
  MANUAL: "text-info",
  RETURNING: "text-accent",
  COMPLETED: "text-muted-foreground",
  ESTOP: "text-destructive",
  ERROR: "text-destructive",
};

/** 사람을 만난 뒤의 단계들. 이 동안에는 탐사가 멈춰 있다(26.2). */
export const ENCOUNTER_PHASES: MissionState[] = [
  "PERSON_APPROACHING",
  "INTERACTING",
  "POST_RECORDING",
  "REPORTING",
];

export default function StatusPanel() {
  const { status } = useRobot();
  const { missionState } = status;

  const danger = missionState === "ESTOP" || missionState === "ERROR";

  // 서버 연결 표시는 상단 바로 몰았다 (S15P11A301-303). 같은 값(wsConnected)을
  // 두 곳에서 그리고 있었다 — 상단 바 시계 옆의 점과 여기가 하나의 상태였다.
  // 평상시 알릴 것이 없으면 이 패널은 자리를 차지하지 않는다.
  if (!danger) return null;

  return (
    <div className="p-3.5 border-b border-border">
      {/* 비상 정지·오류는 다른 모든 것보다 위에 알린다. 배지 색만 바꾸면
          평상시와 구분되지 않는다. 상세 안내는 하단 명령 바가 함께 낸다. */}
      <div className="flex items-center gap-2 rounded border border-destructive/50 bg-destructive/15 px-2.5 py-2">
        <ShieldAlert size={16} className="text-destructive flex-shrink-0" />
        <p className="text-sm font-semibold text-destructive leading-tight">
          {MISSION_LABEL[missionState]}
        </p>
      </div>
    </div>
  );
}
