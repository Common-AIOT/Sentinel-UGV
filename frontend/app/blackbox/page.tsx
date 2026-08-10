"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  ChevronLeft, Play, Archive, User, MapPin, Clock, Volume2, VolumeX, VideoOff, HelpCircle,
} from "lucide-react";
import {
  api,
  type EncounterDetail,
  type EncounterSummary,
  type MissionSummary,
  type TelemetryPoint,
} from "@/lib/api";
import TelemetryChart from "@/features/telemetry/TelemetryChart";
import { sanitizeEnvironmentPoint } from "@/features/telemetry/environmentThresholds";
import {
  classifyMissingMedia,
  mediaStateLabel,
  summarizeMissionRecorder,
  type MissingMediaVerdict,
} from "@/features/telemetry/recorderStatus";
import MissionMap from "@/features/mapping/MissionMap";
import {
  createStompClient,
  missionEncountersTopic,
  type EncounterChangedMessage,
} from "@/lib/realtime";

/**
 * 임무 이력 화면. 운영 API 실데이터로 임무 목록 → 발견(encounter) 목록 → 이벤트
 * 영상 재생까지 이어진다 (S15P11A301-169).
 *
 * durationSec·distanceM·detectionCount 는 임무가 끝난 뒤 서버가 집계한다(#166).
 * 진행 중이거나 집계 이전 임무는 null 이므로 "—" 로 보여준다.
 *
 * 레이아웃은 관제(/)와 같은 "스크롤 없는 한 화면" 대시보드다 (S15P11A301-273).
 * 예전에는 세로 스크롤 문서였는데 — 요약 카드가 옆 차트 높이에 맞춰 빈 상자로
 * 늘어나고, 이 화면의 주인공인 발견 목록이 지도 아래 화면 밖에 있었고, 발견을
 * 클릭하면 영상 패널이 위에서 끼어들며 전체가 밀렸다. 지금은 가운데가 지도,
 * 오른쪽 사이드바가 환경 차트·발견 목록, 영상은 지도 위 오버레이로 뜬다.
 */

/** 잡음 제거 오디오 kind (#228 계약). 원본 오디오의 파생물 — 원본이 증거다. */
const DENOISED_AUDIO_TYPE = "EVENT_AUDIO_DENOISED";

// 음성 보고 한국어 표기(S15P11A301-242)는 요약 줄과 함께 뺐다 (S15P11A301-365).
// RISK_LABEL·MOBILITY_LABEL·URGENT_LABEL 과 humanizeInteractionSummary 가 그 줄
// 하나만 위해 있었다. 요약을 다시 화면에 올릴 때 git 이력에서 되살린다.
//
// 발견 종료 사유. PERSON_LOST 는 서버(EncounterWriter)가, 나머지는 음성 세션 보고
// (interaction-report.schema.json 의 terminationReason enum)가 넣는다.
// SESSION_COMPLETE·NO_RESPONSE 는 예전 형식의 기존 행 호환용으로 남긴다.
const TERMINATION_LABEL: Record<string, string> = {
  PERSON_LOST: "대상 놓침",
  NORMAL: "대화 완료",
  TIMEOUT: "대화 시간 초과",
  ABORTED_MANUAL: "운영자 중단",
  ABORTED_SAFETY: "안전 사유 중단",
  AUDIO_DEVICE_ERROR: "오디오 장치 오류",
  GMS_UNAVAILABLE: "음성 분석 불가",
  UNKNOWN: "사유 미상",
  SESSION_COMPLETE: "대화 완료",
  NO_RESPONSE: "무응답 종료",
};
/** 임무 종료 사유 — 현재 서버가 쓰는 값은 OPERATOR_STOP 뿐(CommandAckWriter). */
const END_REASON_LABEL: Record<string, string> = { OPERATOR_STOP: "운영자 종료" };

const MISSION_STATUS_LABEL: Record<string, string> = {
  CREATED: "대기",
  EXPLORING: "탐사 중",
  PAUSED: "일시정지",
  RETURNING: "복귀 중",
  COMPLETED: "완료",
};

const ENCOUNTER_STATUS_LABEL: Record<string, string> = {
  CONFIRMED: "발견 확정",
  APPROACHED: "접근 완료",
  ENDED: "상호작용 종료",
  REDETECTED: "재감지",
  LOST: "추적 종료",
};

function missionTone(status: string) {
  if (status === "COMPLETED") return "text-primary border-primary/30 bg-primary/10";
  if (status === "EXPLORING" || status === "RETURNING")
    return "text-accent border-accent/30 bg-accent/10";
  return "text-muted-foreground border-border bg-muted";
}

function fmtDuration(sec: number | null) {
  if (sec === null) return "—";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? `${m}분 ${s}초` : `${s}초`;
}

function fmtDistance(m: number | null) {
  return m === null ? "—" : `${m.toFixed(1)} m`;
}

function fmtTime(iso: string | null) {
  return iso ? new Date(iso).toLocaleTimeString("ko-KR", { hour12: false }) : "—";
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
  });
}

export default function MissionHistoryPage() {
  const [missions, setMissions] = useState<MissionSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<MissionSummary | null>(null);
  const [encounters, setEncounters] = useState<EncounterSummary[]>([]);
  const [encountersLoading, setEncountersLoading] = useState(false);
  const [detail, setDetail] = useState<EncounterDetail | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [videoError, setVideoError] = useState<string | null>(null);
  const [telemetry, setTelemetry] = useState<TelemetryPoint[]>([]);

  // ── 음성 보고 실시간 갱신 (S15P11A301-242) ────────────────────────────────
  // 음성 세션은 발견 확정 뒤에 끝나므로, 열려 있는 상세는 #243 신호를 받아
  // 다시 읽어야 보고가 반영된다. 신호에는 내용이 없다 — 상세 재조회가 계약이다.
  const detailIdRef = useRef<string | null>(null);

  // ── 소음 제거본 토글 — 상태 (S15P11A301-229, 실험판 이관) ────────────────
  const [denoisedUrl, setDenoisedUrl] = useState<string | null>(null);
  const [denoised, setDenoised] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    api
      .missions()
      .then(list => {
        setMissions(list);
        setLoadError(null);
        if (list.length > 0) setSelected(prev => prev ?? list[0]);
      })
      .catch(e => setLoadError(e.message));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setEncountersLoading(true);
    setDetail(null);
    setVideoUrl(null);
    setVideoError(null);
    api
      .missionEncounters(selected.id)
      .then(setEncounters)
      .catch(() => setEncounters([]))
      .finally(() => setEncountersLoading(false));
  }, [selected]);

  // telemetry 그래프 — 끝난 임무는 1회 조회, 진행 중 임무는 5초 폴링(실시간).
  useEffect(() => {
    if (!selected) return;
    setTelemetry([]);
    const load = () =>
      api.missionTelemetry(selected.id, 10).then(setTelemetry).catch(() => setTelemetry([]));
    load();
    if (selected.endedAt !== null) return;
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [selected]);

  // 음성 보고 신호 구독(S15P11A301-242) — 열려 있는 상세의 보고가 갱신되면 재조회.
  useEffect(() => {
    if (!selected) return;
    const client = createStompClient();
    client.onConnect = () => {
      client.subscribe(missionEncountersTopic(selected.id), msg => {
        const ev = JSON.parse(msg.body) as EncounterChangedMessage;
        if (ev.phase === "INTERACTION_REPORTED" && ev.encounterId === detailIdRef.current) {
          api.encounterDetail(ev.encounterId).then(setDetail).catch(() => {
            // 재조회 실패는 화면을 깨지 않는다 — 기존 표시를 유지한다.
          });
        }
      });
    };
    client.activate();
    return () => { void client.deactivate(); };
  }, [selected]);

  useEffect(() => { detailIdRef.current = detail?.id ?? null; }, [detail]);

  /** 발견 선택 → 상세(연결 미디어 포함) 조회 → AVAILABLE 영상이면 재생 링크 발급. */
  const openEncounter = useCallback(async (enc: EncounterSummary) => {
    setVideoUrl(null);
    setVideoError(null);
    // ── 소음 제거본 토글 — 발견을 바꾸면 초기화한다 ──────────────
    setDenoisedUrl(null);
    setDenoised(false);
    try {
      const d = await api.encounterDetail(enc.id);
      setDetail(d);
      const video = d.media.find(
        m => m.type === "EVENT_VIDEO" && m.storageStatus === "AVAILABLE",
      );
      if (!video) {
        // 영상 행이 아예 없는 발견(=미디어 없는 발견)은 여기서 아무 말도 하지
        // 않는다. 이유가 텔레메트리에 있고 그것은 폴링으로 늦게 오므로, 판정은
        // 렌더 시점의 missingMedia 가 한다 (S15P11A301-311).
        const videos = d.media.filter(m => m.type === "EVENT_VIDEO");
        if (videos.length === 0) return;
        // 업로드 중(PENDING)과 유실(FAILED)을 구분한다 — 서버가 오래된 PENDING 을
        // 실물 대조로 FAILED 판정하게 되면서(13.6) 화면도 다르게 말해야 한다.
        // FAILED 도 젯슨이 다시 올리면 복구되므로 단정하지 않는다.
        setVideoError(
          videos.some(m => m.storageStatus === "FAILED")
            ? "영상이 유실됐습니다 — 업로드 확인 실패"
            : "영상 업로드 중입니다 — 잠시 후 다시 열어보세요",
        );
        return;
      }
      const { url } = await api.mediaViewUrl(video.mediaId);
      setVideoUrl(url);

      // ── 소음 제거본 토글 — 있으면 링크를 받아 둔다 ────────────
      // 없으면 토글 자체를 그리지 않는다. 워커 배포 전에도 화면이 깨지지 않는다.
      const denoisedAsset = d.media.find(
        m => m.type === DENOISED_AUDIO_TYPE && m.storageStatus === "AVAILABLE",
      );
      if (denoisedAsset) {
        try {
          const audio = await api.mediaViewUrl(denoisedAsset.mediaId);
          setDenoisedUrl(audio.url);
        } catch {
          // 제거본 실패는 영상 재생을 막지 않는다. 원본이 증거고 제거본은 보조다.
          setDenoisedUrl(null);
        }
      }
    } catch (e) {
      setVideoError(e instanceof Error ? e.message : "영상을 불러오지 못했습니다");
    }
  }, []);

  // ── 소음 제거본 토글 — 영상과 오디오 동기 ──────────────────────
  // 제거본은 원본 오디오의 파생물이라 길이가 같다. 아래는 재생 상태를 맞추는 것뿐이다.
  useEffect(() => {
    const video = videoRef.current;
    const audio = audioRef.current;
    if (!video || !audio) return;

    video.muted = denoised;
    if (denoised) {
      audio.currentTime = video.currentTime;
      audio.playbackRate = video.playbackRate;
      if (!video.paused) void audio.play();
    } else {
      audio.pause();
    }

    const onPlay = () => {
      if (!denoised) return;
      audio.currentTime = video.currentTime;
      void audio.play();
    };
    const onPause = () => audio.pause();
    const onSeeked = () => {
      if (denoised) audio.currentTime = video.currentTime;
    };
    const onRate = () => {
      audio.playbackRate = video.playbackRate;
    };

    video.addEventListener("play", onPlay);
    video.addEventListener("pause", onPause);
    video.addEventListener("seeked", onSeeked);
    video.addEventListener("ratechange", onRate);

    // 드리프트 보정 — 0.15초 이상 벌어지면 맞춘다. 긴 영상에서 누적을 막는다.
    const timer = setInterval(() => {
      if (denoised && !video.paused && Math.abs(audio.currentTime - video.currentTime) > 0.15) {
        audio.currentTime = video.currentTime;
      }
    }, 500);

    return () => {
      video.removeEventListener("play", onPlay);
      video.removeEventListener("pause", onPause);
      video.removeEventListener("seeked", onSeeked);
      video.removeEventListener("ratechange", onRate);
      clearInterval(timer);
    };
  }, [denoised, videoUrl, denoisedUrl]);

  // 온습도는 센서 미측정 0값·범위 밖 값을 결측으로 바꿔 그린다 (S15P11A301-280).
  // 부팅 0값 한 점이 세로축을 0까지 늘려 실제 1°C 변화가 안 보이게 눌렸다.
  const envTelemetry = useMemo(
    () => telemetry.map(p => ({ ...p, ...sanitizeEnvironmentPoint(p.temperature, p.humidity) })),
    [telemetry],
  );

  // ── 영상 없는 발견의 사유 (S15P11A301-311) ────────────────────────────────
  // 열려 있는 발견에 영상 행이 하나도 없을 때만 판정한다. 행이 있으면 업로드
  // 상태(PENDING/FAILED)가 이미 답이므로 videoError 가 말한다.
  //
  // 판정을 openEncounter 안에서 하지 않는 이유: 사유는 encounter 가 아니라 임무
  // 텔레메트리에서 오고 그것은 진행 중 임무에서 5초 폴링으로 늦게 온다. 렌더에서
  // 파생시키면 발견을 다시 열지 않아도 「근거 없음」이 사유로 바뀐다.
  const missingMedia = useMemo<MissingMediaVerdict | null>(() => {
    if (!detail || videoUrl) return null;
    if (detail.media.some(m => m.type === "EVENT_VIDEO")) return null;
    // 다음 발견의 마감 결과가 이 발견에 붙지 않게 창을 그 앞까지로 좁힌다.
    const opened = new Date(detail.startedAt).getTime();
    const nextStart = encounters.reduce<string | null>((earliest, e) => {
      const t = new Date(e.startedAt).getTime();
      if (t <= opened) return earliest;
      return earliest === null || t < new Date(earliest).getTime() ? e.startedAt : earliest;
    }, null);
    return classifyMissingMedia(detail, telemetry, nextStart);
  }, [detail, videoUrl, telemetry, encounters]);

  /** 임무 하나의 녹화기 상태 — 발견을 열지 않아도 마감 실패가 보여야 한다. */
  const missionRecorder = useMemo(() => summarizeMissionRecorder(telemetry), [telemetry]);

  /** 영상 오버레이 닫기 — 지도로 복귀. */
  const closeOverlay = useCallback(() => {
    setDetail(null);
    setVideoUrl(null);
    setVideoError(null);
  }, []);

  return (
    <div className="h-screen w-screen flex flex-col overflow-hidden bg-background text-foreground">
      <div className="h-9 flex-shrink-0 flex items-center justify-between px-4 border-b border-border bg-card/60">
        <div className="flex items-center gap-3">
          <Link href="/" className="flex items-center gap-1.5 font-mono text-[10px] text-muted-foreground hover:text-primary transition-colors">
            <ChevronLeft size={12} /> SENTINEL-UGV
          </Link>
          <div className="w-px h-4 bg-border" />
          <div className="flex items-center gap-1.5">
            {/* 왼쪽 내비의 「이력」과 같은 아이콘을 쓴다 (S15P11A301-303).
                같은 화면을 두 곳에서 다른 그림으로 가리키고 있었다. */}
            <Archive size={11} className="text-primary" />
            <span className="font-mono text-[11px] text-primary">임무 이력</span>
          </div>
        </div>
        <span className="font-mono text-[10px] text-muted-foreground">{missions.length}개 임무</span>
      </div>

      <div className="flex-1 flex overflow-hidden">
        {/* 임무 목록 */}
        <div className="w-64 flex-shrink-0 border-r border-border bg-card flex flex-col">
          {/* 패널 제목 10 → 13px (S15P11A301-303). 목록 항목의 날짜·시각과 같은
              크기여서 제목으로 읽히지 않았다 — 무엇의 목록인지 알기 어려웠다. */}
          <div className="p-3 border-b border-border">
            <span className="font-mono text-[13px] font-semibold text-foreground tracking-wide">임무 목록</span>
          </div>
          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            {loadError && (
              <p className="font-mono text-[10px] text-destructive px-2 py-1.5">{loadError}</p>
            )}
            {!loadError && missions.length === 0 && (
              <p className="font-mono text-[10px] text-muted-foreground px-2 py-1.5">임무 기록이 없습니다</p>
            )}
            {missions.map(m => (
              <button
                key={m.id}
                onClick={() => setSelected(m)}
                className={`w-full text-left font-mono text-[10px] px-2 py-2 rounded transition-colors border ${
                  selected?.id === m.id
                    ? "bg-primary/10 text-primary border-primary/20"
                    : "text-muted-foreground border-transparent hover:text-foreground hover:bg-secondary/50"
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span>{fmtDate(m.createdAt)} {fmtTime(m.startedAt ?? m.createdAt)}</span>
                  <span className={`px-1.5 py-0.5 rounded border text-[9px] ${missionTone(m.status)}`}>
                    {MISSION_STATUS_LABEL[m.status] ?? m.status}
                  </span>
                </div>
                {/* 세로로 쌓이는 목록이라 자릿수가 흔들리면 눈이 걸린다.
                    이 줄은 sans 라 tnum 을 켜야 정렬된다 — 옆의 요약 스트립은
                    이미 font-mono 라 따로 켜지 않는다 (S15P11A301-302). */}
                <div className="flex items-center gap-2 text-muted-foreground/70 text-[9px] tabular-nums">
                  <span><Clock size={8} className="inline mr-0.5" />{fmtDuration(m.durationSec)}</span>
                  <span>{fmtDistance(m.distanceM)}</span>
                  {m.detectionCount !== null && m.detectionCount > 0 && (
                    <span className="text-accent"><User size={8} className="inline mr-0.5" />{m.detectionCount}</span>
                  )}
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* 가운데 — 요약 스트립 + 임무 지도. 발견을 열면 이 위에 영상 오버레이가 뜬다. */}
        <div className="relative flex-1 min-w-0 flex flex-col overflow-hidden p-3 gap-3">
          {selected && (
            <>
              {/* 임무 결과 요약 — 값 한 줄이면 충분해서 얇은 스트립으로 그린다.
                  예전 2×2 카드는 옆 차트 높이에 맞춰 늘어나 대부분이 빈 공간이었다. */}
              <div className="flex gap-2 flex-shrink-0">
                {[
                  { k: "걸린 시간", v: fmtDuration(selected.durationSec) },
                  { k: "이동 거리", v: fmtDistance(selected.distanceM) },
                  { k: "발견 인원", v: selected.detectionCount?.toString() ?? "—" },
                  {
                    k: "종료 사유",
                    v: selected.endReason ? (END_REASON_LABEL[selected.endReason] ?? selected.endReason) : "—",
                  },
                  // 녹화기 (S15P11A301-311) — 발견을 하나하나 열지 않아도 마감 실패가
                  // 보여야 재발을 알아챈다. 사유는 젯슨이 래치하고 그 래치는 임무
                  // 경계에서 지워지지 않으므로 「이 임무가 실패했다」가 아니라 「실패
                  // 기록이 남아 있다」로 말한다. 시점은 발견별 판정이 좁힌다.
                  {
                    k: "녹화기",
                    v: missionRecorder.state === "FAILED"
                      ? "실패 기록 있음"
                      : missionRecorder.state === "OK" ? "정상" : "미보고",
                    tone: missionRecorder.state === "FAILED"
                      ? "text-accent"
                      : missionRecorder.state === "OK" ? "text-foreground" : "text-muted-foreground",
                    title: missionRecorder.lastFailure
                      ? `${missionRecorder.lastFailure} — ${mediaStateLabel(missionRecorder.lastFailure)}`
                      : missionRecorder.state === "FAILED"
                        ? "마감 실패가 보고됐지만 사유는 보고되지 않았습니다"
                        : "녹화기 상태 보고가 없습니다 — 「정상」이 아니라 판정 근거가 없다는 뜻입니다",
                  },
                ].map(({ k, v, tone, title }: { k: string; v: string; tone?: string; title?: string }) => (
                  <div
                    key={k}
                    title={title}
                    className="flex-1 min-w-0 border border-border rounded bg-card px-3 py-1.5 flex items-baseline justify-between gap-2"
                  >
                    <span className="font-mono text-[9px] text-muted-foreground flex-shrink-0">{k}</span>
                    <span className={`font-mono text-xs truncate ${tone ?? "text-foreground"}`}>{v}</span>
                  </div>
                ))}
              </div>

              {/* 임무 지도 — 메인. 마커 클릭 → 영상 (S15P11A301-203) */}
              <div className="flex-1 min-h-0">
                <MissionMap
                  fill
                  missionId={selected.id}
                  encounters={encounters}
                  onEncounterClick={openEncounter}
                />
              </div>
            </>
          )}

          {/* 영상 오버레이 — 지도 위에 뜨고 닫으면 지도로 복귀. 예전처럼 상단에
              패널이 끼어들며 레이아웃 전체를 밀어내지 않는다 (S15P11A301-273). */}
          {(detail || videoUrl || videoError) && (
            <div
              className="absolute inset-0 z-20 bg-background/70 backdrop-blur-sm flex items-center justify-center p-6"
              onClick={closeOverlay}
            >
              <div
                className="border border-border rounded-lg bg-card shadow-2xl p-4 w-full max-w-5xl max-h-full overflow-y-auto"
                onClick={e => e.stopPropagation()}
              >
                {/* 헤더 — 발견 요약 한 줄 + 닫기. 영상이 증거의 본체라 카드 폭 전체를
                    영상에 주고 메타는 아래로 내린다. 옆 컬럼으로 두면 좁은 창에서
                    영상이 도로 작아진다(실측). */}
                <div className="flex items-center justify-between mb-3">
                  <p className="font-mono text-xs text-foreground">
                    {detail
                      ? <>{ENCOUNTER_STATUS_LABEL[detail.status] ?? detail.status}
                          <span className="text-muted-foreground"> · 발견 {fmtTime(detail.startedAt)}</span></>
                      : "이벤트 영상"}
                  </p>
                  <button
                    onClick={closeOverlay}
                    className="font-mono text-[10px] px-2 py-1 border border-border rounded text-muted-foreground hover:text-foreground transition-colors"
                  >
                    ✕ 닫기
                  </button>
                </div>

                <div className="rounded border border-border overflow-hidden bg-black w-full aspect-video">
                  {videoUrl ? (
                    <video ref={videoRef} src={videoUrl} controls autoPlay className="w-full h-full object-contain" />
                  ) : (
                    // 영상이 없는 자리에서 세 가지를 구별해 말한다 (S15P11A301-311).
                    // 색만으로 구별하지 않는다(28.6) — 아이콘과 문구를 함께 바꾼다.
                    <div className="w-full h-full flex flex-col items-center justify-center gap-2 px-6 text-center">
                      {missingMedia?.kind === "RECORDING_FAILED" ? (
                        <VideoOff size={24} className="text-accent/70" />
                      ) : missingMedia?.kind === "NO_EVIDENCE" ? (
                        <HelpCircle size={24} className="text-muted-foreground/40" />
                      ) : (
                        <Play size={24} className="text-muted-foreground/40" />
                      )}
                      <span
                        className={`font-mono text-[10px] ${
                          missingMedia?.kind === "RECORDING_FAILED" ? "text-accent" : "text-muted-foreground"
                        }`}
                      >
                        {missingMedia?.message ?? videoError}
                      </span>
                      {/* 원문 사유 — 툴팁만 두면 마우스 없는 화면에서 열리지 않고,
                          젯슨 `report.json` 의 `mediaState` 와 눈으로 대조해야 할 때가
                          있다. 한국어 문구는 표시 변환일 뿐이고 대조는 이 값으로 한다. */}
                      {missingMedia?.rawReason && (
                        <span className="font-mono text-[9px] text-muted-foreground/60 select-all">
                          {missingMedia.rawReason}
                        </span>
                      )}
                    </div>
                  )}
                </div>

                {/* ── 소음 제거본 토글 — 조작부. 제거본 자산이 있을 때만 그린다 ── */}
                {videoUrl && denoisedUrl && (
                  <div className="mt-2 flex items-center gap-3">
                    <audio ref={audioRef} src={denoisedUrl} preload="auto" />
                    <button
                      onClick={() => setDenoised(v => !v)}
                      className={`flex-shrink-0 flex items-center justify-center gap-2 font-mono text-[11px] px-3 py-2 rounded border transition-colors ${
                        denoised
                          ? "border-primary/50 bg-primary/15 text-primary"
                          : "border-border bg-secondary/30 text-muted-foreground hover:text-foreground"
                      }`}
                      title="영상은 그대로 두고 소리만 바꿉니다"
                    >
                      {denoised ? <Volume2 size={12} /> : <VolumeX size={12} />}
                      {denoised ? "소음 제거본 재생 중 — 원본으로" : "소음 제거본으로 듣기"}
                    </button>
                    <p className="font-mono text-[9px] text-muted-foreground/70 leading-relaxed">
                      제거본은 명료도 보조입니다. <span className="text-muted-foreground">원본이 증거</span>이며
                      두드림·신음 같은 비언어 소리는 제거본에서 사라질 수 있습니다.
                    </p>
                  </div>
                )}

                {detail && (
                  <div className="mt-3 space-y-2">
                    <div className="grid grid-cols-3 gap-2">
                      {[
                        ["감지 인원", detail.detectedPersonCount?.toString() ?? "—"],
                        ["상호작용", detail.interactionStartedAt ? `${fmtTime(detail.interactionStartedAt)}~${fmtTime(detail.interactionEndedAt)}` : "—"],
                        // 영상만 센다 — 잡음 제거 오디오(#228)까지 세면 영상이 없는
                        // 발견이 「영상 개수 1」로 보인다. 옆의 녹화 실패 문구와
                        // 정면으로 어긋나는 자리다 (S15P11A301-311).
                        ["영상 개수", detail.media.filter(m => m.type === "EVENT_VIDEO").length.toString()],
                        // ── 음성 보고 (S15P11A301-242) — 요구조자 발화 기반 값이다.
                        // responsivePersonCount 는 응답이 확인되지 않은 주변 인원까지
                        // 포함하므로 "응답 가능"이라 쓰지 않는다. null 은 추출 실패
                        // (GMS 장애 폴백 포함) — 0(아무도 없음)과 다르므로 "미확인".
                        ["요구조자가 말한 인원", detail.responsivePersonCount === null ? "미확인" : `${detail.responsivePersonCount}명`],
                        ["무응답 인원", detail.unresponsivePersonCount === null ? "미확인" : `${detail.unresponsivePersonCount}명`],
                        ["종료 사유", detail.terminationReason ? (TERMINATION_LABEL[detail.terminationReason] ?? detail.terminationReason) : "—"],
                      ].map(([k, v]) => (
                        <div key={k} className="border border-border rounded px-2 py-1.5 bg-secondary/20">
                          <p className="font-mono text-[9px] text-muted-foreground">{k}</p>
                          <p className="font-mono text-xs text-foreground truncate">{v}</p>
                        </div>
                      ))}
                    </div>
                    {/* 음성 보고 요약 줄을 뺐다 (S15P11A301-365).
                        표시하던 세 값(riskLevel·mobilityStatus·urgentConditionReported)이
                        추출 실패 시 전부 UNKNOWN 으로 오는데, 그 사실은 바로 위 6칸의
                        「미확인」이 이미 말한다. 같은 없음을 「위험도 UNKNOWN · 이동성
                        미확인 · 긴급 상태 UNKNOWN」으로 한 번 더 쓰면 읽는 사람이 얻는
                        것 없이 화면만 시끄럽다. 값이 채워지면 6칸이 그것을 보여준다. */}
                    {(detail.encounterPose || detail.additionalPersonReports.length > 0) && (
                      <div className="space-y-1.5 rounded border border-accent/40 bg-accent/5 p-2">
                        {detail.encounterPose && (
                          <div className="flex items-start gap-1.5 font-mono text-[10px] text-muted-foreground">
                            <MapPin size={11} className="mt-0.5 flex-shrink-0 text-accent" />
                            <span>
                              음성 상호작용 위치: ({detail.encounterPose.x.toFixed(1)}, {detail.encounterPose.y.toFixed(1)})
                              {detail.encounterPose.mapId ? ` · ${detail.encounterPose.mapId}` : ""}
                              <span className="ml-1 text-[9px]">(로봇 Encounter 기준)</span>
                            </span>
                          </div>
                        )}
                        {detail.additionalPersonReports.map((report, index) => (
                          <div key={`${report.rawUtterance}-${index}`} className="border-t border-accent/20 pt-1.5 font-mono text-[10px]">
                            <p className="text-accent">
                              ⚠ 추가 요구조자 미확인 제보
                              {report.certaintyStatus === "TENTATIVE" ? " · 불확실 표현" : ""}
                            </p>
                            <p className="text-foreground">
                              대상: {report.subjectText ?? "미상"}
                              {report.countStatus === "EXACT" && report.reportedCount !== null
                                ? ` · ${report.reportedCount}명`
                                : " · 인원 수 미확인"}
                              {` · 위치: ${report.locationText ?? "미확인"}`}
                            </p>
                            <p className="text-muted-foreground break-words">
                              원문: “{report.rawUtterance}”
                            </p>
                            <p className="text-[9px] text-muted-foreground">
                              지도 연결 불가 · 음성 제보이며 좌표·자동 주행 목표로 사용하지 않습니다.
                            </p>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* 오른쪽 — 임무 당시 환경 + 발견 목록. 발견이 이 화면의 주인공이라 스크롤
            없이 항상 보이는 자리에 둔다. */}
        <div className="w-80 flex-shrink-0 border-l border-border bg-card flex flex-col overflow-hidden">
          {/* CPU 그래프는 뺐다(S15P11A301-273) — 관제자 판단에 도움이 안 돼 관제
              화면에서 뺀 값(#223)이 이력에서라고 유용해지지 않는다. 대신 재난 현장
              기록으로 의미 있는 임무 당시 온습도를 그린다. 값은 이미 telemetry
              응답에 있다(#205). 단위가 달라(°C vs %) 차트를 나눈다. */}
          {/* 차트는 h-full 로 부모를 채우므로 높이가 정해진 상자에 넣는다 — 자동 높이
              컨테이너에 두면 퍼센트 높이가 무너져 아래 목록과 겹친다(실측). */}
          <div className="p-2 space-y-2 flex-shrink-0">
            <div className="h-28">
              <TelemetryChart points={envTelemetry} metric="temperature" label="임무 중 온도" unit="°C" />
            </div>
            <div className="h-28">
              <TelemetryChart points={envTelemetry} metric="humidity" label="임무 중 습도" unit="%" />
            </div>
          </div>

          <p className="px-3 py-2 border-y border-border font-mono text-[10px] text-muted-foreground uppercase tracking-wider flex-shrink-0">
            발견 이벤트 {encounters.length > 0 && `(${encounters.length})`}
          </p>
          <div className="flex-1 overflow-y-auto p-2 space-y-2">
            {encountersLoading && (
              <p className="font-mono text-[10px] text-muted-foreground px-1 py-1">불러오는 중…</p>
            )}
            {!encountersLoading && encounters.length === 0 && (
              <p className="font-mono text-[10px] text-muted-foreground px-1 py-1">이 임무에서 발견된 사람이 없습니다</p>
            )}
            {encounters.map(enc => (
              <div
                key={enc.id}
                onClick={() => openEncounter(enc)}
                className={`border rounded p-2.5 bg-card hover:border-primary/30 transition-colors cursor-pointer ${
                  detail?.id === enc.id ? "border-primary/40 bg-primary/5" : "border-border"
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <div className="w-8 h-8 rounded-full flex-shrink-0 flex items-center justify-center border border-accent/30 bg-accent/10">
                    <User size={12} className="text-accent" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between mb-0.5">
                      <span className="font-mono text-[10px] text-foreground truncate">
                        {ENCOUNTER_STATUS_LABEL[enc.status] ?? enc.status}
                        {enc.detectedPersonCount !== null && (
                          <span className="text-accent"> · {enc.detectedPersonCount}명</span>
                        )}
                      </span>
                      <span className="font-mono text-[9px] text-muted-foreground flex-shrink-0 ml-1">{fmtTime(enc.startedAt)}</span>
                    </div>
                    <div className="flex items-center gap-2 font-mono text-[9px] text-muted-foreground">
                      {enc.mapX !== null && enc.mapY !== null ? (
                        <span><MapPin size={8} className="inline mr-0.5" />({enc.mapX.toFixed(1)}, {enc.mapY.toFixed(1)})</span>
                      ) : (
                        <span className="text-muted-foreground/50">위치 미기록</span>
                      )}
                      {enc.terminationReason && (
                        <span className="truncate">{TERMINATION_LABEL[enc.terminationReason] ?? enc.terminationReason}</span>
                      )}
                    </div>
                  </div>
                  <button
                    onClick={e => { e.stopPropagation(); openEncounter(enc); }}
                    className="flex-shrink-0 w-7 h-7 rounded-full border border-primary/30 bg-primary/10 hover:bg-primary/20 flex items-center justify-center text-primary transition-colors"
                    title="이벤트 영상 재생"
                  >
                    <Play size={11} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
