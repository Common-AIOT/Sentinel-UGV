-- 명세 13.2(일반 테이블)·13.3(hypertable) 기준 MVP 스키마.
-- MVP 범위 밖인 users·interaction_sessions·interaction_turns 는 제외한다.
-- hypertable 은 TimescaleDB 권장에 따라 FK 를 두지 않고 mission_id 인덱스로만 조회한다.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ── 일반 테이블 (13.2) ─────────────────────────────────────────────────────

CREATE TABLE robots (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         VARCHAR(64) NOT NULL UNIQUE,
    status       VARCHAR(32) NOT NULL DEFAULT 'OFFLINE',
    last_seen_at TIMESTAMPTZ
);

CREATE TABLE missions (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    robot_id           UUID        NOT NULL REFERENCES robots (id),
    created_by_user_id UUID,
    status             VARCHAR(32) NOT NULL,
    started_at         TIMESTAMPTZ,
    ended_at           TIMESTAMPTZ,
    home_pose          JSONB,
    end_reason         VARCHAR(64),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_missions_started_at ON missions (started_at DESC);

CREATE TABLE mission_results (
    mission_id      UUID PRIMARY KEY REFERENCES missions (id) ON DELETE CASCADE,
    duration_sec    INTEGER,
    distance_m      DOUBLE PRECISION,
    coverage        DOUBLE PRECISION,
    detection_count INTEGER
);

CREATE TABLE maps (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id  UUID        NOT NULL REFERENCES missions (id) ON DELETE CASCADE,
    s3_key_pgm  VARCHAR(512),
    s3_key_yaml VARCHAR(512),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE events (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id   UUID REFERENCES missions (id) ON DELETE CASCADE,
    message_id   UUID UNIQUE,             -- QoS 1 중복 멱등 처리 (29.3)
    type         VARCHAR(64) NOT NULL,
    severity     VARCHAR(16) NOT NULL,
    occurred_at  TIMESTAMPTZ NOT NULL,
    payload_json JSONB
);

CREATE INDEX idx_events_mission_occurred ON events (mission_id, occurred_at DESC);

CREATE TABLE encounters (
    id                        UUID PRIMARY KEY,  -- Jetson 생성 UUID 를 그대로 사용 (29.3)
    mission_id                UUID        NOT NULL REFERENCES missions (id) ON DELETE CASCADE,
    map_id                    UUID REFERENCES maps (id),
    status                    VARCHAR(32) NOT NULL,
    map_x                     DOUBLE PRECISION,
    map_y                     DOUBLE PRECISION,
    map_yaw                   DOUBLE PRECISION,
    detected_person_count     INTEGER,
    responsive_person_count   INTEGER,
    unresponsive_person_count INTEGER,
    interaction_summary       TEXT,
    started_at                TIMESTAMPTZ NOT NULL,
    interaction_started_at    TIMESTAMPTZ,
    interaction_ended_at      TIMESTAMPTZ,
    ended_at                  TIMESTAMPTZ,
    termination_reason        VARCHAR(64)
);

CREATE INDEX idx_encounters_mission ON encounters (mission_id, started_at DESC);

CREATE TABLE victims (
    id            UUID PRIMARY KEY,
    latest_status VARCHAR(32),
    first_seen_at TIMESTAMPTZ
);

CREATE TABLE encounter_victims (
    encounter_id    UUID NOT NULL REFERENCES encounters (id) ON DELETE CASCADE,
    victim_id       UUID NOT NULL REFERENCES victims (id),
    track_id        INTEGER,
    response_status VARCHAR(32),
    first_seen_at   TIMESTAMPTZ,
    last_seen_at    TIMESTAMPTZ,
    PRIMARY KEY (encounter_id, victim_id)
);

CREATE TABLE detections (
    id               UUID PRIMARY KEY,  -- Jetson 생성 UUID (QoS 1 멱등, 13.2)
    mission_id       UUID        NOT NULL REFERENCES missions (id) ON DELETE CASCADE,
    encounter_id     UUID REFERENCES encounters (id) ON DELETE CASCADE,
    victim_id        UUID REFERENCES victims (id),
    track_id         INTEGER,
    class            VARCHAR(32),
    confidence       DOUBLE PRECISION,
    map_x            DOUBLE PRECISION,
    map_y            DOUBLE PRECISION,
    position_quality VARCHAR(32),
    pose_status      VARCHAR(32),
    observed_at      TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_detections_encounter ON detections (encounter_id, observed_at DESC);

CREATE TABLE media_assets (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id             UUID         NOT NULL REFERENCES missions (id) ON DELETE CASCADE,
    encounter_id           UUID REFERENCES encounters (id) ON DELETE CASCADE,
    type                   VARCHAR(32)  NOT NULL,
    s3_key                 VARCHAR(512) NOT NULL UNIQUE,
    storage_status         VARCHAR(16)  NOT NULL,  -- 13.6 업로드 상태 머신
    duration_ms            BIGINT,
    triggered_at           TIMESTAMPTZ,
    interaction_started_at TIMESTAMPTZ,
    interaction_ended_at   TIMESTAMPTZ,
    pre_buffer_sec         DOUBLE PRECISION,
    post_buffer_sec        DOUBLE PRECISION,
    termination_reason     VARCHAR(64),
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX idx_media_assets_encounter ON media_assets (encounter_id);

CREATE TABLE control_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    robot_id   UUID        NOT NULL REFERENCES robots (id),
    user_id    UUID,
    expires_at TIMESTAMPTZ NOT NULL
);

-- 로봇당 제어권은 1개만 존재한다 (11.4).
CREATE UNIQUE INDEX uq_control_sessions_robot ON control_sessions (robot_id);

CREATE TABLE control_commands (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id        UUID REFERENCES missions (id) ON DELETE CASCADE,
    issued_by_user_id UUID,
    command_id        UUID        NOT NULL UNIQUE,  -- 명령 멱등 키 (31-4)
    type              VARCHAR(32) NOT NULL,
    requested_at      TIMESTAMPTZ NOT NULL,
    result            VARCHAR(32),
    sequence          BIGINT
);

-- ── TimescaleDB hypertable (13.3) ─────────────────────────────────────────

CREATE TABLE robot_pose (
    time             TIMESTAMPTZ      NOT NULL,
    mission_id       UUID             NOT NULL,
    x                DOUBLE PRECISION NOT NULL,
    y                DOUBLE PRECISION NOT NULL,
    yaw              DOUBLE PRECISION NOT NULL,
    linear_velocity  DOUBLE PRECISION,
    angular_velocity DOUBLE PRECISION
);
SELECT create_hypertable('robot_pose', 'time');
CREATE INDEX idx_robot_pose_mission ON robot_pose (mission_id, time DESC);

CREATE TABLE robot_metrics (
    time        TIMESTAMPTZ NOT NULL,
    mission_id  UUID        NOT NULL,
    battery     DOUBLE PRECISION,
    voltage     DOUBLE PRECISION,
    cpu         DOUBLE PRECISION,
    gpu         DOUBLE PRECISION,
    memory      DOUBLE PRECISION,
    jetson_temp DOUBLE PRECISION,
    state       TEXT  -- hypertable 문자열 컬럼은 TimescaleDB 권장에 따라 TEXT 를 사용한다.
);
SELECT create_hypertable('robot_metrics', 'time');
CREATE INDEX idx_robot_metrics_mission ON robot_metrics (mission_id, time DESC);

CREATE TABLE environment_metrics (
    time        TIMESTAMPTZ NOT NULL,
    mission_id  UUID        NOT NULL,
    temperature DOUBLE PRECISION,
    humidity    DOUBLE PRECISION
);
SELECT create_hypertable('environment_metrics', 'time');
CREATE INDEX idx_environment_metrics_mission ON environment_metrics (mission_id, time DESC);

CREATE TABLE network_metrics (
    time             TIMESTAMPTZ NOT NULL,
    mission_id       UUID        NOT NULL,
    latency_ms       DOUBLE PRECISION,
    packet_loss      DOUBLE PRECISION,
    connection_state TEXT,
    stream_mode      TEXT
);
SELECT create_hypertable('network_metrics', 'time');
CREATE INDEX idx_network_metrics_mission ON network_metrics (mission_id, time DESC);

CREATE TABLE safety_events (
    time            TIMESTAMPTZ NOT NULL,
    mission_id      UUID        NOT NULL,
    min_distance    DOUBLE PRECISION,
    cmd_vel_linear  DOUBLE PRECISION,
    cmd_vel_angular DOUBLE PRECISION,
    stop_reason     TEXT
);
SELECT create_hypertable('safety_events', 'time');
CREATE INDEX idx_safety_events_mission ON safety_events (mission_id, time DESC);
