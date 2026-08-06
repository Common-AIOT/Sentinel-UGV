// 모바일 수동 조종 페이지 (S15P11A301-298).
//
// `tank_drive.ino` 의 페이지에서 **멈추는 경로는 전부 그대로 가져왔다** -
// pointerdown/up/cancel/leave 홀드 deadman, ■정지 버튼, Space/Escape,
// window.blur·visibilitychange, touch-action:none. 그 코드는 검증된 안전 장치이며
// 손댈 이유가 없다.
//
// 바뀐 것은 다섯이다.
//
//  1. 재전송 400ms → 50ms. 250ms TTL 에서 400ms 는 **매 패킷 사이에 스톨을 보장**
//     한다. 단일 in-flight 플래그로 중첩 요청을 막는다.
//  2. `/manual/session` 부트스트랩과 seq 카운터. 단일 조종자를 강제한다.
//  3. `f`/`t` 탱크 믹싱 → `lin`/`ang` 정규화 밀리 단위. 좌 = +(CCW, REP-103).
//  4. **좌/우는 전진·후진 홀드 중에만 활성**이다. 전륜 조향 차량은 제자리 회전을
//     물리적으로 할 수 없고(§34-2) 펌웨어가 거부한다. tank_drive 에서는 좌측 단독
//     누름이 제자리 회전이었으므로, 그 동작이 사라진 것을 UI 가 말해야 한다.
//  5. 상태줄을 응답 JSON 으로 렌더한다. 「관제 정지 수신」·「방향 전환 대기」처럼
//     사람이 무엇을 하면 되는지가 화면에 있어야 한다.
//
// 선회 슬라이더는 없앴다. 속도 슬라이더는 **클라이언트 전용**으로 `lin` 을
// 스케일링할 뿐이며 보드에 별도 엔드포인트가 없다.
#pragma once

#include <Arduino.h>

const char CONTROL_PAGE[] PROGMEM = R"rawliteral(
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Sentinel 수동 조종</title>
  <style>
    body {
      margin: 0; min-height: 100vh; display: grid; place-items: center;
      background: #111827; color: #f9fafb; font-family: sans-serif;
    }
    main { width: min(92vw, 440px); text-align: center; }
    h1 { font-size: 1.3rem; margin: 12px 0 4px; }
    p.hint { color: #94a3b8; font-size: .85rem; margin: 4px 0 12px; }
    #status { min-height: 1.5em; color: #facc15; font-weight: 700; font-size: 1.15rem; }
    #notice { min-height: 1.2em; color: #fb923c; font-size: .9rem; margin-top: 4px; }
    .pad { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 16px 0; }
    .pad button {
      min-height: 84px; border: 0; border-radius: 14px; color: white;
      font-size: 1.25rem; font-weight: 700; touch-action: none; user-select: none;
    }
    .pad button:disabled { opacity: .35; }
    .spacer { visibility: hidden; }
    #forward { background: #15803d; }
    #backward { background: #1d4ed8; }
    #left, #right { background: #b45309; }
    #stop { background: #b91c1c; }
    .speed { margin: 16px 0; padding: 16px; border-radius: 14px; background: #1f2937; text-align: left; }
    label { display: block; margin-bottom: 10px; font-weight: 700; }
    input[type="range"] { width: 100%; }
    #takeover {
      display: none; width: 100%; min-height: 56px; margin-bottom: 12px;
      border: 0; border-radius: 14px; background: #7c3aed; color: white;
      font-size: 1.05rem; font-weight: 700;
    }
  </style>
</head>
<body>
  <main>
    <h1>Sentinel 수동 조종</h1>
    <p class="hint">누르고 있는 동안만 움직입니다. 손을 떼면 바로 멈춥니다.<br>
      <b>전진·후진 중에만 조향됩니다</b> — 제자리 회전은 할 수 없습니다.</p>

    <button id="takeover">제어권 가져오기</button>
    <div id="status">정지</div>
    <div id="notice"></div>

    <div class="pad">
      <div class="spacer"></div>
      <button id="forward">▲ 전진</button>
      <div class="spacer"></div>
      <button id="left" disabled>◀ 좌</button>
      <button id="stop">■ 정지</button>
      <button id="right" disabled>우 ▶</button>
      <div class="spacer"></div>
      <button id="backward">▼ 후진</button>
      <div class="spacer"></div>
    </div>

    <div class="speed">
      <label for="speed">속도: <span id="speedValue">60</span>%</label>
      <input id="speed" type="range" min="20" max="100" value="60">
    </div>
  </main>

  <script>
  (function () {
    'use strict';

    // 250ms TTL 에 대해 50ms 재전송이면 한 번 손실돼도 네 번 더 기회가 있다.
    // 400ms(tank_drive 값)는 매 패킷 사이에 스톨을 보장한다.
    var SEND_INTERVAL_MS = 50;
    var TTL_MS = 250;
    var IDLE_POLL_MS = 500;

    var held = { fwd: false, back: false, left: false, right: false };
    var sid = null;
    var seq = 0;
    var inFlight = false;      // 단일 in-flight 가드. 없으면 요청이 쌓인다.
    var driveTimer = null;
    var idleTimer = null;
    var speedPercent = 60;

    var statusEl = document.getElementById('status');
    var noticeEl = document.getElementById('notice');
    var takeoverEl = document.getElementById('takeover');
    var leftEl = document.getElementById('left');
    var rightEl = document.getElementById('right');

    function driving() { return held.fwd || held.back; }
    function active() { return driving() || held.left || held.right; }

    function linMilli() {
      var dir = (held.fwd ? 1 : 0) - (held.back ? 1 : 0);
      return Math.round(dir * 10 * speedPercent);   // ±(20..100) × 10 = ±1000
    }

    // 좌 = +(CCW, REP-103). 조향은 주행 중에만 의미가 있다.
    function angMilli() {
      if (!driving()) return 0;
      return ((held.left ? 1 : 0) - (held.right ? 1 : 0)) * 1000;
    }

    var STATE_LABEL = {
      0: '부팅 중', 1: '대기', 2: '준비', 3: '수동 권한 보유',
      4: '자율 주행 중', 5: '정지 중', 6: '비상 정지', 7: '결함 정지'
    };

    function render(body) {
      if (!body) return;

      if (body.lat && body.dm && body.pwm !== 0) {
        statusEl.textContent = held.back ? '후진 중' : '전진 중';
      } else if (body.lat) {
        statusEl.textContent = STATE_LABEL[body.st] || '수동 권한 보유';
      } else {
        statusEl.textContent = STATE_LABEL[body.st] || '정지';
      }

      var notes = [];
      if (body.res && body.res !== 'ACCEPTED') notes.push(body.res);
      if (body.rearm) notes.push('관제 정지 수신 — 손을 떼고 다시 누르세요');
      if (body.dz) notes.push('방향 전환 대기 (약 0.5초)');
      if (body.nosteer && (held.left || held.right)) notes.push('조향 불가 — 전진·후진 중에만 꺾입니다');
      if (body.ff) notes.push('결함 비트 0x' + body.ff.toString(16));
      if (!body.wifi) notes.push('WiFi 끊김');
      noticeEl.textContent = notes.join(' · ');

      // 래치를 잃었으면(관제가 자율로 되돌렸다) 세션도 사라진다.
      if (body.sid === '00000000' && sid !== null) {
        sid = null;
        takeoverEl.style.display = 'block';
        noticeEl.textContent = '제어권이 회수되었습니다. 다시 가져오세요.';
      }
    }

    function request(url) {
      if (inFlight) return Promise.resolve(null);
      inFlight = true;
      return fetch(url, { cache: 'no-store' })
        .then(function (r) { return r.json().catch(function () { return null; }); })
        .then(function (body) { render(body); return body; })
        .catch(function () {
          noticeEl.textContent = '연결 끊김 — 바퀴는 자동으로 멈춥니다';
          return null;
        })
        .then(function (body) { inFlight = false; return body; });
    }

    function nextSeq() { seq = (seq + 1) & 0xFFFF; return seq; }

    function sendDrive() {
      if (sid === null) return;
      var dm = active() ? 1 : 0;
      request('/manual/drive?sid=' + sid + '&seq=' + nextSeq() +
              '&dm=' + dm + '&lin=' + linMilli() + '&ang=' + angMilli() +
              '&ttl=' + TTL_MS);
    }

    // 부작용 없는 조회다. **/manual/drive 를 폴링에 쓰면 안 된다** — 그러면
    // lastManualInputMs 가 계속 갱신되어 관제의 500ms 신선도 창이 영구히 닫히지
    // 않고 로봇을 되찾을 수 없다.
    function pollState() { request('/manual/state'); }

    function refresh() {
      leftEl.disabled = !driving();
      rightEl.disabled = !driving();

      if (active()) {
        if (idleTimer !== null) { clearInterval(idleTimer); idleTimer = null; }
        sendDrive();
        if (driveTimer === null) driveTimer = setInterval(sendDrive, SEND_INTERVAL_MS);
        return;
      }

      if (driveTimer !== null) { clearInterval(driveTimer); driveTimer = null; }
      // 손을 뗀 것을 즉시 알린다. TTL 만료를 기다리지 않는다.
      if (sid !== null) {
        request('/manual/drive?sid=' + sid + '&seq=' + nextSeq() +
                '&dm=0&lin=0&ang=0&ttl=' + TTL_MS);
      }
      if (idleTimer === null) idleTimer = setInterval(pollState, IDLE_POLL_MS);
    }

    function setHeld(dir, value) {
      // 주행을 놓으면 조향도 함께 놓는다. 안 그러면 정지 상태에서 좌/우만 눌린
      // 채로 남아 다음 전진이 곧바로 꺾여 출발한다.
      if ((dir === 'fwd' || dir === 'back') && value === false) {
        held.left = false;
        held.right = false;
      }
      if (held[dir] === value) return;
      held[dir] = value;
      refresh();
    }

    function stopAll() {
      held.fwd = held.back = held.left = held.right = false;
      refresh();
      // 세션 불일치에도 수락되는 경로다. 누구의 정지든 항상 존중한다.
      fetch('/manual/stop?sid=' + (sid === null ? '0' : sid) + '&seq=' + nextSeq(),
            { cache: 'no-store' }).catch(function () {});
    }

    function bindButton(id, dir) {
      var el = document.getElementById(id);
      el.addEventListener('pointerdown', function () { setHeld(dir, true); });
      el.addEventListener('pointerup', function () { setHeld(dir, false); });
      el.addEventListener('pointercancel', function () { setHeld(dir, false); });
      el.addEventListener('pointerleave', function () { setHeld(dir, false); });
    }

    bindButton('forward', 'fwd');
    bindButton('backward', 'back');
    bindButton('left', 'left');
    bindButton('right', 'right');
    document.getElementById('stop').addEventListener('click', stopAll);

    document.addEventListener('keydown', function (event) {
      if (event.key === ' ' || event.key === 'Escape') { event.preventDefault(); stopAll(); }
    });
    window.addEventListener('blur', stopAll);
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) stopAll();
    });

    var speed = document.getElementById('speed');
    var speedValue = document.getElementById('speedValue');
    speed.addEventListener('input', function () {
      speedPercent = parseInt(speed.value, 10);
      speedValue.textContent = speed.value;
    });

    function acquire() {
      fetch('/manual/session', { cache: 'no-store' })
        .then(function (r) {
          return r.json().catch(function () { return null; }).then(function (body) {
            if (r.status === 409) {
              takeoverEl.style.display = 'block';
              noticeEl.textContent = '다른 조종자가 사용 중입니다. 잠시 뒤 다시 시도하세요.';
              return;
            }
            if (!body || !body.sid || body.sid === '00000000') {
              noticeEl.textContent = '세션 발급 실패';
              return;
            }
            sid = body.sid;
            seq = 0;
            takeoverEl.style.display = 'none';
            render(body);
          });
        })
        .catch(function () { noticeEl.textContent = '로봇에 연결할 수 없습니다'; });
    }

    takeoverEl.addEventListener('click', acquire);
    acquire();
    idleTimer = setInterval(pollState, IDLE_POLL_MS);
  })();
  </script>
</body>
</html>
)rawliteral";
