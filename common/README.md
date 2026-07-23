# Common contracts

Jetson, backend와 frontend가 공유하는 외부 계약의 단일 기준점입니다. 구현 코드나 프레임워크별 생성물은 각 모듈에 두고 이곳에는 중립적인 명세와 예시를 둡니다.

- `protocol/`: REST, WebSocket, WebRTC 및 ROS 연계 규약
- `schemas/`: JSON Schema, OpenAPI, AsyncAPI 등 기계 검증 가능한 계약
- `samples/`: 정상·오류·경계 조건 샘플 메시지

계약을 변경할 때는 호환성, 버전과 생산자/소비자 영향 범위를 기록합니다.

시스템 경계와 채널 선택은 [5장 전체 시스템 아키텍처](../docs/architecture/05-전체-시스템-아키텍처.md)와 [31장 Jetson·Spring Boot·관제 웹 통신 설계](../docs/architecture/31-Jetson-Spring-Boot-관제-웹-통신-설계.md)를 참고합니다.
