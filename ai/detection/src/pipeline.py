"""Detect → Track → Person filter → Crop → Pose → Rule → Persistence → Log → Save.

각 모듈을 호출하되 모델 구현을 중복 포함하지 않는다(AGENTS.md §10).
"""

from __future__ import annotations

import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .logger import PipelineLogger
from .object_detector import ObjectDetector
from .persistence import PersistenceTracker
from .pose_estimator import PoseEstimator, PoseScheduler
from .posture_classifier import PostureClassifier, PostureSmoother
from .schemas import (
    POSTURE_UNKNOWN,
    FrameResult,
    PersonObservation,
    PostureResult,
    build_encounter_data,
    build_envelope,
    new_uuid,
    utc_now_iso,
)
from .storage import EventImageStore
from .visualize import draw


# 이 파일 기준 프로젝트 루트(ai/detection). 설정 안의 상대 경로 해석에 쓴다.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve_path(value: str) -> str:
    """설정 파일의 상대 경로를 프로젝트 루트 기준 절대 경로로 바꾼다.

    cwd 기준으로 두면 `cd` 위치에 따라 실행이 실패한다. Jetson에서 systemd 서비스나
    ROS2 launch로 띄우면 cwd가 프로젝트 루트가 아닌 경우가 흔하다.
    Ultralytics 기본 트래커 이름(bytetrack.yaml 등)이나 이미 존재하는 경로는 그대로 둔다.
    """
    path = Path(value)
    if path.is_absolute() or path.exists():
        return value
    candidate = PROJECT_ROOT / value
    return str(candidate) if candidate.exists() else value


@dataclass
class PipelineStats:
    frames: int = 0
    frames_with_person: int = 0
    detections: int = 0
    pose_runs: int = 0
    # 사람 단위로 encounter 조건을 충족한 횟수. 한 프레임에 여러 명이면 여러 번 증가한다.
    person_events: int = 0
    # possible_fallen이 심각도 기준을 넘긴 관측 수(이벤트 수가 아니라 프레임 누적).
    fallen_observations: int = 0
    # 실제로 발행된 이벤트 메시지 수. events.jsonl의 줄 수와 일치해야 한다.
    events: int = 0
    # 처리에 걸린 실제 시간(초).
    elapsed_sec: float = 0.0
    # 트래커의 track_buffer(프레임). forget_seconds에서 자동 환산된 값.
    track_buffer_frames: int = 0
    # 설정한 기억 시간(초)과, 마지막 환산 시점의 실측 FPS 기준 실제 기억 시간(초).
    memory_target_sec: float = 0.0
    memory_actual_sec: float = 0.0

    @property
    def avg_fps(self) -> float:
        return self.frames / self.elapsed_sec if self.elapsed_sec > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "frames": self.frames,
            "frames_with_person": self.frames_with_person,
            "detections": self.detections,
            "pose_runs": self.pose_runs,
            "person_events": self.person_events,
            "fallen_observations": self.fallen_observations,
            "events": self.events,
            "elapsed_sec": round(self.elapsed_sec, 2),
            "avg_fps": round(self.avg_fps, 2),
        }
        # 기억 시간은 초 단위 설정값이고, track_buffer는 거기서 환산된 프레임 수다.
        # memory_actual_sec가 target에서 크게 벗어나면 min/max 클램프에 걸린 것이다.
        if self.track_buffer_frames:
            out["track_buffer_frames"] = self.track_buffer_frames
            out["memory_target_sec"] = round(self.memory_target_sec, 1)
            out["memory_actual_sec"] = round(self.memory_actual_sec, 1)
        return out


class InferencePipeline:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        output_dir: Path,
        detector_model: str | None = None,
        pose_model: str | None = None,
        device: str | None = None,
    ) -> None:
        self.config = config
        self.output_dir = output_dir
        out_cfg = config["output"]

        det_cfg = config["detector"]
        self.detector = ObjectDetector(
            detector_model or det_cfg["model"],
            # 설정 파일 안의 상대 경로는 프로젝트 루트 기준으로 해석한다.
            # cwd 기준이면 다른 디렉터리에서 실행할 때 트래커 설정을 못 찾는다.
            target_classes=det_cfg["target_classes"],
            confidence=det_cfg["confidence"],
            iou=det_cfg["iou"],
            tracker=_resolve_path(det_cfg["tracker"]),
            device=device,
            imgsz=det_cfg.get("imgsz", 640),
            quantize=det_cfg.get("quantize"),
        )

        pose_cfg = config["pose"]
        self.pose_estimator = PoseEstimator(
            pose_model or pose_cfg["model"],
            confidence=pose_cfg["confidence"],
            crop_margin=pose_cfg["crop_margin"],
            device=device,
            imgsz=pose_cfg.get("imgsz", 640),
            quantize=pose_cfg.get("quantize"),
        )
        self.keypoint_confidence = pose_cfg["keypoint_confidence"]

        posture_cfg = config["posture"]
        self.classifier = PostureClassifier(
            torso_horizontal_deg=posture_cfg["torso_horizontal_deg"],
            bbox_aspect_ratio=posture_cfg["bbox_aspect_ratio"],
            vertical_extent_ratio=posture_cfg["vertical_extent_ratio"],
            min_valid_keypoints=posture_cfg["min_valid_keypoints"],
            keypoint_confidence=self.keypoint_confidence,
        )
        self.smoother = PostureSmoother(
            window=posture_cfg.get("smoothing_window", 1),
            unknown_yields_to_known=posture_cfg.get("unknown_yields_to_known", True),
        )

        mem_cfg = config.get("memory") or {}
        self.forget_seconds = float(mem_cfg.get("forget_seconds", 10.0))
        self._min_track_buffer = int(mem_cfg.get("min_track_buffer", 30))
        self._max_track_buffer = int(mem_cfg.get("max_track_buffer", 900))
        self.persistence = PersistenceTracker(
            forget_seconds=self.forget_seconds, **config["persistence"]
        )

        trigger = config["pose_trigger"]
        self.pose_scheduler = PoseScheduler(
            activate_after_frames=trigger.get("activate_after_frames", 3),
            max_fps=trigger.get("max_fps", 2.0),
            deactivate_after_seconds=trigger.get("deactivate_after_seconds", 3.0),
            min_bbox_width=trigger["min_bbox_width"],
            min_bbox_height=trigger["min_bbox_height"],
        )

        self.logger = PipelineLogger(
            output_dir,
            frames_filename=out_cfg["jsonl_frames"],
            events_filename=out_cfg["jsonl_events"],
            write_frame_log=out_cfg["write_frame_log"],
        )
        self.image_store = EventImageStore(output_dir / out_cfg["events_dir"])
        self.draw_overlay_on_event = out_cfg["draw_overlay_on_event_image"]

        report_cfg = config["report"]
        self.schema_version = report_cfg["schema_version"]
        self.robot_id = report_cfg["robot_id"]
        self.mission_id = report_cfg["mission_id"]

        self.stats = PipelineStats()
        self._sequence = 0
        self._track_buffer_frames = self._read_track_buffer(det_cfg["tracker"])

    @staticmethod
    def _read_track_buffer(tracker_cfg: str) -> int:
        """트래커 설정에서 track_buffer(프레임)를 읽는다.

        실측 FPS와 함께 "추적이 유지되는 시간"을 초로 환산해 보여주기 위한 값이다.
        읽지 못해도 파이프라인 동작에는 영향이 없으므로 0으로 둔다.
        """
        path = Path(tracker_cfg)
        if not path.exists():
            # Ultralytics 기본 트래커 이름이면 패키지 안에서 찾는다.
            try:
                import ultralytics

                path = Path(ultralytics.__file__).parent / "cfg" / "trackers" / tracker_cfg
            except ImportError:
                return 0
        try:
            import yaml

            with path.open("r", encoding="utf-8") as fp:
                return int(yaml.safe_load(fp).get("track_buffer", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            return 0

    def process_frame(
        self, frame: np.ndarray, frame_index: int, timestamp_sec: float, source: str
    ) -> FrameResult:
        detections = self.detector.detect(frame)
        self.stats.frames += 1
        self.stats.detections += len(detections)
        if detections:
            self.stats.frames_with_person += 1

        persons: list[PersonObservation] = []
        for det in detections:
            pose = None
            # person이 탐지되지 않으면 Pose를 아예 실행하지 않는다(게이트 2번).
            # 실행 여부는 명세 25.6의 조건부 규칙을 따른다(3프레임 연속, 약 2FPS).
            pose_ran = self.pose_scheduler.should_run(det, timestamp_sec)
            if pose_ran:
                pose = self.pose_estimator.estimate(frame, det)
                self.stats.pose_runs += 1
                posture = self.smoother.smooth(det.track_id, self.classifier.classify(det, pose))
                self.pose_scheduler.cache(det.track_id, posture)
            else:
                # Pose를 돌리지 않은 프레임은 직전 판정을 재사용한다.
                # 없으면 아직 자세를 모르는 상태다(명세: 관절 정보 부족 = POSE_UNKNOWN).
                posture = self.pose_scheduler.cached(det.track_id) or PostureResult(
                    status=POSTURE_UNKNOWN, reason="Pose 미실행(조건부 스케줄)"
                )


            x1, y1, x2, y2 = det.bbox_xyxy
            state = self.persistence.update(
                det.track_id,
                posture.status,
                timestamp_sec,
                # ID가 바뀌어도 같은 자리면 지속 시간을 승계하기 위한 정보
                center=((x1 + x2) / 2.0, (y1 + y2) / 2.0),
                size=max(det.width, det.height),
            )
            if state.event_confirmed:
                self.stats.person_events += 1
            fallen_confirmed = self.persistence.is_fallen_confirmed(state.fallen_sec)
            if fallen_confirmed:
                self.stats.fallen_observations += 1

            persons.append(
                PersonObservation(
                    detection=det,
                    pose=pose,
                    posture=posture,
                    seen_sec=state.seen_sec,
                    fallen_sec=state.fallen_sec,
                    event_confirmed=state.event_confirmed,
                    fallen_confirmed=fallen_confirmed,
                    pose_ran=pose_ran,
                )
            )

        self.persistence.prune(timestamp_sec)
        self.pose_scheduler.prune(timestamp_sec)
        self.smoother.forget({d.track_id for d in detections if d.track_id is not None})

        result = FrameResult(
            frame_index=frame_index,
            timestamp=utc_now_iso(),
            source=source,
            persons=persons,
        )
        self.logger.log_frame(result.to_dict())

        confirmed_persons = [p for p in persons if p.event_confirmed]
        if confirmed_persons:
            self._emit_event(frame, persons, confirmed_persons, result)

        return result

    def _emit_event(
        self,
        frame: np.ndarray,
        all_persons: list[PersonObservation],
        confirmed: list[PersonObservation],
        result: FrameResult,
    ) -> None:
        """이벤트 이미지 저장 + 명세 31-5 봉투로 이벤트 로그 기록."""
        image = (
            draw(frame, all_persons, keypoint_confidence=self.keypoint_confidence)
            if self.draw_overlay_on_event
            else frame
        )
        track_id = confirmed[0].detection.track_id
        image_path = self.image_store.save(image, track_id=track_id)

        self._sequence += 1
        data = build_encounter_data(confirmed, encounter_id=new_uuid())
        # 명세가 정의하지 않은 로컬 부가 정보는 별도 키로 분리해 봉투를 오염시키지 않는다.
        data["_local"] = {
            "frameIndex": result.frame_index,
            "source": result.source,
            "eventImage": str(image_path) if image_path else None,
            "persons": [p.to_dict() for p in confirmed],
        }

        envelope = build_envelope(
            "ENCOUNTER_CONFIRMED",
            data,
            schema_version=self.schema_version,
            robot_id=self.robot_id,
            mission_id=self.mission_id,
            sequence=self._sequence,
        )
        self.logger.log_event(envelope)
        self.stats.events += 1

    def _sync_track_buffer(self, measured_fps: float) -> int:
        """실측 FPS를 반영해 트래커의 기억 길이를 forget_seconds에 맞춘다.

        트래커의 track_buffer는 **프레임 단위**라, 고정해두면 실제 기억 시간이
        FPS에 따라 출렁인다(사람 수·장치에 따라 25~91초까지 벌어졌다).
        매 프레임 환산해서 "10초 기억"이 항상 10초가 되게 한다.
        """
        if measured_fps <= 0:
            return self._track_buffer_frames
        frames = int(round(self.forget_seconds * measured_fps))
        frames = max(self._min_track_buffer, min(self._max_track_buffer, frames))
        # 클램프에 걸리면 실제 기억 시간이 목표와 달라진다. 통계로 드러나게 기록한다.
        self.stats.memory_target_sec = self.forget_seconds
        self.stats.memory_actual_sec = frames / measured_fps
        if frames == self._track_buffer_frames:
            return frames

        predictor = getattr(self.detector.model, "predictor", None)
        for tracker in getattr(predictor, "trackers", []) or []:
            # _remove_stale_lost가 참조하는 값. args도 함께 맞춰 일관성을 유지한다.
            if hasattr(tracker, "max_frames_lost"):
                tracker.max_frames_lost = frames
            if hasattr(tracker, "args"):
                tracker.args.track_buffer = frames
        self._track_buffer_frames = frames
        return frames

    def _open_camera(self, index: int) -> cv2.VideoCapture:
        """카메라를 설정된 해상도·코덱으로 연다.

        해상도를 지정하지 않으면 OpenCV가 640x480으로 여는 경우가 많아 화질이 나빠 보인다.
        카메라 성능 문제가 아니라 요청을 안 한 것이므로 명시적으로 지정한다.
        """
        cam = self.config.get("camera") or {}
        backend_name = str(cam.get("backend", "auto")).lower()
        if backend_name == "auto":
            # Windows는 DirectShow, Linux(Jetson 포함)는 V4L2가 USB 카메라 표준 경로다.
            backend_name = "dshow" if sys.platform == "win32" else "v4l2"
        backend = {
            "dshow": cv2.CAP_DSHOW,      # Windows 전용
            "msmf": cv2.CAP_MSMF,        # Windows 전용
            "v4l2": cv2.CAP_V4L2,        # Linux / Jetson USB 카메라
            "gstreamer": cv2.CAP_GSTREAMER,
            "any": cv2.CAP_ANY,
        }.get(backend_name, cv2.CAP_ANY)

        capture = cv2.VideoCapture(index, backend)
        if not capture.isOpened():
            return capture

        # fourcc를 먼저 설정해야 해상도 요청이 제대로 반영된다.
        fourcc = str(cam.get("fourcc", "") or "")
        if len(fourcc) == 4:
            capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))

        width = int(cam.get("width", 0) or 0)
        height = int(cam.get("height", 0) or 0)
        if width and height:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        requested_fps = float(cam.get("fps", 0) or 0)
        if requested_fps > 0:
            capture.set(cv2.CAP_PROP_FPS, requested_fps)

        actual_w = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"[pipeline] 카메라 {index} ({backend_name}) 해상도 {actual_w}x{actual_h}")
        if width and height and (actual_w, actual_h) != (width, height):
            print(
                f"[pipeline] 요청 {width}x{height}가 적용되지 않았습니다. "
                "카메라가 지원하지 않는 해상도일 수 있습니다.",
                file=sys.stderr,
            )
        return capture

    def run_video(
        self,
        source: str | int,
        *,
        max_frames: int | None = None,
        show: bool = False,
        window_name: str = "Sentinel Detection",
    ) -> PipelineStats:
        """영상 하나 또는 카메라 스트림을 처리한다.

        source가 int면 카메라 장치로 간주하고 **벽시계 시간**으로 persistence를 잰다.
        카메라는 프레임 드롭이 있어 frame_index/fps가 실제 경과 시간과 어긋나기 때문이다.
        파일 입력은 재현성을 위해 영상 내 시간(frame_index/fps)을 쓴다.

        show=True면 overlay 미리보기 창을 띄운다. q 또는 ESC로 종료한다.
        """
        is_live = isinstance(source, int)

        if is_live:
            capture = self._open_camera(int(source))
        else:
            capture = cv2.VideoCapture(source)

        if not capture.isOpened():
            capture.release()
            hint = (
                " 카메라가 다른 프로그램에서 사용 중이거나 장치 번호가 다를 수 있습니다."
                if is_live
                else ""
            )
            raise RuntimeError(f"입력을 열 수 없습니다: {source}.{hint}")

        # 새 입력마다 추적 상태를 초기화한다. 안 하면 trackId가 이어져 오탐이 된다.
        self.detector.reset_tracker()
        self.persistence.reset()
        self.smoother.reset()
        self.pose_scheduler.reset()

        fps = capture.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 0 or np.isnan(fps):
            fps = 30.0
            if not is_live:
                print(
                    f"[pipeline] FPS를 읽을 수 없어 {fps}로 가정합니다. "
                    "persistence 판정이 부정확할 수 있습니다.",
                    file=sys.stderr,
                )

        if show:
            cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
            print("[pipeline] 미리보기 실행 중 — 종료하려면 창을 선택하고 q 또는 ESC")

        frame_index = 0
        start = time.monotonic()
        recent = deque(maxlen=30)  # 최근 프레임 소요 시간(초). 순간 FPS 표시용.
        try:
            while True:
                loop_start = time.monotonic()
                ok, frame = capture.read()
                if not ok:
                    if is_live:
                        print("[pipeline] 카메라 프레임을 읽지 못했습니다.", file=sys.stderr)
                    break

                timestamp_sec = (loop_start - start) if is_live else (frame_index / fps)
                result = self.process_frame(frame, frame_index, timestamp_sec, str(source))
                frame_index += 1
                recent.append(time.monotonic() - loop_start)

                # 실측 FPS로 기억 길이를 계속 맞춘다. 초기 몇 프레임은 표본이 부족해 건너뛴다.
                if len(recent) >= 5:
                    self._sync_track_buffer(len(recent) / sum(recent))

                if show:
                    preview = draw(
                        frame, result.persons, keypoint_confidence=self.keypoint_confidence
                    )
                    live_fps = len(recent) / sum(recent) if sum(recent) > 0 else 0.0
                    hold = self._track_buffer_frames / live_fps if live_fps > 0 else 0.0
                    cv2.putText(
                        preview,
                        f"{live_fps:5.1f} FPS | memory {hold:.1f}s "
                        f"(target {self.forget_seconds:.0f}s) | persons {len(result.persons)}",
                        (10, 28),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (0, 255, 255),
                        2,
                        cv2.LINE_AA,
                    )
                    cv2.imshow(window_name, preview)
                    # waitKey는 창을 갱신하는 역할도 하므로 show일 때 반드시 호출한다.
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):
                        print("[pipeline] 사용자 종료")
                        break

                if max_frames is not None and frame_index >= max_frames:
                    break
        finally:
            self.stats.elapsed_sec = time.monotonic() - start
            self.stats.track_buffer_frames = self._track_buffer_frames
            # 종료 시 반드시 리소스를 해제한다(게이트 10번).
            capture.release()
            if show:
                cv2.destroyWindow(window_name)
                # Windows에서 창이 즉시 닫히지 않는 경우가 있어 이벤트 루프를 몇 번 돌린다.
                for _ in range(4):
                    cv2.waitKey(1)

        return self.stats

    def close(self) -> None:
        self.logger.close()

    def __enter__(self) -> "InferencePipeline":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
