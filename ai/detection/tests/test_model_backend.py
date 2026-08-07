"""엔진 전용 강제 시험 (S15P11A301-329).

이 규칙이 막는 것은 기능 오류가 아니라 **조용한 성능 저하**다. PyTorch로
떨어져도 결과는 정상으로 나오고 로그도 조용하다. 그래서 시험으로 못박는다.
"""

from __future__ import annotations

import pytest

from src.model_backend import backend_label, is_engine, resolve_model


def test_엔진_판정은_확장자로_한다():
    assert is_engine("models/yolo26n.engine")
    assert is_engine("MODELS/YOLO26N.ENGINE")
    assert not is_engine("models/yolo26n.pt")
    assert not is_engine("models/yolo26n.engine.pt")


def test_백엔드_이름():
    assert backend_label("a.engine") == "TensorRT"
    assert backend_label("a.pt") == "PyTorch"


def test_강제가_없으면_설정값을_그대로_쓴다():
    assert resolve_model("탐지", "models/x.pt", None, require_engine=False) == "models/x.pt"


def test_인자가_설정을_이긴다():
    # 엔진을 다시 굽는 동안 .pt 로 돌려보는 일이 실제로 필요하므로 우선순위
    # 자체는 유지한다. 막는 것은 엔진 전용 프로파일에서의 위반뿐이다.
    got = resolve_model("탐지", "models/a.engine", "models/b.engine", require_engine=True)
    assert got == "models/b.engine"


def test_엔진_전용에서_설정값이_pt면_죽는다():
    with pytest.raises(ValueError) as exc:
        resolve_model("탐지", "models/yolo26n.pt", None, require_engine=True)
    message = str(exc.value)
    # 무엇이 문제인지, 어디서 왔는지, 어떻게 고치는지가 모두 있어야 한다.
    assert "models/yolo26n.pt" in message
    assert "설정의 model 값" in message
    assert "yolo export" in message


def test_엔진_전용에서_인자가_pt면_죽고_출처를_밝힌다():
    with pytest.raises(ValueError) as exc:
        resolve_model("pose", "models/a.engine", "models/b.pt", require_engine=True)
    message = str(exc.value)
    assert "models/b.pt" in message
    # 설정은 엔진인데 인자가 어겼다는 것을 알려야 한다. 출처를 안 밝히면
    # 설정 파일만 보고 "엔진인데 왜?" 하며 시간을 쓴다.
    assert "--model 인자" in message


def test_엔진_전용에서_엔진이면_통과한다():
    assert (
        resolve_model("pose", "models/p.engine", None, require_engine=True)
        == "models/p.engine"
    )


def test_엔진_전용에서_엔진_파일이_없으면_굽는_명령을_준다():
    # 설정 기본값이 .pt였던 원래 근거가 "엔진을 굽지 않은 기기에서 clone 직후
    # 실행이 깨진다"였다. 그 우려를 없앤 것이 아니라 깨지는 방식을 바꾼 것이므로,
    # 메시지에 굽는 명령이 반드시 있어야 한다.
    with pytest.raises(FileNotFoundError) as exc:
        resolve_model(
            "pose",
            "models/yolo26n-pose.engine",
            None,
            require_engine=True,
            imgsz=320,
            exists=lambda _: False,
        )
    message = str(exc.value)
    assert "models/yolo26n-pose.engine" in message
    # 굽는 원본은 .pt 이고 imgsz 가 설정과 같아야 한다.
    assert "model=models/yolo26n-pose.pt" in message
    assert "imgsz=320" in message
    assert "커밋되지 않는다" in message


def test_엔진_파일이_있으면_통과한다():
    assert (
        resolve_model(
            "탐지", "models/a.engine", None, require_engine=True, exists=lambda _: True
        )
        == "models/a.engine"
    )
