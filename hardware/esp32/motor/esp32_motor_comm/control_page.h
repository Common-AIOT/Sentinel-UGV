// 모바일 수동 조종 페이지 (S15P11A301-298, UI 개편 S15P11A301-312).
//
// **멈추는 경로는 전부 그대로다** - pointerdown/up/cancel/lostpointercapture 홀드
// deadman, 긴급 정지 버튼, Space/Escape, window.blur·visibilitychange·pagehide,
// touch-action:none, 50ms 재전송 + 단일 in-flight 가드, 유휴 시 `/manual/state`
// 폴링. 그 코드는 검증된 안전 장치이며 손댈 이유가 없다.
//
// 바뀐 것은 **입력 위젯과 화면**뿐이다. 5버튼 D-패드를 터치 조이스틱으로 바꿨다.
//
//  1. D-패드에서는 좌/우가 전·후진 홀드 중에만 눌리게 `disabled` 로 막아야 했다.
//     조이스틱은 그 규칙이 기하로 표현된다 - 스틱을 좌우 수평으로만 밀면 전·후진
//     성분이 0 이라 애초에 조향 명령이 나가지 않는다(§34-2). 막을 것이 없다.
//  2. `ang` 은 `|lin| >= 100` 일 때만 싣는다. 100 은 우연이 아니라
//     `STEERING_MIN_DRIVE_MMPS(30) / MANUAL_MAX_DRIVE_MMPS(300) x 1000` 이다.
//     펌웨어가 어차피 거부할 조향을 보내면 그 거부가
//     `FAULT_STEERING_COMMAND_INVALID`(bit 14)로 올라가 그 비트의 의미를 파괴한다.
//     화면의 「조향 활성」 표시도 이 경계와 같은 값을 봐야 거짓말을 하지 않는다.
//  3. 「좌우 최대 조향각」 슬라이더는 **클라이언트 전용**이다. 속도 슬라이더와
//     똑같이 `ang`/`lin` 을 스케일링할 뿐이며 보드에 별도 엔드포인트가 없다.
//     상한은 `STEERING_MAX_MDEG`(30°)와 같은 값이어야 한다 - 어긋나면 슬라이더 끝이
//     실제 δ_max 와 달라지고 그것은 화면에 보이지 않는다.
//  4. 상태줄·상태표는 여전히 응답 JSON 으로 렌더한다. 「관제 정지 수신」·「방향 전환
//     대기」처럼 사람이 무엇을 하면 되는지가 화면에 있어야 한다.
//  5. `file:` 로 열면 오프라인 미리보기로 동작한다. 보드 없이 레이아웃을 보기 위한
//     것이며 `location.protocol` 로만 켜진다 - 서빙된 페이지는 항상 `http:` 다.
//
// 좌 = +(CCW, REP-103). 조이스틱 dx 는 우가 +이므로 `ang` 은 부호를 뒤집는다.
#pragma once

#include <Arduino.h>

const char CONTROL_PAGE[] PROGMEM = R"rawliteral(
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="theme-color" content="#07111f">
  <title>Sentinel 수동 조종</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #07111f;
      --panel: #101d2f;
      --panel-2: #14243a;
      --line: #29405e;
      --text: #f3f7fc;
      --muted: #9db0c8;
      --cyan: #22d3ee;
      --blue: #3b82f6;
      --violet: #7c3aed;
      --yellow: #facc15;
      --orange: #fb923c;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      padding: max(16px, env(safe-area-inset-top)) 16px
               max(24px, env(safe-area-inset-bottom));
      background:
        radial-gradient(circle at 50% -10%, #16365d 0, transparent 40%),
        var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, "Noto Sans KR", sans-serif;
      overscroll-behavior: none;
    }
    main { width: min(100%, 880px); margin: 0 auto; }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 16px;
    }
    h1 { margin: 0; font-size: clamp(1.35rem, 4vw, 2rem); }
    .badge {
      flex: 0 0 auto;
      padding: 7px 11px;
      border: 1px solid #28614a;
      border-radius: 999px;
      background: #123829;
      color: #86efac;
      font-size: .78rem;
      font-weight: 800;
    }
    .badge.offline { border-color: #72591a; background: #3e310e; color: #fde68a; }
    .badge.error { border-color: #7f2d2d; background: #431919; color: #fca5a5; }
    .notice {
      display: none;
      margin: 0 0 14px;
      padding: 11px 13px;
      border: 1px solid #72591a;
      border-radius: 12px;
      background: #3e310e;
      color: #fde68a;
      font-size: .88rem;
      line-height: 1.45;
    }
    .grid { display: grid; grid-template-columns: 1.1fr .9fr; gap: 16px; }
    .panel {
      padding: 18px;
      border: 1px solid var(--line);
      border-radius: 20px;
      background: color-mix(in srgb, var(--panel) 94%, transparent);
      box-shadow: 0 18px 45px #0005;
    }
    .panel h2 { margin: 0 0 6px; font-size: 1.08rem; }
    .sub { margin: 0 0 12px; color: var(--muted); font-size: .86rem; line-height: 1.45; }
    .sub b { color: #dbe7f5; }
    #state {
      margin: 0 0 10px;
      color: var(--yellow);
      font-size: 1.15rem;
      font-weight: 800;
      min-height: 1.4em;
    }
    .joystick-wrap { display: grid; place-items: center; padding: 2px 0 12px; }
    #joystick {
      position: relative;
      width: min(72vw, 330px);
      aspect-ratio: 1;
      border: 1px solid #3d5879;
      border-radius: 50%;
      overflow: hidden;
      touch-action: none;
      user-select: none;
      -webkit-user-select: none;
      background:
        radial-gradient(circle, #1b304b 0 9%, transparent 10%),
        linear-gradient(90deg, transparent 49.6%, #3d5879 50%, transparent 50.4%),
        linear-gradient(transparent 49.6%, #3d5879 50%, transparent 50.4%),
        radial-gradient(circle, #172a42 0 54%, #101d2f 55% 100%);
      box-shadow: inset 0 0 35px #0007, 0 8px 24px #0005;
    }
    #joystick::after {
      content: "";
      position: absolute;
      inset: 12%;
      border: 1px dashed #3d5879;
      border-radius: 50%;
      pointer-events: none;
    }
    .axis-label {
      position: absolute;
      z-index: 1;
      color: #87a0bd;
      font-size: .74rem;
      font-weight: 800;
      pointer-events: none;
    }
    .axis-label.forward { top: 7%; left: 50%; transform: translateX(-50%); }
    .axis-label.backward { bottom: 7%; left: 50%; transform: translateX(-50%); }
    .axis-label.left { left: 7%; top: 50%; transform: translateY(-50%); }
    .axis-label.right { right: 7%; top: 50%; transform: translateY(-50%); }
    #knob {
      position: absolute;
      z-index: 3;
      left: 50%;
      top: 50%;
      width: 27%;
      aspect-ratio: 1;
      border: 3px solid #a5f3fc;
      border-radius: 50%;
      background: radial-gradient(circle at 35% 30%, #67e8f9, #0284c7 62%, #075985);
      box-shadow: 0 8px 25px #0008, 0 0 24px #22d3ee55;
      transform: translate(-50%, -50%);
      pointer-events: none;
    }
    .live-values {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-top: 4px;
    }
    .metric { padding: 11px; border-radius: 12px; background: var(--panel-2); text-align: center; }
    .metric span { display: block; color: var(--muted); font-size: .75rem; }
    .metric strong { display: block; margin-top: 2px; color: var(--cyan); font-size: 1.05rem; }
    .metric.off strong { color: var(--muted); }
    label { display: block; margin-bottom: 9px; color: #dbe7f5; font-weight: 750; }
    .range-line { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    output { color: var(--cyan); font-size: 1.2rem; font-weight: 850; }
    input[type="range"] { width: 100%; accent-color: var(--cyan); }
    .speed-box, .steer-box { padding: 15px; border-radius: 15px; background: var(--panel-2); }
    .steer-box { margin-top: 14px; }
    .input-row { display: grid; grid-template-columns: 1fr auto; gap: 9px; margin-bottom: 6px; }
    input[type="number"] {
      min-width: 0;
      width: 100%;
      padding: 12px;
      border: 1px solid #3d5879;
      border-radius: 11px;
      outline: none;
      background: #081423;
      color: var(--text);
      font-size: 1rem;
      font-weight: 750;
    }
    input[type="number"]:focus { border-color: var(--cyan); box-shadow: 0 0 0 3px #22d3ee22; }
    button {
      min-height: 46px;
      padding: 10px 15px;
      border: 0;
      border-radius: 11px;
      color: white;
      font: inherit;
      font-weight: 800;
      cursor: pointer;
      touch-action: manipulation;
    }
    button:active { transform: translateY(1px); }
    .apply { background: var(--blue); }
    .takeover { width: 100%; margin-top: 14px; background: var(--violet); }
    .takeover.urgent { box-shadow: 0 0 0 3px #7c3aed55, 0 8px 22px #7c3aed44; }
    .stop {
      width: 100%;
      min-height: 58px;
      margin-top: 9px;
      background: linear-gradient(135deg, #ef4444, #b91c1c);
      font-size: 1.06rem;
      box-shadow: 0 8px 22px #ef444433;
    }
    .status-card { margin-top: 14px; padding: 13px; border: 1px solid var(--line); border-radius: 14px; }
    .status-row { display: flex; justify-content: space-between; gap: 12px; padding: 5px 0; color: var(--muted); font-size: .86rem; }
    .status-row strong { color: var(--text); text-align: right; }
    #message { min-height: 1.4em; margin: 12px 2px 0; color: var(--orange); font-size: .83rem; line-height: 1.4; }
    .hint { margin: 12px 0 0; color: #8196af; font-size: .76rem; line-height: 1.5; }
    @media (max-width: 720px) {
      .grid { grid-template-columns: 1fr; }
      .panel { padding: 16px; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Sentinel 수동 조종</h1>
      <div id="connectionBadge" class="badge">연결 확인 중</div>
    </header>
    <p id="previewNotice" class="notice">
      보드 없이 보는 오프라인 미리보기입니다. 조이스틱과 모든 입력은 화면에서만 동작합니다.
    </p>

    <div class="grid">
      <section class="panel">
        <h2>터치 조이스틱</h2>
        <p class="sub">누르고 있는 동안만 움직이고 손을 떼면 바로 멈춥니다.
          위·아래는 전진/후진, 좌·우는 조향입니다.
          <b>전진·후진 중에만 조향됩니다</b> — 제자리 회전은 할 수 없습니다.</p>
        <div id="state">정지</div>
        <div class="joystick-wrap">
          <div id="joystick" role="application" aria-label="차량 조향 및 주행 조이스틱">
            <span class="axis-label forward">전진</span>
            <span class="axis-label backward">후진</span>
            <span class="axis-label left">좌회전</span>
            <span class="axis-label right">우회전</span>
            <div id="knob"></div>
          </div>
        </div>
        <div class="live-values">
          <div class="metric off" id="steerMetric"><span>조향</span><strong id="steerValue">중앙 0°</strong></div>
          <div class="metric off" id="driveMetric"><span>주행</span><strong id="throttleValue">정지 0%</strong></div>
        </div>
      </section>

      <section class="panel">
        <h2>세부 설정</h2>
        <p class="sub">속도와 조향 폭은 이 화면에서만 스케일링합니다. 보드의 상한(0.30&nbsp;m/s · 30°)을 넘지 못합니다.</p>

        <div class="speed-box">
          <div class="range-line">
            <label for="speed">주행 최대 속도</label>
            <output id="speedValue" for="speed">60%</output>
          </div>
          <input id="speed" type="range" min="20" max="100" step="1" value="60">
        </div>

        <div class="steer-box">
          <label for="steerLimitInput">좌우 최대 조향각 (°)</label>
          <div class="input-row">
            <input id="steerLimitInput" type="number" inputmode="numeric" min="5" max="30" step="1" value="30">
            <button class="apply" id="applySteerLimit">적용</button>
          </div>
          <input id="steerLimitRange" type="range" min="5" max="30" step="1" value="30" aria-label="좌우 최대 조향각 슬라이더">
        </div>

        <button class="takeover" id="takeover">제어권 가져오기</button>
        <button class="stop" id="stop">■ 긴급 정지</button>

        <div class="status-card">
          <div class="status-row"><span>보드 상태</span><strong id="boardState">확인 중</strong></div>
          <div class="status-row"><span>주행 출력</span><strong id="driveState">정지</strong></div>
          <div class="status-row"><span>조향각</span><strong id="steerState">조향 불가</strong></div>
          <div class="status-row"><span>세션</span><strong id="sessionState">미발급</strong></div>
        </div>
        <p id="message"></p>
        <p class="hint">조향 링키지와 바퀴를 지면에서 띄운 상태로 먼저 시험하세요.
          스틱을 놓거나 브라우저가 명령 전송을 멈추면 250ms 안에 자동 정지합니다.
          Space·Esc 도 긴급 정지입니다.</p>
      </section>
    </div>
  </main>

  <script>
  (function () {
    'use strict';

    var previewMode = location.protocol === 'file:';

    // 250ms TTL 에 대해 50ms 재전송이면 한 번 손실돼도 네 번 더 기회가 있다.
    var SEND_INTERVAL_MS = 50;
    var TTL_MS = 250;
    var IDLE_POLL_MS = 500;

    // 펌웨어 상수의 사본이다. 갈라지면 화면만 거짓말을 한다.
    var MANUAL_MAX_DRIVE_MMPS = 300;   // mode_arbiter.h
    var STEERING_MAX_DEG = 30;         // steering_limits.h STEERING_MAX_MDEG
    // STEERING_MIN_DRIVE_MMPS(30) / MANUAL_MAX_DRIVE_MMPS(300) x 1000.
    // 이 아래에서는 펌웨어가 조향 요청 자체를 하지 않는다(§34-2).
    var STEER_MIN_LIN = 100;

    var sid = null;
    var seq = 0;
    var inFlight = false;      // 단일 in-flight 가드. 없으면 요청이 쌓인다.
    var driveTimer = null;
    var idleTimer = null;

    var activePointerId = null;
    var steerPercent = 0;      // -100(좌) .. 100(우)
    var throttlePercent = 0;   // -100(후진) .. 100(전진)
    var speedPercent = 60;
    var steerLimitDeg = STEERING_MAX_DEG;

    var badgeEl = document.getElementById('connectionBadge');
    var stateEl = document.getElementById('state');
    var messageEl = document.getElementById('message');
    var takeoverEl = document.getElementById('takeover');
    var joystickEl = document.getElementById('joystick');
    var knobEl = document.getElementById('knob');
    var steerMetricEl = document.getElementById('steerMetric');
    var driveMetricEl = document.getElementById('driveMetric');
    var steerValueEl = document.getElementById('steerValue');
    var throttleValueEl = document.getElementById('throttleValue');
    var boardStateEl = document.getElementById('boardState');
    var driveStateEl = document.getElementById('driveState');
    var steerStateEl = document.getElementById('steerState');
    var sessionStateEl = document.getElementById('sessionState');
    var speedEl = document.getElementById('speed');
    var speedValueEl = document.getElementById('speedValue');
    var steerLimitInputEl = document.getElementById('steerLimitInput');
    var steerLimitRangeEl = document.getElementById('steerLimitRange');

    var STATE_LABEL = {
      0: '부팅 중', 1: '대기', 2: '준비', 3: '수동 권한 보유',
      4: '자율 주행 중', 5: '정지 중', 6: '비상 정지', 7: '결함 정지'
    };

    function clamp(value, low, high) {
      return value < low ? low : value > high ? high : value;
    }

    function linMilli() {
      // throttle(-100..100) x speed(20..100) / 10 = -1000..1000
      return clamp(Math.round(throttlePercent * speedPercent / 10), -1000, 1000);
    }

    // 좌 = +(CCW, REP-103). 조이스틱은 우가 +이므로 부호를 뒤집는다.
    // 주행 성분이 조향 최소 속도에 못 미치면 아예 싣지 않는다.
    function angMilli() {
      if (Math.abs(linMilli()) < STEER_MIN_LIN) return 0;
      return clamp(Math.round(-steerPercent * 10 * steerLimitDeg / STEERING_MAX_DEG),
                   -1000, 1000);
    }

    function holding() { return activePointerId !== null; }

    // ---- 화면 렌더 ----

    function setConnection(mode) {
      badgeEl.className = 'badge' +
        (mode === 'offline' ? ' offline' : mode === 'error' ? ' error' : '');
      badgeEl.textContent = mode === 'offline' ? '오프라인 미리보기'
        : mode === 'error' ? '보드 연결 끊김' : '보드 연결됨';
    }

    function renderLiveValues() {
      var lin = linMilli();
      var steerable = Math.abs(lin) >= STEER_MIN_LIN;
      var deg = Math.round(Math.abs(steerPercent) * steerLimitDeg / 100);

      // 스틱을 옆으로 밀고 있는데 주행 성분이 모자랄 때만 「조향 불가」다.
      // 중립에서까지 그렇게 쓰면 경고가 배경 소음이 되어 아무도 안 읽는다.
      if (deg < 1) {
        steerValueEl.textContent = '중앙 0°';
        steerMetricEl.className = 'metric off';
      } else if (!steerable) {
        steerValueEl.textContent = '조향 불가';
        steerMetricEl.className = 'metric off';
      } else {
        steerValueEl.textContent = (steerPercent < 0 ? '좌 ' : '우 ') + deg + '°';
        steerMetricEl.className = 'metric';
      }

      var mmps = Math.round(Math.abs(lin) * MANUAL_MAX_DRIVE_MMPS / 1000);
      if (mmps < 1) {
        throttleValueEl.textContent = '정지 0%';
        driveMetricEl.className = 'metric off';
      } else {
        throttleValueEl.textContent = (throttlePercent > 0 ? '전진 ' : '후진 ') +
          Math.round(Math.abs(lin) / 10) + '% · ' + (mmps / 1000).toFixed(2) + ' m/s';
        driveMetricEl.className = 'metric';
      }
    }

    function render(body) {
      if (!body) return;

      if (body.lat && body.dm && body.pwm !== 0) {
        stateEl.textContent = throttlePercent < 0 ? '후진 중' : '전진 중';
      } else if (body.lat) {
        stateEl.textContent = STATE_LABEL[body.st] || '수동 권한 보유';
      } else {
        stateEl.textContent = STATE_LABEL[body.st] || '정지';
      }

      var notes = [];
      if (body.res && body.res !== 'ACCEPTED') notes.push(body.res);
      if (body.rearm) notes.push('관제 정지 수신 — 손을 떼고 다시 누르세요');
      if (body.dz) notes.push('방향 전환 대기 (약 0.5초)');
      if (body.nosteer && steerPercent !== 0 && holding()) {
        notes.push('조향 불가 — 전진·후진 중에만 꺾입니다');
      }
      if (body.ff) notes.push('결함 비트 0x' + body.ff.toString(16));
      if (!body.wifi) notes.push('WiFi 끊김');
      messageEl.textContent = notes.join(' · ');

      boardStateEl.textContent = STATE_LABEL[body.st] || ('상태 ' + body.st);
      driveStateEl.textContent = body.pwm ? ('PWM ' + body.pwm) : '정지';
      if (body.nosteer) {
        steerStateEl.textContent = '조향 불가 (정지 중)';
      } else {
        var mdeg = body.sdeg || 0;
        steerStateEl.textContent = (mdeg > 0 ? '좌 ' : mdeg < 0 ? '우 ' : '중앙 ') +
          Math.abs(mdeg / 1000).toFixed(1) + '°';
      }
      sessionStateEl.textContent = sid === null ? '미발급'
        : (body.lat ? '보유 · ' : '대기 · ') + sid;

      // 래치를 잃었으면(관제가 자율로 되돌렸다) 세션도 사라진다.
      if (body.sid === '00000000' && sid !== null) {
        sid = null;
        takeoverEl.classList.add('urgent');
        sessionStateEl.textContent = '회수됨';
        messageEl.textContent = '제어권이 회수되었습니다. 다시 가져오세요.';
      }
    }

    // ---- 통신 ----

    function request(url) {
      if (previewMode) { render(previewBody()); return Promise.resolve(null); }
      if (inFlight) return Promise.resolve(null);
      inFlight = true;
      return fetch(url, { cache: 'no-store' })
        .then(function (r) { return r.json().catch(function () { return null; }); })
        .then(function (body) { setConnection('connected'); render(body); return body; })
        .catch(function () {
          setConnection('error');
          messageEl.textContent = '연결 끊김 — 바퀴는 자동으로 멈춥니다';
          return null;
        })
        .then(function (body) { inFlight = false; return body; });
    }

    function nextSeq() { seq = (seq + 1) & 0xFFFF; return seq; }

    function sendDrive() {
      if (sid === null) return;
      request('/manual/drive?sid=' + sid + '&seq=' + nextSeq() +
              '&dm=' + (holding() ? 1 : 0) +
              '&lin=' + linMilli() + '&ang=' + angMilli() +
              '&ttl=' + TTL_MS);
    }

    // 부작용 없는 조회다. **/manual/drive 를 폴링에 쓰면 안 된다** — 그러면
    // lastManualInputMs 가 계속 갱신되어 관제의 500ms 신선도 창이 영구히 닫히지
    // 않고 로봇을 되찾을 수 없다.
    function pollState() { request('/manual/state'); }

    function startSending() {
      if (idleTimer !== null) { clearInterval(idleTimer); idleTimer = null; }
      sendDrive();
      if (driveTimer === null) driveTimer = setInterval(sendDrive, SEND_INTERVAL_MS);
    }

    function startPolling() {
      if (driveTimer !== null) { clearInterval(driveTimer); driveTimer = null; }
      if (idleTimer === null) idleTimer = setInterval(pollState, IDLE_POLL_MS);
    }

    // ---- 조이스틱 ----

    function centerKnob() {
      steerPercent = 0;
      throttlePercent = 0;
      knobEl.style.transform = 'translate(-50%, -50%)';
      renderLiveValues();
    }

    function updateFromPointer(event) {
      var rect = joystickEl.getBoundingClientRect();
      var maxRadius = rect.width * 0.37;
      var dx = event.clientX - (rect.left + rect.width / 2);
      var dy = event.clientY - (rect.top + rect.height / 2);
      var distance = Math.hypot(dx, dy);
      if (distance > maxRadius) {
        dx = dx / distance * maxRadius;
        dy = dy / distance * maxRadius;
      }

      // 조향 방향과 주행 속도를 분리한다. 원의 바깥쪽을 유지한 채 조금만 전방/후방
      // 영역으로 들어가도 조향각과 관계없이 최대 주행 명령을 유지한다. 완전 좌우
      // 수평에서는 주행하지 않으며, 경계 5% 구간만 부드럽게 0~100%로 전환하여 좌우
      // 조작 노이즈로 인한 출발을 막는다.
      var travelRatio = Math.min(distance / maxRadius, 1);
      var dominantAxis = Math.max(Math.abs(dx), Math.abs(dy));
      if (dominantAxis < 0.001) {
        steerPercent = 0;
        throttlePercent = 0;
      } else {
        var axisScale = travelRatio * 100 / dominantAxis;
        steerPercent = clamp(Math.round(dx * axisScale), -100, 100);
        var driveDirection = dy < 0 ? 1 : dy > 0 ? -1 : 0;
        var directionBlend = Math.min(Math.abs(dy) / (maxRadius * 0.05), 1);
        throttlePercent = clamp(
          Math.round(driveDirection * travelRatio * directionBlend * 100), -100, 100);
      }
      knobEl.style.transform =
        'translate(calc(-50% + ' + dx + 'px), calc(-50% + ' + dy + 'px))';
      renderLiveValues();
    }

    joystickEl.addEventListener('pointerdown', function (event) {
      if (activePointerId !== null) return;
      activePointerId = event.pointerId;
      joystickEl.setPointerCapture(event.pointerId);
      updateFromPointer(event);
      // 세션이 없으면 sendDrive 가 조용히 아무것도 안 한다. 스틱만 움직이고 차는
      // 서 있는 상태를 설명 없이 두면 안 된다.
      if (sid === null) {
        takeoverEl.classList.add('urgent');
        messageEl.textContent = '제어권이 없습니다 — 「제어권 가져오기」를 먼저 누르세요';
      }
      startSending();
    });
    joystickEl.addEventListener('pointermove', function (event) {
      if (event.pointerId === activePointerId) updateFromPointer(event);
    });

    function releasePointer(event) {
      if (event && event.pointerId !== activePointerId) return;
      activePointerId = null;
      centerKnob();
      // 손을 뗀 것을 즉시 알린다. TTL 만료를 기다리지 않는다.
      if (driveTimer !== null) { clearInterval(driveTimer); driveTimer = null; }
      if (sid !== null && !previewMode) {
        request('/manual/drive?sid=' + sid + '&seq=' + nextSeq() +
                '&dm=0&lin=0&ang=0&ttl=' + TTL_MS);
      }
      startPolling();
    }

    joystickEl.addEventListener('pointerup', releasePointer);
    joystickEl.addEventListener('pointercancel', releasePointer);
    joystickEl.addEventListener('lostpointercapture', releasePointer);

    // ---- 정지 ----

    function stopAll() {
      activePointerId = null;
      centerKnob();
      if (driveTimer !== null) { clearInterval(driveTimer); driveTimer = null; }
      startPolling();
      if (previewMode) { render(previewBody()); return; }
      // 세션 불일치에도 수락되는 경로다. 누구의 정지든 항상 존중한다.
      fetch('/manual/stop?sid=' + (sid === null ? '0' : sid) + '&seq=' + nextSeq(),
            { cache: 'no-store' }).catch(function () {});
    }

    document.getElementById('stop').addEventListener('click', stopAll);
    document.addEventListener('keydown', function (event) {
      if (event.key === ' ' || event.key === 'Escape') { event.preventDefault(); stopAll(); }
    });
    window.addEventListener('blur', stopAll);
    window.addEventListener('pagehide', stopAll);
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) stopAll();
    });

    // ---- 슬라이더 (둘 다 클라이언트 전용 스케일링) ----

    speedEl.addEventListener('input', function () {
      speedPercent = parseInt(speedEl.value, 10);
      speedValueEl.value = speedPercent + '%';
      speedValueEl.textContent = speedPercent + '%';
      renderLiveValues();
    });

    function commitSteerLimit(value) {
      var low = parseInt(steerLimitInputEl.min, 10);
      var high = parseInt(steerLimitInputEl.max, 10);
      if (!isFinite(value) || value < low || value > high) {
        messageEl.textContent = low + '–' + high + '° 범위의 정수를 입력하세요.';
        return;
      }
      steerLimitDeg = value;
      steerLimitInputEl.value = value;
      steerLimitRangeEl.value = value;
      renderLiveValues();
    }

    steerLimitRangeEl.addEventListener('input', function () {
      commitSteerLimit(parseInt(steerLimitRangeEl.value, 10));
    });
    document.getElementById('applySteerLimit').addEventListener('click', function () {
      commitSteerLimit(parseInt(steerLimitInputEl.value, 10));
    });
    steerLimitInputEl.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') commitSteerLimit(parseInt(steerLimitInputEl.value, 10));
    });

    // ---- 세션 ----

    function acquire() {
      if (previewMode) { sid = 'preview1'; render(previewBody()); return; }
      fetch('/manual/session', { cache: 'no-store' })
        .then(function (r) {
          return r.json().catch(function () { return null; }).then(function (body) {
            setConnection('connected');
            if (r.status === 409) {
              takeoverEl.classList.add('urgent');
              messageEl.textContent = '다른 조종자가 사용 중입니다. 잠시 뒤 다시 시도하세요.';
              return;
            }
            if (!body || !body.sid || body.sid === '00000000') {
              messageEl.textContent = '세션 발급 실패';
              return;
            }
            sid = body.sid;
            seq = 0;
            takeoverEl.classList.remove('urgent');
            render(body);
          });
        })
        .catch(function () {
          setConnection('error');
          messageEl.textContent = '로봇에 연결할 수 없습니다';
        });
    }

    takeoverEl.addEventListener('click', acquire);

    // ---- 오프라인 미리보기 ----

    function previewBody() {
      var lin = linMilli();
      var ang = angMilli();
      return {
        st: 3, lat: 1, dm: holding() ? 1 : 0, rearm: 0, dz: 0,
        nosteer: Math.abs(lin) >= STEER_MIN_LIN ? 0 : 1,
        pwm: Math.round(lin * 255 / 1000),
        sdeg: ang * STEERING_MAX_DEG,
        ff: 0, ttl: TTL_MS, wifi: 1, sid: 'preview1', res: 'ACCEPTED'
      };
    }

    if (previewMode) {
      document.getElementById('previewNotice').style.display = 'block';
      setConnection('offline');
    }
    renderLiveValues();
    acquire();
    startPolling();
  })();
  </script>
</body>
</html>
)rawliteral";
