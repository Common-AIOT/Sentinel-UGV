"""torchaudio 2.11+에서 DeepFilterNet 0.5.6이 import하는 삭제 API를 복원한다.

DeepFilterNet은 `torchaudio.backend.common.AudioMetaData`를 import하는데, 이
모듈은 torchaudio 2.9에서 비권장이 되고 2.11에서 삭제됐다. DeepFilterNet은
2023-08 이후 릴리스가 없어 스스로 고쳐질 가망이 없다.

sys.modules에 그 모듈 하나만 만들어 넣는다. torchaudio 실물은 건드리지 않으므로
같은 환경의 다른 코드에는 영향이 없다. `df` 를 import하기 전에 호출해야 한다.

    from torchaudio_compat import ensure_backend_module
    ensure_backend_module()
    from df.enhance import enhance, init_df
"""

from __future__ import annotations

import sys
import types


def ensure_backend_module() -> None:
    import torchaudio

    if hasattr(torchaudio, "backend"):
        return

    meta = getattr(torchaudio, "AudioMetaData", None)
    if meta is None:
        class meta:  # noqa: N801 — 원본 클래스명(AudioMetaData) 대역
            pass

    common = types.ModuleType("torchaudio.backend.common")
    common.AudioMetaData = meta
    backend = types.ModuleType("torchaudio.backend")
    backend.common = common
    sys.modules["torchaudio.backend"] = backend
    sys.modules["torchaudio.backend.common"] = common
    torchaudio.backend = backend
