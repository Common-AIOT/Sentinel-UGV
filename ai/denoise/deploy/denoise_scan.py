#!/usr/bin/env python3
"""미처리 발견 스캔 → 잡음 제거 워커 실행 (S15P11A301-230).

EC2 크론이 2분 주기로 부른다. 트리거를 "워커 스캔"으로 정한 이유(#228 합의):
백엔드가 워커를 부르는 어떤 형태든(HTTP·docker 소켓·메시지) 상주 프로세스나
위험한 결합이 생기는데, 스캔은 기존 조회 API 만으로 후보를 찾아 백엔드 무변경이다.
실패 재시도도 자연 해결된다 — 다음 주기에 다시 잡힌다.

후보: EVENT_VIDEO 가 AVAILABLE 인데 EVENT_AUDIO_DENOISED 가 없는 발견.
워커 종료 코드 2(재시도 무의미 — 오디오 트랙 없음 등)는 skip 목록에 적어
매 주기 7초(torch import)를 헛쓰지 않는다. 0/1 은 기록하지 않는다 —
0 이면 다음 주기 후보 조건에서 자연히 빠지고, 1 은 다시 시도해야 한다.

사용: python3 denoise_scan.py            (EC2 기본값)
      API=http://localhost:8081 WORKER_API=http://host.docker.internal:8081 python3 denoise_scan.py
"""
import json
import os
import pathlib
import subprocess
import sys
import urllib.request

try:
    import fcntl  # EC2(리눅스) 전용 — 주기 겹침 방지 잠금
except ImportError:  # 윈도 로컬 검증 시에는 잠금 없이 돈다
    fcntl = None

API = os.environ.get("API", "http://localhost:8080")
# 컨테이너 안에서 백엔드로 가는 주소 — compose 네트워크 이름에 묶이지 않도록
# host-gateway 를 쓴다.
WORKER_API = os.environ.get("WORKER_API", "http://host.docker.internal:8080")
IMAGE = os.environ.get("IMAGE", "sentinel-denoise-worker")
STATE_DIR = pathlib.Path(os.environ.get("STATE_DIR", str(pathlib.Path.home() / "denoise")))
SKIP_FILE = STATE_DIR / "skip.txt"
LOCK_FILE = STATE_DIR / "scan.lock"


def get(path):
    with urllib.request.urlopen(API + path, timeout=15) as r:
        return json.load(r)["data"]


def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # 이전 주기가 아직 도는 중이면 겹치지 않고 조용히 빠진다.
    if fcntl is not None:
        lock = open(LOCK_FILE, "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0

    SKIP_FILE.touch(exist_ok=True)
    skip = set(SKIP_FILE.read_text().split())

    for mission in get("/api/v1/missions"):
        for enc in get(f"/api/v1/missions/{mission['id']}/encounters"):
            eid = enc["id"]
            if eid in skip:
                continue
            media = get(f"/api/v1/encounters/{eid}")["media"]
            has_video = any(m["type"] == "EVENT_VIDEO" and m["storageStatus"] == "AVAILABLE" for m in media)
            has_denoised = any(m["type"] == "EVENT_AUDIO_DENOISED" and m["storageStatus"] == "AVAILABLE" for m in media)
            if not has_video or has_denoised:
                continue
            rc = subprocess.run(
                ["docker", "run", "--rm", "--add-host=host.docker.internal:host-gateway",
                 IMAGE, "--encounter", eid, "--api", WORKER_API],
            ).returncode
            print(f"[scan] {eid}: exit {rc}", flush=True)
            done = rc == 0 and any(
                m["type"] == "EVENT_AUDIO_DENOISED" and m["storageStatus"] == "AVAILABLE"
                for m in get(f"/api/v1/encounters/{eid}")["media"])
            # skip 에 적는 두 경우 — 다시 시도해도 결과가 같은 것들:
            #   rc 2       재시도 무의미 (워커 계약)
            #   rc 0 인데 자산이 안 생김 — NO_AUDIO 등 "처리할 것 없음"(워커는 정상 종료가
            #   맞다는 설계라 0 을 준다). 안 적으면 마이크 꺼진 임무를 매 주기 재처리한다.
            if rc == 2 or (rc == 0 and not done):
                with SKIP_FILE.open("a") as f:
                    f.write(eid + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
