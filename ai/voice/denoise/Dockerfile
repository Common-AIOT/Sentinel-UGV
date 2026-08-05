# 잡음 제거 워커 이미지 (S15P11A301-230).
#
# 상주하지 않는다 — 크론 스캔(deploy/denoise_scan.py)이 작업마다
# `docker run --rm` 로 띄우고, 워커는 한 건 처리 후 종료 코드로 결과를 알린다
# (0 성공 / 1 재시도 가능 / 2 재시도 무의미).
FROM python:3.11-slim

WORKDIR /app

# CPU 전용 torch 를 먼저 설치한다. 기본 인덱스의 Linux 휠은 CUDA 런타임을 끌고 와
# 이미지가 5GB를 넘는데 EC2 에는 GPU 가 없어 전부 죽은 무게다(인계 지시).
# requirements.txt 의 torch/torchaudio 는 이미 충족돼 재설치되지 않는다.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch torchaudio

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# DeepFilterNet init_df() 가 로깅용 커밋 해시를 찾으려고 git 을 실행한다.
# git 이 아예 없으면 FileNotFoundError 로 죽지만, "git 은 있는데 저장소가 아님"
# (CalledProcessError)은 라이브러리가 정상 처리한다. 실제 git(~100MB)을 넣는 대신
# 항상 실패하는 심으로 그 경로를 태운다.
RUN printf '#!/bin/sh\nexit 1\n' > /usr/local/bin/git && chmod +x /usr/local/bin/git

COPY torchaudio_compat.py enhance_media.py worker.py ./

# 설치 검증 — 무거운 모델 로딩 없이 import 경로만 확인한다.
RUN python worker.py --help > /dev/null

ENTRYPOINT ["python", "worker.py"]
