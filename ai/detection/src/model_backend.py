"""추론 백엔드 판정과 강제 (S15P11A301-329).

Jetson 프로파일이 **조용히 PyTorch로 떨어지는 것**을 막는다.

그동안 `configs/pipeline.jetson.yaml`의 모델 값은 `.pt`였고 TensorRT 엔진은
실행 인자(`--model`)로만 들어왔다. `detection.launch.py`가 그 인자를 주므로
ROS 경로는 정상이지만, 인자 없이 같은 프로파일을 돌리면 PyTorch로 떨어진다.
대가는 프레임당 CPU **+145ms(43% 느려짐)**이고, 아무 경고도 없다.

이 함정은 실제로 사람을 잡았다. S15P11A301-329 조사에서 추적 설정 A/B를 한
바퀴 다 돌린 뒤, 프로파일에 `torch.conv2d`가 28,629회 찍힌 것을 보고서야
백엔드가 다르다는 것을 알았다. 그 회차의 결론 하나가 틀렸다 — `track_buffer`가
121.6ms를 절감하는 것처럼 보였는데 엔진으로 다시 재니 1.8ms였다.
**조용한 오설정이 조용한 오결론을 만든다.**

그래서 값을 고치는 것으로 끝내지 않고 규칙을 코드에 둔다. 개발 PC 프로파일은
`.pt`를 쓰므로 `require_engine`이 없으면 종전대로 동작한다.
"""

from __future__ import annotations

from typing import Callable

ENGINE_SUFFIX = ".engine"


def is_engine(model_path: str) -> bool:
    """TensorRT 엔진 파일인지."""
    return model_path.lower().endswith(ENGINE_SUFFIX)


def backend_label(model_path: str) -> str:
    """시작 로그에 쓸 백엔드 이름."""
    return "TensorRT" if is_engine(model_path) else "PyTorch"


def _export_hint(source_pt: str, imgsz: int | None) -> str:
    size = f" imgsz={imgsz}" if imgsz else ""
    return (
        f"PYTHONPATH=/usr/lib/python3.10/dist-packages .venv/bin/yolo export "
        f"model={source_pt} format=engine half=True device=0{size}"
    )


def resolve_model(
    role: str,
    config_value: str,
    override: str | None,
    *,
    require_engine: bool,
    imgsz: int | None = None,
    exists: Callable[[str], bool] | None = None,
) -> str:
    """쓸 모델 경로를 정하고, 엔진 전용 프로파일이면 위반을 즉시 막는다.

    `override`(CLI `--model`)가 설정값을 이긴다. 그 우선순위 자체는 유지한다 —
    엔진을 다시 굽는 동안 `.pt`로 돌려보는 일이 실제로 필요하다. 다만 그때는
    `require_engine`을 끈 프로파일을 쓰거나 설정을 고치게 만든다. 조용히
    느려지는 것보다 시끄럽게 죽는 것이 낫다.

    `exists`를 주면 엔진 파일이 실제로 있는지도 본다. 엔진은 기기·드라이버
    종속이라 커밋되지 않으므로 SD카드를 다시 굽거나 JetPack을 올린 뒤에는 다시
    구워야 한다. 그때 굽는 명령을 그대로 담아 죽는다 — 이 예외를 처음 보는
    사람은 엔진이 없다는 사실도, 굽는 명령도 모르는 상태다.
    """
    chosen = override or config_value
    if not require_engine:
        return chosen

    source = "--model 인자" if override else "설정의 model 값"
    if not is_engine(chosen):
        hint = _export_hint(chosen, imgsz)
        raise ValueError(
            f"{role} 모델이 TensorRT 엔진이 아니다: {chosen} ({source}). "
            f"이 프로파일은 require_engine: true 이므로 {ENGINE_SUFFIX} 만 받는다. "
            f"PyTorch 로 돌면 프레임당 CPU 가 약 145ms 늘어나는데 경고가 없어 "
            f"측정과 판단이 조용히 틀어진다(S15P11A301-329). "
            f"엔진을 만들려면: {hint}. "
            f"의도적으로 PyTorch 로 돌리려면 require_engine 을 false 로 둔 "
            f"프로파일을 쓴다."
        )

    if exists is not None and not exists(chosen):
        source_pt = chosen[: -len(ENGINE_SUFFIX)] + ".pt"
        hint = _export_hint(source_pt, imgsz)
        raise FileNotFoundError(
            f"{role} 엔진이 없다: {chosen} ({source}). "
            f"엔진은 기기·드라이버 종속이라 저장소에 커밋되지 않는다 — "
            f"이 기기에서 직접 구워야 한다: {hint}"
        )

    return chosen
