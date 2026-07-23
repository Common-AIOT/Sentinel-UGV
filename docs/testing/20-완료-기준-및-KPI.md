<!--
  GENERATED FILE - DO NOT EDIT DIRECTLY.
  Source: docs/specifications/Sentinel_UGV_최종_통합_명세서_v1.0-rc1.md
  Generator: scripts/docs/split-integrated-spec.ps1
-->

> 기준 문서: Sentinel UGV 통합 명세서 v1.0-rc1 (2026-07-22). 변경은 전체본에 반영한 뒤 분할 스크립트를 실행합니다.

# 20. 완료 기준 및 KPI [확정]
## 20.1 Definition of Done
| **영역**  | **완료 기준**                                               |
|-----------|-------------------------------------------------------------|
| 하드웨어  | 무한궤도/구동부가 반복 주행하고 배선·전원이 안전하게 고정됨 |
| STM32     | 엔코더 PID·CRC·sequence·300ms watchdog·부팅 안전값 검증    |
| ROS2      | 한 개 launch로 센서·SLAM·Nav2·탐사·안전 노드 실행           |
| AI        | person 탐지·ByteTrack·다중 인원 encounter가 실시간 동작      |
| 센서 융합 | 탐지 위치가 지도에 표시되고 실패 상태도 처리                |
| 스트리밍  | 로컬 WebRTC가 관제 화면에서 안정적으로 재생                 |
| 수동 제어 | 게임패드 연결·deadman·해제 정지·제어권 동작                 |
| 관제      | 실시간 상태, 제어, 지도, 이벤트 알림 표시                   |
| 데이터    | 임무·시계열·S3 미디어가 과거 페이지에서 조회                |
| 상호작용  | 안전 접근·고정 질문·무응답 처리·구조화 보고가 동작          |
| 안전      | E-Stop·watchdog·센서 장애 정지 검증                         |
| 배포      | EC2 재배포와 Jetson 배포 스크립트 문서화                    |
| 문서      | README, 실행법, 회로/기구, API, 테스트 결과, 변경 이력 완료 |

## 20.2 발표용 KPI
| **KPI**                              | **수집 방법**       |
|--------------------------------------|---------------------|
| 평균/최대 YOLO 추론 FPS              | Jetson 로그         |
| 로컬/원격 영상 지연                  | 타임코드 촬영       |
| 평균 주행 속도·총 이동 거리          | robot_pose 시계열   |
| 탐사 시간·복귀 시간                  | missions            |
| 사람 후보·encounter·중복 억제 수      | encounters/observations |
| 응답자·무응답자 수와 상호작용 완료율  | encounter_victims/interactions |
| 최대 CPU/GPU/메모리·온도             | robot_metrics       |
| 장애물 급정지 횟수                   | safety_events       |
| 네트워크 단절 후 누락 업로드 복구 수 | media_assets 상태   |
| 전체 시나리오 성공률                 | 리허설 체크리스트   |

## 20.3 최종 산출물
- 동작 가능한 Sentinel UGV 실물
- GitLab 모노레포와 이슈·MR 기록
- EC2 관제 웹 서비스
- PostgreSQL/TimescaleDB/S3 데이터 구조
- ROS2 launch·파라미터·배포 스크립트
- 3D 프린팅 CAD/STL 및 BOM
- 프로젝트 README와 사용자 매뉴얼
- 테스트 결과·성능 그래프·시연 영상
- 최종 발표자료와 본 종합 명세서 Final 버전
