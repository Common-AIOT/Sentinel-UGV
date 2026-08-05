# GPU local extraction server

`Qwen/Qwen3.5-4B`를 non-thinking BF16으로 올리고 기존 3필드 구조화 계약만
OpenAI 호환 API로 제공하는 shadow 서버입니다. 현재 실측 판정은 운영 전환
불합격이므로 기본 파이프라인에서 호출하지 않습니다.

## 설치와 실행

```bash
python3 -m venv .venv
.venv/bin/pip install -r gpu_llm_server/requirements.txt

export LOCAL_LLM_API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export LOCAL_LLM_CUDA_VISIBLE_DEVICES=3
python -m gpu_llm_server
```

기본 주소는 `127.0.0.1:18200`입니다. 외부 공개가 필요하면 GPU 서버에 직접
포트를 열지 말고 인증·TLS가 있는 내부 프록시를 거칩니다. API 키와 프롬프트,
모델 출력은 로그에 남기지 않습니다.

헬스 체크는 `GET /health`, 추론은 Bearer 인증이 필요한
`POST /v1/chat/completions`입니다. 모델 로딩 또는 Schema 검증 실패 시
정상값처럼 보정하지 않고 503으로 닫힙니다.

## 벤치마크

```bash
export LOCAL_LLM_API_KEY=...
python -m bench.local_llm_shadow --dry-run
python -m bench.local_llm_shadow --runs 1 --vram-mib 9160 --confirm-live
```

치명 오분류가 하나라도 있으면 벤치마크는 결과를 저장한 뒤 종료 코드 2를
반환합니다. 2026-08-05 판정과 롤백 원칙은
`docs/measurements/Qwen3.5-4B-로컬-shadow.md`를 참고합니다.
