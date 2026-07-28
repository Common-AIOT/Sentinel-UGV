"""단절 중 이벤트를 보관하는 SQLite Outbox (S15P11A301-128 뼈대).

명세 31-10이 데이터 성격에 따라 단절 중 처리를 다르게 정했다.

    주행 명령        저장하지 않고 즉시 폐기
    일반 telemetry   최신값 중심, 긴 backlog 금지
    Mission 이벤트   SQLite Outbox에 저장 → messageId 유지 후 재전송
    탐지·encounter   SQLite Outbox에 저장 → ACK까지 재시도
    이벤트 영상      로컬 파일과 업로드 작업 저장

즉 Outbox는 **이벤트 전용**이다. telemetry를 여기에 넣으면 복구 직후 낡은 값이
쏟아져 관제 화면이 과거를 현재처럼 보여준다.

`messageId`를 그대로 유지해 재전송하는 것이 핵심이다. 서버가 그 값으로 중복을
막으므로(31-10 UNIQUE 제약), 재전송이 중복 저장을 만들지 않는다.

이 티켓은 스키마와 기본 연산까지만 만든다. 실제 이벤트 적재와 재전송 워커는
S15P11A301-123(이벤트 녹화)에서 붙인다. 지금 채우면 발행할 이벤트가 없어
검증할 수 없다.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    -- 봉투의 messageId를 그대로 쓴다. 재전송해도 같은 값이어야 서버가 중복을
    -- 막을 수 있다(31-10).
    message_id   TEXT PRIMARY KEY,
    channel      TEXT NOT NULL,
    payload      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT
);
CREATE INDEX IF NOT EXISTS outbox_created_at ON outbox (created_at);
"""


class OutboxRepository:
    """이벤트 보관과 조회. 재전송 정책은 호출자가 정한다."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False로 두는 이유는 ROS 타이머 스레드와 paho 콜백
        # 스레드가 같은 연결을 쓸 수 있기 때문이다. 쓰기는 짧고 드물어 직렬화
        # 비용이 문제되지 않는다.
        self._connection = sqlite3.connect(
            self.database_path, check_same_thread=False, isolation_level=None
        )
        self._connection.executescript(SCHEMA)

    def enqueue(self, message: dict[str, Any], channel: str) -> None:
        """이벤트를 보관한다. 같은 messageId가 이미 있으면 무시한다."""
        self._connection.execute(
            "INSERT OR IGNORE INTO outbox (message_id, channel, payload, created_at) "
            "VALUES (?, ?, ?, ?)",
            (
                message["messageId"],
                channel,
                json.dumps(message, ensure_ascii=False),
                message["sentAt"],
            ),
        )

    def pending(self, limit: int = 50) -> list[tuple[str, str, dict[str, Any]]]:
        """오래된 것부터 돌려준다. (message_id, channel, message)"""
        rows = self._connection.execute(
            "SELECT message_id, channel, payload FROM outbox "
            "ORDER BY created_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [(row[0], row[1], json.loads(row[2])) for row in rows]

    def mark_sent(self, message_id: str) -> None:
        self._connection.execute(
            "DELETE FROM outbox WHERE message_id = ?", (message_id,)
        )

    def mark_failed(self, message_id: str, error: str) -> None:
        self._connection.execute(
            "UPDATE outbox SET attempts = attempts + 1, last_error = ? "
            "WHERE message_id = ?",
            (error, message_id),
        )

    def count(self) -> int:
        return int(
            self._connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
        )

    def close(self) -> None:
        self._connection.close()
