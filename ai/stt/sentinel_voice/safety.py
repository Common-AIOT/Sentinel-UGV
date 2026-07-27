# safety.py
"""
LLM은 '진단'하지 않고 '정보 구조화'만 한다는 원칙을 강제하는 가드.
 - is_valid_stt : STT 환각/무음/프롬프트복사 컷
 - strip_hallucinated : 발화에 없는 부위/위험요소 제거
 - coerce_defaults : enum 밖 값/타입 오류를 안전값으로 보정
 - triage_rule : LLM 자유판단이 아니라 규칙으로 등급 산출(재현·설명 가능)
"""

ENUMS = {
    "consciousness": {"명료", "혼미", "통증반응", "무반응", "미확인"},
    "speech":        {"완전문장", "단어만", "신음만", "불가", "미확인"},
    "can_move":      {"가능", "불가", "미확인"},
}


def is_valid_stt(text, no_speech_prob, prompt_text=""):
    """STT 출력이 유효한 발화인지 판정. prompt_text 에는 whisper initial_prompt
    (config.STT_PROMPT, 도메인 프라이밍 키워드)를 넘긴다.

    프롬프트 복사(echo) 판정은 '부분문자열'이 아니라 '키워드 다수 동시 등장'으로 한다.
    initial_prompt 는 요구조자가 실제로 외칠 법한 단어("살려주세요" 등)로 구성되므로,
    부분문자열 검사는 정상 발화("살려주세요")를 환각으로 오탐한다. whisper 의 실제
    echo 환각은 프라이밍 목록을 통째로 되뱉는 형태이므로, 프라이밍 키워드가 3개 이상
    한꺼번에 나타날 때만 복사로 본다(단어 하나는 정상 통과)."""
    if not text or not text.strip():
        return False, "빈 출력"
    if no_speech_prob > 0.7:
        return False, "무음확률 높음"
    toks = text.split()
    if toks and max(toks.count(t) for t in set(toks)) >= 4:
        return False, "반복 환각"
    norm = lambda x: x.replace(" ", "").replace(",", "")
    primes = {norm(k) for k in prompt_text.replace(",", " ").split() if k.strip()}
    if primes:
        hit = sum(1 for k in primes if k and k in norm(text))
        if hit >= 3 or (len(primes) >= 2 and hit == len(primes)):
            return False, "프롬프트 복사"
    return True, "ok"


def strip_hallucinated(info, source_text):
    def clean(items):
        return [x for x in (items or [])
                if x and x != "미확인" and (x in source_text or x[:2] in source_text)]
    info["hazard"] = clean(info.get("hazard"))
    info["pain_location"] = clean(info.get("pain_location"))
    return info


def coerce_defaults(info):
    for k, allowed in ENUMS.items():
        if info.get(k) not in allowed:      # "미홐정" 같은 글리치 → 미확인
            info[k] = "미확인"
    v = info.get("additional_victims")
    info["additional_victims"] = v if isinstance(v, int) else (int(v) if str(v).isdigit() else 0)
    for key in ("pain_location", "hazard"):
        if not isinstance(info.get(key), list):
            info[key] = []
    if not isinstance(info.get("raw_note"), str):
        info["raw_note"] = ""
    return info


def triage_rule(info):
    """규칙 기반 색상 등급. 최종 판단은 관제의 사람이 한다(참고값)."""
    c = info.get("consciousness", "미확인")
    s = info.get("speech", "미확인")
    move = info.get("can_move", "미확인")
    if c in ("무반응", "통증반응"):
        return "적색(즉시)"
    if s in ("신음만", "불가"):
        return "적색(즉시)"
    if c == "혼미":
        return "황색(응급)"
    if move == "불가":
        return "황색(응급)"
    if s == "단어만":
        return "황색(응급)"
    if c == "명료" and move == "가능":
        return "녹색(경증)"
    return "미확인"
