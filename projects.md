# Project Registry

봇이 기동 시 이 표를 파싱해 dict로 캐시한다 (`bot_lib/registry.py`).
새 프로젝트를 추가하려면 행 하나를 끝에 붙이고 봇을 재기동.
한 행을 임시로 비활성화하려면 `이름` 컬럼 맨 앞에 `#` 를 붙인다.
`테스트 명령` 이 `-` 또는 빈 칸이면 해당 repo는 테스트 단계를 스킵한다.

| 이름 | 경로 | 기본 브랜치 | 테스트 명령 | 테스트 타임아웃(초) |
|---|---|---|---|---|
| jira-digest | /Users/hyungjunpark/dev/jira-slack-digest | main | .venv/bin/pytest -q | 120 |
