# 잡음 제거 워커 EC2 배치 (S15P11A301-230)

인계 결정 그대로: **상주하지 않는다.** 크론이 2분마다 스캔 스크립트를 부르고,
후보가 있으면 그때만 `docker run --rm` 으로 워커가 떠서 한 건 처리하고 죽는다.
평소 점유 0, 작업당 약 7초 + 처리 1~3초.

## 구성

| 파일 | 역할 |
|---|---|
| `../Dockerfile` | 워커 이미지 (CPU 전용 torch — CUDA 휠 금지, 인계 지시) |
| `denoise_scan.py` | 후보 스캔 + 워커 실행 + exit 2 skip 목록 관리 |

트리거를 "워커 스캔"으로 정한 이유(#228 합의): 기존 조회 API 만으로 후보를
찾으므로 백엔드 무변경·결합 0 이고, 실패(exit 1)는 다음 주기에 자연 재시도된다.
exit 2(오디오 트랙 없음 등 재시도 무의미)는 `~/denoise/skip.txt` 에 적어
매 주기 torch import 7초를 헛쓰지 않는다.

## EC2 최초 설치 (1회)

```bash
# 1. 레포 받기 (이미 있으면 git pull)
git clone <repo-url> ~/S15P11A301 && cd ~/S15P11A301/ai/denoise

# 2. 이미지 빌드 (CPU torch 포함 약 1.5~2GB, 수 분)
sudo docker build -t sentinel-denoise-worker .

# 3. 스캔 스크립트 배치
mkdir -p ~/denoise && cp deploy/denoise_scan.py ~/denoise/

# 4. 손으로 1회 실행해 확인
sudo python3 ~/denoise/denoise_scan.py

# 5. 크론 등록 (2분 주기)
( sudo crontab -l 2>/dev/null; echo '*/2 * * * * python3 /home/ubuntu/denoise/denoise_scan.py >> /home/ubuntu/denoise/scan.log 2>&1' ) | sudo crontab -
```

- 스캔은 호스트의 `http://localhost:8080`(스프링), 워커 컨테이너는
  `host.docker.internal`(host-gateway) 로 백엔드에 붙는다 — compose 네트워크
  이름에 묶이지 않는다. S3 는 Presigned URL(공개 도메인)이라 자격증명이 없다.
- 메모리: 워커 피크 약 1.0GB, EC2 여유 14GB (인계 실측). 스왑이 0 이므로
  조각 처리 30초 상한을 늘리지 않는다.
- 갱신 배포: `git pull && sudo docker build -t sentinel-denoise-worker .` 만.

## 운용 확인

```bash
tail -f ~/denoise/scan.log     # [scan] <encounterId>: exit 0 이 성공
cat ~/denoise/skip.txt         # 재시도 무의미로 제낀 발견들
```
