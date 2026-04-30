# 기능 정의서 — Slack ↔ Jira ↔ Claude Code 봇

> 외출 중 Slack DM 한 줄로 Jira 이슈를 코드 변경·테스트·PR 생성까지 자동화.
> 설계 의도·결정사항은 [`plan.md`](./plan.md), 구현 진행은 git history 참조.

---

## 1. 봇이 하는 일

```
[iPhone Slack DM]            [맥 (launchd 24/7)]               [GitLab/GitHub]
 run fix CDS-99 -d ...   →   bot.py + bot_lib              →   PR 자동 생성
                              ├─ Jira 이슈 fetch
                              ├─ feature 브랜치 생성
                              ├─ claude -p 로 코드 편집
                              ├─ 프로젝트 테스트 실행
                              ├─ commit + push + gh pr create
                              └─ Slack 회신
```

단일 사용자 (`U0AUQ2VTVQE`), 단일 맥. 외출 중 트리거가 핵심 가치.

## 2. 명령 카탈로그

원칙: **조회는 평문 명사**, **변경은 동사 + CLI 플래그**, **작업은 `run`**.

### 2.1 작업 (run)

```
run <type> <issue>                          ← Jira-trust 모드 (instruction 없음)
run <type> <issue> -d <지시문>              ← -d 그리디: 끝까지 캡처
```

- `<type>`: `fix | feat | refactor | chore | docs | test | perf`
- `<issue>`: Jira 키 (`^[A-Z]+-\d+$`)
- `-d`: 자유 텍스트 지시문 (인용부호 불필요, `-d` 는 항상 마지막)

브랜치 자동 명명: `<type>/[<who>/]<issue>` — `who` 가 있으면 namespace 들어감.

### 2.2 조회 (read-only, 평문)

| 명령 | 동작 |
|---|---|
| `repo` | 등록된 repo 전체 목록 |
| `remote [<repo>]` | git remote 이름·URL |
| `branch [<repo>] [all]` | 최근 브랜치 (default 10, all=최대 50) |
| `find <pattern> [-r <repo>]` | 파일 path 검색 (case-insensitive substring) |
| `who` | 현재 사용자 이름 + 출처 (context / env / unset) |
| `status` | 컨텍스트 + repo 요약 |
| `help` / `도움말` | 전체 사용법 |

### 2.3 컨텍스트 설정 (write)

```
init <repo>                                  ← 컨텍스트 set, 나머지는 default
init <repo> -r <remote>                      ← + remote override
init <repo> -b <branch>                      ← + base 브랜치 override
init <repo> -r <remote> -b <branch>          ← 모두

who <name>                                   ← 사용자 이름 set (브랜치 namespace 용)
who clear                                    ← 사용자 이름 해제

clear                                        ← 컨텍스트 전체 삭제
cleanup <repo>                               ← 워킹 트리 git reset --hard + clean -fd
cancel <repo>                                ← 진행 중인 claude SIGTERM
```

`who` 의 검증: ASCII 영숫자 + `.`, `-`, `_` 만 허용 (브랜치명 호환).

## 3. 설정 (운영자가 한 번 만지는 것)

### 3.1 `.env` (gitignore)

```bash
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_USER_ID=U...

JIRA_BASE_URL=https://company.atlassian.net
JIRA_EMAIL=you@company.com
JIRA_API_TOKEN=...

BOT_USER=hjpark        # (옵션) 브랜치 namespace 기본값
```

### 3.2 `projects.md`

마크다운 표 1 행 = 1 repo:

| 이름 | 경로 | 기본 브랜치 | 원격 | 테스트 명령 | 테스트 타임아웃(초) |
|---|---|---|---|---|---|
| ceph-api | /Users/hyungjunpark/IdeaProjects/ceph-service-api | dev | 305 | ./gradlew test --no-daemon | 600 |

봇이 기동 시 1회 파싱·캐시. `테스트 명령` 이 `-` 또는 빈 칸이면 테스트 skip. `원격` 이 비면 `origin` default.

봇 자기 자신의 repo 는 등록해도 self-modification 가드로 거부됨.

### 3.3 컨텍스트 영속화

`~/Library/Application Support/jira-bot/context.json` 에 자동 저장.
JSON: `{repo, remote, branch?, who?}`. 봇 재시작·맥 재부팅 무관 유지.

## 4. 워크플로 — 일상 사용

```
[처음 한 번]
init ceph-api -r 305            ← 컨텍스트 set
who hjpark                       ← 브랜치 namespace 설정 (또는 .env BOT_USER)

[작업할 때마다]
find ApiOsdService               ← 파일 path 확인 (선택, 토큰 절감)
                                  → src/main/java/.../ApiOsdService.java

run fix CDS-99 -d src/.../ApiOsdService.java 의 export 메소드에 javadoc 추가
                                  → claude 호출, 테스트, commit, push, PR 생성

[봇 회신]
✅ 완료 — fix/hjpark/CDS-99
커밋: a3f9d21abc
diff: 1 file changed, 18 insertions(+)
테스트: PASS (attempt 1/3)
토큰: in=X out=Y cache(...) — $0.0XXX
PR: http://gitlab.../merge_requests/...

[잘못 보냈을 때]
cancel ceph-api                  ← claude SIGTERM
cleanup ceph-api                 ← 부분 편집 정리
```

## 5. 안전장치

| 단계 | 메커니즘 |
|---|---|
| 사전 예방 | self-repo 가드 / dirty-check (시작 거부) / `find` 로 path 명시 |
| 작업 한도 | claude per-call timeout 600s · 전체 30분 cap · 재시도 max 3회 |
| 사용자 중단 | `cancel <repo>` (SIGTERM) |
| 사후 복구 | `cleanup <repo>` (working tree 초기화) |
| 권한 격리 | `--permission-mode acceptEdits` + `--disallowedTools Bash WebFetch WebSearch` |
| 사용자 allowlist | `SLACK_USER_ID` 외 silent ignore |

## 6. 토큰 비용 (Sonnet 기준 실측)

| 시나리오 | 토큰 | 비용 |
|---|---|---|
| 모호한 한 줄 지시 (path 없음) | ~150K | $0.19 |
| `find` 로 path 명시 후 동일 작업 | ~115K | $0.15 |
| 시스템 prompt floor (불가피) | ~80K cache_read | $0.025 |

**비용 대부분은 cache_create 단계의 claude reasoning + tool ceremony.** 절감 옵션:
- 모델을 Haiku 로 (단가 1/4 — 미도입)
- Pre-inject 파일 내용 (미도입)
- 토큰 watchdog (미도입)

## 7. 상주화

```
launchd plist : ~/Library/LaunchAgents/com.hjpark.jira-bot.plist
로그          : ~/Library/Logs/jira-bot.log + jira-bot.err.log
재기동        : launchctl kickstart -k gui/$(id -u)/com.hjpark.jira-bot
```

자세한 절차는 [`launchd/README.md`](./launchd/README.md).

## 8. 모듈 구성

```
bot.py                       Socket Mode 진입점
bot_lib/
├─ commands.py               run/help 파서
├─ registry.py               projects.md 파싱
├─ jira_client.py            Jira REST + ADF→텍스트
├─ git_ops.py                git 래퍼 (find_files, list_remotes, branches, push, ...)
├─ claude_runner.py          claude -p Popen + on_start/on_end 콜백
├─ test_runner.py            pytest/gradle 등 외부 테스트 실행
├─ orchestrator.py           §7 처리 흐름 + 재시도 + token usage 누적
├─ slack_handler.py          명령 라우팅 + 응답 포매팅
├─ context.py                ContextStore (JSON 영속)
├─ mutex.py                  RepoMutex (repo 단위 직렬화)
└─ cancellation.py           CancellationRegistry (claude PID 추적·SIGTERM)
```

테스트 245건 (live 포함 246건). live 마커는 `pytest -m live` 로 opt-in.

## 9. 미구현·후순위

- `-m haiku` 모델 옵션 (default 변경 검토)
- `pre-inject 파일 내용` 자동화
- `토큰 watchdog` (per-attempt 한도 자동 차단)
- 사내 repo 추가 시 push 권한 사전 검증 자동화 ([§14](./plan.md#14-남은-불확실성))

## 10. 변경 이력 — 큰 흐름

자세한 commit 단위는 `git log --oneline`. 마일스톤만:

- 슬래시 4토큰 명령으로 시작 (`fix/repo/CDS-99/x`)
- `init <repo> -r <remote> -b <branch>` 컨텍스트 도입 → 3토큰 단축형
- 슬래시 작업 명령 폐기 → `run <type> <issue> -d <text>` 단일 형태
- 조회 명령군 (`repo`, `remote`, `branch`, `find`, `who`, `status`)
- `who` 도입으로 `<type>/<who>/<issue>` 브랜치 namespace
- `cancel`, `find` 로 토큰·시간 절감 보강
- Token usage 회신에 표시
