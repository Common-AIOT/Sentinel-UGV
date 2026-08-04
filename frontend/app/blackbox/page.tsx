"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ChevronLeft, Play, Film, User, MapPin, Clock, Volume2, VolumeX } from "lucide-react";
import {
  api,
  type EncounterDetail,
  type EncounterSummary,
  type MissionSummary,
  type TelemetryPoint,
} from "@/lib/api";
import TelemetryChart from "@/features/telemetry/TelemetryChart";
import MissionMap from "@/features/mapping/MissionMap";

/**
 * 임무 이력 화면. 운영 API 실데이터로 임무 목록 → 발견(encounter) 목록 → 이벤트
 * 영상 재생까지 이어진다 (S15P11A301-169).
 *
 * durationSec·distanceM·detectionCount 는 임무가 끝난 뒤 서버가 집계한다(#166).
 * 진행 중이거나 집계 이전 임무는 null 이므로 "—" 로 보여준다.
 */

/** 잡음 제거 오디오 kind (#228 계약). 원본 오디오의 파생물 — 원본이 증거다. */
const DENOISED_AUDIO_TYPE = "EVENT_AUDIO_DENOISED";

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
        setVideoError(
          d.media.length === 0
            ? "연결된 영상이 없습니다"
            : "영상이 아직 업로드되지 않았습니다 (PENDING)",
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

  return (
    <div className="h-screen w-screen flex flex-col overflow-hidden bg-background text-foreground">
      <div className="h-9 flex-shrink-0 flex items-center justify-between px-4 border-b border-border bg-card/60">
        <div className="flex items-center gap-3">
          <Link href="/" className="flex items-center gap-1.5 font-mono text-[10px] text-muted-foreground hover:text-primary transition-colors">
            <ChevronLeft size={12} /> GCS
          </Link>
          <div className="w-px h-4 bg-border" />
          <div className="flex items-center gap-1.5">
            <Film size={11} className="text-primary" />
            <span className="font-mono text-[11px] text-primary">임무 이력</span>
          </div>
        </div>
        <span className="font-mono text-[10px] text-muted-foreground">{missions.length}개 임무</span>
      </div>

      <div className="flex-1 flex overflow-hidden">
        {/* 임무 목록 */}
        <div className="w-64 flex-shrink-0 border-r border-border bg-card flex flex-col">
          <div className="p-3 border-b border-border">
            <span className="font-mono text-[10px] text-muted-foreground uppercase tracking-wider">임무 목록</span>
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
                <div className="flex items-center gap-2 text-muted-foreground/70 text-[9px]">
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

        {/* 임무 상세 */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* 영상 재생 패널 */}
          {(videoUrl || videoError) && (
            <div className="flex-shrink-0 border-b border-border bg-[#141a22] p-4">
              <div className="flex gap-4">
                {/* ── 소음 제거본 토글 — 영상 아래에 조작부를 붙이려고 세로 묶음으로 감쌌다 ── */}
                <div className="flex-shrink-0 flex flex-col gap-2">
                  <div className="rounded border border-border overflow-hidden bg-black" style={{ width: 400, height: 225 }}>
                    {videoUrl ? (
                      <video ref={videoRef} src={videoUrl} controls autoPlay className="w-full h-full object-contain" />
                    ) : (
                      <div className="w-full h-full flex flex-col items-center justify-center gap-2">
                        <Play size={24} className="text-muted-foreground/40" />
                        <span className="font-mono text-[10px] text-muted-foreground">{videoError}</span>
                      </div>
                    )}
                  </div>

                  {/* ── 소음 제거본 토글 — 조작부. 제거본 자산이 있을 때만 그린다 ── */}
                  {videoUrl && denoisedUrl && (
                    <>
                      <audio ref={audioRef} src={denoisedUrl} preload="auto" />
                      <button
                        onClick={() => setDenoised(v => !v)}
                        className={`flex items-center justify-center gap-2 font-mono text-[11px] px-3 py-2 rounded border transition-colors ${
                          denoised
                            ? "border-primary/50 bg-primary/15 text-primary"
                            : "border-border bg-secondary/30 text-muted-foreground hover:text-foreground"
                        }`}
                        title="영상은 그대로 두고 소리만 바꿉니다"
                      >
                        {denoised ? <Volume2 size={12} /> : <VolumeX size={12} />}
                        {denoised ? "소음 제거본 재생 중 — 원본으로" : "소음 제거본으로 듣기"}
                      </button>
                      <p className="font-mono text-[9px] text-muted-foreground/70 leading-relaxed" style={{ width: 400 }}>
                        제거본은 명료도 보조입니다. <span className="text-muted-foreground">원본이 증거</span>이며
                        두드림·신음 같은 비언어 소리는 제거본에서 사라질 수 있습니다.
                      </p>
                    </>
                  )}
                </div>
                {detail && (
                  <div className="flex-1 space-y-2 min-w-0">
                    <p className="font-mono text-xs text-foreground">
                      {ENCOUNTER_STATUS_LABEL[detail.status] ?? detail.status}
                      <span className="text-muted-foreground"> · 발견 {fmtTime(detail.startedAt)}</span>
                    </p>
                    <div className="grid grid-cols-3 gap-2 max-w-md">
                      {[
                        ["감지 인원", detail.detectedPersonCount?.toString() ?? "—"],
                        ["상호작용", detail.interactionStartedAt ? `${fmtTime(detail.interactionStartedAt)}~${fmtTime(detail.interactionEndedAt)}` : "—"],
                        ["영상 개수", detail.media.length.toString()],
                      ].map(([k, v]) => (
                        <div key={k} className="border border-border rounded px-2 py-1.5 bg-secondary/20">
                          <p className="font-mono text-[9px] text-muted-foreground">{k}</p>
                          <p className="font-mono text-xs text-foreground truncate">{v}</p>
                        </div>
                      ))}
                    </div>
                    <button
                      onClick={() => { setDetail(null); setVideoUrl(null); setVideoError(null); }}
                      className="font-mono text-[10px] px-2 py-1 border border-border rounded text-muted-foreground hover:text-foreground transition-colors"
                    >
                      ✕ 닫기
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}

          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {selected && (
              <>
                {/* 임무 결과 요약 + 시스템 지표 — 한 블록. 왼쪽 2×2 카드, 오른쪽 그래프. */}
                <div className="grid grid-cols-2 gap-2">
                  <div className="grid grid-cols-2 gap-2">
                    {[
                      ["걸린 시간", fmtDuration(selected.durationSec)],
                      ["이동 거리", fmtDistance(selected.distanceM)],
                      ["발견 인원", selected.detectionCount?.toString() ?? "—"],
                      ["종료 사유", selected.endReason ?? "—"],
                    ].map(([k, v]) => (
                      <div key={k} className="border border-border rounded px-3 py-2 bg-card">
                        <p className="font-mono text-[9px] text-muted-foreground mb-0.5">{k}</p>
                        <p className="font-mono text-sm text-foreground">{v}</p>
                      </div>
                    ))}
                  </div>
                  <TelemetryChart points={telemetry} metric="cpu" label="CPU 사용률" unit="%" />
                </div>

                {/* 임무 지도 — 지도 위 발견 마커·주행 경로. 마커 클릭 → 영상 (S15P11A301-203) */}
                <MissionMap
                  missionId={selected.id}
                  encounters={encounters}
                  onEncounterClick={openEncounter}
                />

                {/* 발견 목록 */}
                <div>
                  <p className="font-mono text-[10px] text-muted-foreground uppercase tracking-wider mb-2">
                    발견 이벤트 {encounters.length > 0 && `(${encounters.length})`}
                  </p>
                  {encountersLoading && (
                    <p className="font-mono text-[10px] text-muted-foreground">불러오는 중…</p>
                  )}
                  {!encountersLoading && encounters.length === 0 && (
                    <p className="font-mono text-[10px] text-muted-foreground">이 임무에서 발견된 사람이 없습니다</p>
                  )}
                  <div className="space-y-2">
                    {encounters.map(enc => (
                      <div
                        key={enc.id}
                        onClick={() => openEncounter(enc)}
                        className={`border rounded p-3 bg-card hover:border-primary/30 transition-colors cursor-pointer ${
                          detail?.id === enc.id ? "border-primary/40 bg-primary/5" : "border-border"
                        }`}
                      >
                        <div className="flex items-center gap-4">
                          <div className="w-10 h-10 rounded-full flex-shrink-0 flex items-center justify-center border border-accent/30 bg-accent/10">
                            <User size={14} className="text-accent" />
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center justify-between mb-1">
                              <span className="font-mono text-[11px] text-foreground">
                                {ENCOUNTER_STATUS_LABEL[enc.status] ?? enc.status}
                                {enc.detectedPersonCount !== null && (
                                  <span className="text-accent"> · {enc.detectedPersonCount}명</span>
                                )}
                              </span>
                              <span className="font-mono text-[9px] text-muted-foreground">{fmtTime(enc.startedAt)}</span>
                            </div>
                            <div className="flex items-center gap-3 font-mono text-[10px] text-muted-foreground">
                              {enc.mapX !== null && enc.mapY !== null ? (
                                <span><MapPin size={9} className="inline mr-0.5" />({enc.mapX.toFixed(1)}, {enc.mapY.toFixed(1)})</span>
                              ) : (
                                <span className="text-muted-foreground/50">위치 미기록</span>
                              )}
                              {enc.terminationReason && <span>{enc.terminationReason}</span>}
                            </div>
                          </div>
                          <button
                            onClick={e => { e.stopPropagation(); openEncounter(enc); }}
                            className="flex-shrink-0 w-8 h-8 rounded-full border border-primary/30 bg-primary/10 hover:bg-primary/20 flex items-center justify-center text-primary transition-colors"
                            title="이벤트 영상 재생"
                          >
                            <Play size={12} />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
