# Project Registry

봇이 기동 시 이 표를 파싱해 dict로 캐시한다 (`bot_lib/registry.py`).
새 프로젝트를 추가하려면 행 하나를 끝에 붙이고 봇을 재기동.
한 행을 임시로 비활성화하려면 `이름` 컬럼 맨 앞에 `#` 를 붙인다.

`원격` 이 `-` 또는 빈 칸이면 `origin` 으로 간주한다 (단일 remote repo 용 default).
`테스트 명령` 이 `-` 또는 빈 칸이면 해당 repo는 테스트 단계를 스킵한다.

봇 자신의 repo (`/Users/hyungjunpark/dev/jira-slack-digest`) 는 등록해도
orchestrator의 self-repo 가드에 의해 거부된다 — 자기 자신을 수정하면
브랜치 스위치로 봇 소스가 working tree에서 사라져 launchd 재시작이 깨짐.

| 이름 | 경로 | 기본 브랜치 | 원격 | 테스트 명령 | 테스트 타임아웃(초) |
|---|---|---|---|---|---|
| ceph-api | /Users/hyungjunpark/IdeaProjects/ceph-service-api | dev | 305 | ./gradlew test --no-daemon | 600 |
