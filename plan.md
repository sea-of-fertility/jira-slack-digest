---
name: plan-2.0
description: Slack DM 한 줄로 로컬 맥(Claude Code)에게 Jira 이슈 수정을 지시해 브랜치 생성·코드 수정·테스트·commit·push·PR 생성까지 자동화하는 봇 시스템의 설계 문서. bot.py 구현·아키텍처·확정 결정사항(§12 Q1~Q17, Invariant 1~4)을 참조해야 할 때 로드.
---

# Plan 2.0 — Slack Bot 기반 원격 코드 수정

> 목적: 외출 중/모바일 환경에서 Slack DM 한 줄로 로컬 맥(Claude Code 설치된)에게 "이 Jira 이슈 수정해라" 를 지시하면, 맥이 알아서 브랜치 만들고 `claude -p` 로 코드 수정하고 commit까지 마치는 최소 시스템.
>
> 대화로 수정해 가며 확정할 설계 문서. 최종 구현은 여기 합의된 내용을 기반으로 bot.py 에 반영.

---

## 1. 목표 (What)

- **입력**: Slack DM 한 메시지 (이슈 키 + 프로젝트 이름 + 수정 지시)
- **처리**: 상주 봇 프로세스가 받아 → Jira 이슈 본문 조회 → 매핑된 repo 디렉토리로 이동 → 브랜치 생성 → `claude -p` 로 수정 → **테스트 실행** → commit → **push → PR 자동 생성**
- **출력**: Slack DM으로 "완료 (브랜치명, 커밋 해시, 변경 diff stat, 테스트 결과, PR URL)" 회신
- **개발 방식**: 봇 자체(`bot.py` 및 보조 모듈)는 **TDD** 로 작성. 각 기능은 실패하는 테스트 먼저 → 통과시키는 최소 구현 → 리팩터 순서.

## 1.1 설계 전제 (Design Invariants)

이 봇은 **사용자가 해당 PC에 직접 접속할 수 없는 상황** 을 위한 도구다. PC 앞에 있을 때는 Claude Code 로컬을 직접 쓰고, 봇을 쓰지 않는다.

이 전제로부터 파생되는 불변식:

1. **봇이 작동하는 시점에 repo 는 봇만의 소유.** 다른 프로세스·사용자가 동시에 파일을 건드리지 않는다고 가정한다. _(§10 전제)_
2. **워킹 트리가 dirty 하면 = 전제 위반 신호.** 봇은 조용히 진행하는 대신 중단하고 Slack 으로 현황을 보고한다. _(§7 step 6, §10 보안장치 6)_
3. **자동 stash / 자동 복구 금지.** 이상 상태는 중단으로 드러내고, 복구는 원격 `cleanup/<repo>` 명령(§12 Q15)으로만 이루어진다. 봇이 "영리하게" 처리하면 진짜 사용자 WIP 를 덮을 위험이 커짐.
4. **봇은 사용자 응답을 기다리지 않는다.** 진행 상태 중계(§12 Q-interactive A)만 허용. 승인 버튼(B)·thread 대화(C)·실시간 개입(D) 금지. 상호작용이 필요하면 PC 로컬의 Claude Code 를 쓴다.

## 2. 비목표 (What NOT)

- 완전 자동화 (이슈가 생기면 알아서 수정) — 반드시 사용자가 명시 트리거
- 자동 merge — 봇은 PR 생성까지만, merge 는 사용자가 모바일 GitHub 앱 등에서 수동 결정
- 여러 사용자 지원 — 본인(`U0AUQ2VTVQE`)만

## 3. 아키텍처

```
┌─────────────────┐
│ iPhone Slack    │  DM: "fix CDS-611 in ceph-api: Exporter.java javadoc 추가"
└────────┬────────┘
         │ Socket Mode (WebSocket, 서버에서 outbound 연결)
         ↓
┌──────────────────────────────────────────────────────┐
│ 상주 맥 (launchd 로 24/7)                            │
│                                                      │
│  bot.py (slack_bolt + SocketModeHandler)             │
│   ├─ allowFrom 검증 (U0AUQ2VTVQE 외 모두 무시)       │
│   ├─ 메시지 파싱 (정규식)                            │
│   ├─ projects.md 로드 → 경로·기본브랜치 조회         │
│   ├─ Jira 이슈 본문 조회 (기존 헬퍼 재사용)          │
│   ├─ git: pull base, checkout -b <branch>            │
│   ├─ subprocess: claude -p --permission-mode         │
│   │               acceptEdits (파일만 수정)          │
│   ├─ git diff --stat 확인                            │
│   ├─ 프로젝트 test_cmd 실행 (projects.md 참조)       │
│   │     └─ 실패 → Q11 정책에 따라 처리               │
│   ├─ git add -A && git commit (테스트 결과 포함)     │
│   └─ Slack say(): 결과 리포트 (테스트 통과 여부)     │
└──────────────────────────────────────────────────────┘
                        ↓
              ~/IdeaProjects/ceph-service-api
                  브랜치: fix/CDS-611-<ts>
                  커밋: fix(CDS-611): <summary>
```

핵심 원칙:
- **봇이 git 워크플로 주도권을 갖는다.** `claude -p` 는 파일 편집만.
- **프로젝트 레지스트리는 수기 편집하는 plain Markdown** (DB·yaml 불필요).
- **OpenClaw 등 범용 프레임워크 미사용.** 토큰 오버헤드 + 복잡도 회피.

## 4. 산출물 (사용자가 직접 다루게 되는 것)

- **`projects.md`** — 프로젝트 레지스트리 (수기 편집, §5 참조)
- **신규 Slack 명령 1종** — `<type>/<repo>/<issue>/<instruction>` (§6)
- **기존 digest 와 같은 봇 DM** 에서 양방향으로 동작 (§8)
- **상주 프로세스** — 맥 부팅 후 자동 시작, 재기동 (§11)

실제 소스 파일 구성·모듈 분할은 §13 구현 단계에서 결정.

## 5. `projects.md` 포맷 (초안)

```markdown
# Project Registry

| 이름 | 경로 | 기본 브랜치 | 테스트 명령 | 테스트 타임아웃(초) |
|---|---|---|---|---|
| ceph-api | /Users/hyungjunpark/IdeaProjects/ceph-service-api | develop | ./gradlew test --no-daemon | 600 |
| jira-digest | /Users/hyungjunpark/dev/jira-slack-digest | main | .venv/bin/pytest -q | 120 |
| real-chat | /Users/hyungjunpark/dev/real-time-chat | main | npm test -- --run | 300 |
```

- 봇은 기동 시 이 파일을 파싱해 dict로 캐시.
- 새 프로젝트 추가 시 행 하나 추가 후 봇 재시작 (또는 향후 `reload` 명령어 추가).
- 행 필드: `이름` (Slack 명령에서 쓸 별칭), `경로` (절대), `기본 브랜치`, `테스트 명령`, `테스트 타임아웃`.
- `테스트 명령` 이 비어 있으면("-" 또는 공란) 해당 repo는 **테스트 단계를 스킵**하고 바로 commit. (레거시/테스트 없는 repo 대응)

### 파싱 견고성 규약
- 표 파싱 실패(열 개수 불일치, 깨진 파이프 등) → **봇 기동 거부**. 어느 행이 문제인지 에러 로그에 라인번호로 출력.
- 경로가 존재하지 않으면 → **기동 거부**. (부분 skip 하지 않음 — 등록된 repo 는 모두 정상 기동 시 사용 가능해야 함.)
- 경로가 symlink 면 → 따라감 (개발자 의도로 간주).
- `테스트 타임아웃` 이 정수가 아니면 → **기동 거부**.
- `-`, `빈 칸`, 주석 처리(`#`) 된 행은 허용하되 명시적 스킵 규칙을 문서화.

## 6. Slack 명령 포맷 (확정)

### 포맷
```
<type>/<repo>/<issue>/<instruction>
```

- `<type>` — Conventional Commits 접두사, **허용 7종**: `fix`, `feat`, `refactor`, `chore`, `docs`, `test`, `perf`
- `<repo>` — `projects.md` 에 등록된 별칭 (예: `ceph-api`, `jira-digest`)
- `<issue>` — JIRA 이슈 키, 형식 `^[A-Z]+-\d+$` (예: `CDS-2099`, `CEPH-457`)
- `<instruction>` — 자유 텍스트 (줄바꿈·슬래시·한글 모두 허용)

### 파싱 규칙
```python
parts = text.split("/", maxsplit=3)    # 앞 3번만 split — 지시문 내부 슬래시 보존
if len(parts) != 4:
    return None
type_, repo, issue, instruction = parts
```

### 예시
```
fix/ceph-api/CDS-2099/새 예외 클래스 생성. IoException 상속, CephApiException.java 참고.
feat/jira-digest/CDS-3100/README.md 에 설치 가이드 추가
refactor/real-chat/CDS-2500/src/ws/handler.ts 의 switch 문을 전략 패턴으로 분리
docs/ceph-api/CDS-2077/README.md 의 API 사용 예시 섹션 보강
```

멀티라인도 허용:
```
fix/ceph-api/CDS-2099/새 예외 클래스 생성.
- CephApiException.java 참고
- message 포맷: "CEPH_xxx"
- /error 경로 라우팅 주의
```

### 전처리 (파싱 전에)
- `text = text.strip()` — 메시지 전체 앞뒤 공백 제거
- 각 토큰도 개별 `strip()` — `fix / ceph-api / ...` 같은 공백 섞인 입력 관대 수용
- 빈 문자열 금지 — 어느 토큰이든 `strip()` 후 빈 값이면 거부

### 검증 흐름

| 단계 | 조건 | 실패 시 응답 |
|---|---|---|
| split 결과 4토큰 | `len(parts) == 4` | "형식: <type>/<repo>/<issue>/<instruction>" |
| 모든 토큰 non-empty (strip 후) | 4개 모두 길이 > 0 | "instruction(또는 해당 토큰)이 비어 있습니다" |
| type 화이트리스트 | `type_ in ALLOWED_TYPES` | "지원 type: fix, feat, refactor, chore, docs, test, perf" |
| repo 등록됨 | `repo in PROJECTS` | "모르는 repo: `<repo>`. 혹시: `<difflib 후보>`?" |
| issue 형식 | `^[A-Z]+-\d+$` | "issue 형식: PROJ-123 같은 Jira 키" |

### 도움말 트리거
`text.strip().lower() == "help"` **또는** `text.strip() == "도움말"` — **정확 일치만** 도움말 분기로 들어감. `fix/.../help` 같은 instruction 토큰 내부 단어와 충돌 방지.

### 브랜치 명명
```
<type>/<issue>
```
- 예: `fix/CDS-2099`, `feat/CDS-3100`, `refactor/CDS-2500`
- **브랜치 재사용 키는 `<type>+<issue>` 조합 기준** (§7, §12 Q6b). 같은 이슈라도 type 이 다르면 (`fix/CDS-99` vs `refactor/CDS-99`) **별개 브랜치** 가 정상이다 — 서로 다른 PR 을 만들어야 하는 논리적으로 독립된 변경이라는 뜻.
- repo 이름은 브랜치에 포함하지 않음 — 브랜치는 해당 repo 내부 식별자이므로 중복될 여지가 없음.

### 커밋 메시지 (Conventional Commits)
```
<type>(<issue>): <Jira 이슈 제목>

<instruction>

Tests: PASS (attempt N/3) | SKIPPED
```
- `FAIL` 은 포맷에 없음. 최종 실패 시엔 commit 자체가 안 일어나기 때문. _(§7 step 11)_
예:
```
fix(CDS-2099): OAuth refresh 실패 시 500 반환 버그

새 예외 CephApiException 생성. IoException 상속.

Tests: PASS
```

## 7. 처리 흐름 (행동 관점)

사용자 명령 1건이 들어왔을 때 봇이 어떤 순서로, 어떤 규칙에 따라 움직이는지를 기술한다. 실제 코드는 §13 단계에서 TDD 로 별도 설계.

**타임 측정 기준**: 아래 "30분 cap" (§12 Q11d) 은 **step 1 수신 시각부터 step 11 최종 보고 시각까지** 의 벽시계 시간 전체를 말한다. Claude 호출 + 테스트 + git 조작 + 대기가 모두 포함됨.

1. **수신·권한 확인** — 허용된 사용자(§10-1)의 DM 인지 확인. 아니면 무응답. 수신 시각을 기록해 이후 30분 cap 판정에 사용.
2. **도움말 분기** — 본문이 정확히 `help` 또는 `도움말` 이면 §6 사용법을 회신하고 종료.
3. **명령 해석** — §6 의 4토큰 포맷으로 파싱. 전처리(strip, 빈 토큰 금지) → type/repo/issue 형식 검증 → 실패 유형별 정확한 힌트로 회신.
4. **동시성 점검 (mutex)** — 해당 repo 에 대한 작업이 이미 진행 중이면 "순서대로 처리되니 대기해 주세요" 회신 후 큐에 적재 (§12 Q8). 아니면 락 획득하고 다음으로.
5. **Jira 이슈 로드** — 이슈 제목·본문·최근 댓글을 조회 (기존 digest 의 조회 헬퍼 재사용).
6. **워킹 트리 점검** — `git status --porcelain` 으로 깨끗한지 확인. dirty 면 **중단 + 현황 회신** (§1.1 Invariant 2).
   - _(주의)_ 실패 후 남는 `ATTEMPT_FAILED.log` 는 §11 에 따라 **repo 바깥** 에 저장되므로 이 점검을 막지 않는다.
   - 데드락 해소용 원격 명령으로 `cleanup/<repo>` 제공 (§12 Q15). `git reset --hard HEAD` + untracked 파일 삭제로 초기화.
7. **브랜치 준비** — 브랜치 키 = `<type>/<issue>` (§12 Q6b):
   - 로컬에 존재 → 체크아웃 후 재사용 (base pull 생략)
   - 원격에만 존재 → fetch 해서 추적 브랜치 생성
   - 둘 다 없음 → base 를 pull 하고 신규 분기
   - **base 와의 gap 경고** (§12 Q17): 재사용 시 base 대비 몇 커밋 뒤져있는지를 Slack 에 함께 알림. 자동 rebase 는 하지 않음 (Invariant 위반 위험).
   - **base 직접 commit 방지**: 현재 HEAD 가 `projects.md` 의 해당 repo 기본 브랜치와 동일한 상태로 진입하려는 경우 거부 (§10-4).
8. **Claude 호출** — Jira 본문 + 사용자 지시를 프롬프트로 주입. `claude -p` 1회 실행. 편집 외 도구는 명시적 deny-list 로 차단 (§10-3).
9. **변경 확인** — Claude 호출 직후 파일이 실제로 바뀌었는지 확인. 첫 시도에서 아무 파일도 안 건드렸으면 중단·보고.
10. **테스트 실행** — repo 에 `test_cmd` 가 정의돼 있으면 실행. **없으면 이 단계 자체를 건너뛰고 바로 step 12 로 진행 (커밋).** skip 은 fail 이 아니다.
11. **실패 시 자동 재시도** — test_cmd 가 있고 결과가 fail 일 때만 진입. §12 Q11~Q11d 정책.
    - 테스트 실패 로그를 Claude 에게 피드백하며 같은 브랜치 위에 **추가 교정**.
    - 최대 3회. **호출 방식**: 첫 호출의 session_id 로 `--resume` 을 시도, 실패하면 stateless 로 자동 폴백 (§12 Q11c).
    - **"변경 없음" 판정 기준**: _직전 attempt 종료 시점의 HEAD 대비 현재 워킹트리의 incremental diff_. 누적 diff 가 아님. Claude 가 이번 attempt 에서 아무것도 더 편집하지 않았다는 뜻일 때 무한 루프 방지로 루프 종료.
    - 전체 30분 cap (§12 Q11d) 은 step 1 수신 시각 기준. cap 초과 시 즉시 중단.
12. **결과 처리**
    - **최종 통과 또는 test skip**: Conventional Commits 포맷으로 커밋. 이어서 다음 step 으로.
    - **최종 실패 (재시도 3회 소진 또는 cap 초과)**: 커밋하지 않고 편집 상태를 그대로 남김. 마지막 테스트 로그는 **repo 외부** (`~/Library/Logs/jira-bot/<issue>-<timestamp>.log`)로 저장. Slack 에 "직접 확인 필요 + `cleanup/<repo>` 로 초기화 가능" 안내 후 종료.
13. **push → PR 생성** (통과/skip 경로 한정, §12 Q3): `git push -u origin <branch>` → `gh pr create --title "<type>(<issue>): <Jira 제목>" --body "<instruction>\n\nCloses <issue-key>"`.
    - push 또는 PR 생성이 실패하면 (네트워크·권한·충돌): commit 은 이미 로컬에 존재하므로 손실 없음. Slack 에 "commit 완료, push/PR 실패: <원인>. `cleanup/<repo>` 로 초기화하거나 PC 복귀 후 수동 push" 회신.
    - 동일 브랜치로 이미 PR 이 열려 있으면 (재트리거 케이스): 새 PR 을 만들지 않고 기존 PR 에 "새 커밋 추가됨" 코멘트만 달거나 그대로 두 (`gh` 가 자동으로 "PR already exists" 상태를 돌려 보냄).
14. **최종 Slack 보고** — 브랜치명, 커밋 해시(최신), diff 요약, 테스트 결과(`PASS (attempt N/3)` 또는 `SKIPPED`), PR URL 을 한 메시지에 담아 회신.
15. **사용자 몫** — 모바일 GitHub 앱 등에서 PR 리뷰 후 merge 결정. merge 자체는 봇이 하지 않는다 (§2).

각 단계에서 봇이 Slack 에 어떤 진행 상태 메시지를 보낼지는 Invariant 4 (interactive) 확정 후 이 섹션을 보강.

## 8. Slack App 권한 확장 (기존 봇 재사용 확정)

**결정: 기존 digest 앱 하나에 권한·이벤트·Socket Mode를 얹어서 확장.** 신규 앱 분리 대안은 폐기.

### 이 결정의 이유 (UX 우선)

- 아침 digest가 보여준 이슈(예: `CDS-611`)를 **같은 DM 스크롤 안에서** 길게 누르고 `fix CDS-611 in ceph-api: ...` 로 바로 답장 가능. 컨텍스트 끊김 없음.
- 모바일에서 두 개 봇 DM 사이 이동 불필요.
- DM 타임라인 자체가 **작업 기록**이 됨 (어떤 이슈를 언제 고쳤는지 Slack 검색으로 추적).
- 개인 1인 + 같은 사람이 양쪽 책임인 상황에서 앱 분리로 얻는 "blast radius 축소" 이득은 제한적 — 어차피 같은 맥·같은 `.env` 에 토큰이 있음. 실질 방어는 토큰 관리 강건성에서 나옴 (§10 참조).

### 기존 앱에 **추가로** 켜야 할 것

Bot Token Scopes — 아래 3개 **추가** (기존 `chat:write`, `im:write` 유지):
- `im:read` — DM 채널 메타 정보
- `im:history` — DM 본문 읽기
- `app_mentions:read` — 멘션 트리거(선택 사용)

Event Subscriptions:
- `message.im` 구독 **활성화**

Socket Mode:
- **활성화** (양방향 WebSocket)

App-Level Token 신규 발급:
- `xapp-...` 토큰, 스코프 `connections:write`

### 앱 재설치 필요 (일회성)

스코프 추가 시 Slack이 "워크스페이스에 재설치" 를 요구합니다. 관리 페이지에서 **Install to Workspace** 버튼 한 번 눌러 승인하면 끝. 기존 봇 토큰(`xoxb-...`)은 유효성 그대로 유지됩니다.

### 봇 이름·아이콘

**변경 없이 유지.** 양방향 기능 추가돼도 정체성은 동일. 사용자가 DM 내역을 "한 봇과의 대화" 로 자연스럽게 인식.

### 환경 변수

`.env` 에 **한 줄만 추가**:
```bash
SLACK_APP_TOKEN="xapp-..."     # Socket Mode 용, 신규
```

기존 `SLACK_BOT_TOKEN` (xoxb-...) 은 **그대로 재사용**. cron digest 와 Socket Mode 봇 양쪽이 동일 토큰 공유.

### 두 런타임 경로가 공존

| 경로 | 트리거 | 사용 토큰 | 용도 |
|---|---|---|---|
| `jira_daily_digest.py` + **cron** (현행 유지) | 정해진 시각 배치, 단발성 | `SLACK_BOT_TOKEN` (HTTP `chat.postMessage`) | 아침 digest 발송 |
| `bot.py` + **launchd** | 상주 프로세스, 24/7 | `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN` (Socket Mode) | DM 수신·처리·응답 |

digest 는 단발성이라 cron 이 자연스러움. launchd 로의 통합은 하지 않음 — 두 트랙 독립 유지가 운영 단순성에 더 기여.

두 경로는 **서로 간섭하지 않음** — 동일 봇 아이덴티티로 동작하되 코드·로그·수명주기 분리.

### 로그 파일 분리 (운영 편의)

동일 봇이지만 두 경로의 로그는 섞이지 않게:
- digest: `~/dev/jira-slack-digest/digest.log` (기존 유지)
- bot(Socket Mode): `~/Library/Logs/jira-bot.log` (신규, §11 launchd plist 에서 지정)

## 9. 개발 방식 원칙

- **TDD 로 진행한다.** 모든 기능은 실패하는 테스트가 먼저 작성된 뒤에야 구현 코드가 들어온다 (Red → Green → Refactor).
- **외부 자원은 mock, 순수 로직은 실물로 검증한다.** Slack/Jira/Claude CLI/네트워크·subprocess 는 mock 대상. 파싱·레지스트리 로드·경로 조립 등 pure 로직은 실입력으로 테스트.
- **봇 자체의 테스트** 와 **봇이 건드리는 대상 repo 의 테스트** 는 격리한다. 봇의 pytest 는 대상 repo 의 테스트 스위트를 실행하지 않는다.
- 구체적 테스트 파일 구성·Mock 기법·케이스 목록 등 **구현 세부는 §13 단계에서 결정**한다.

## 10. 보안·안전 장치

1. **Slack user ID allowlist** (`ALLOWED = "U0AUQ2VTVQE"`) — 이외 모두 조용히 무시
2. **프로젝트 화이트리스트** — `projects.md` 에 명시된 repo 만 조작
3. **Claude 권한 제한 — 편집 허용 + shell/웹 deny-list**
   - `--permission-mode acceptEdits` — 파일 편집 제안을 자동 승인
   - `--disallowedTools Bash WebFetch WebSearch` 로 shell 실행·외부 호출을 **명시적으로 차단**
   - 주의: `acceptEdits` 단독으로는 Bash 실행이 막히지 않을 수 있음. deny-list 조합이 실제 방어선 (§14 에 실동작 검증 항목 있음).
4. **기본 브랜치 보호** — `projects.md` 에 등록된 **해당 repo 의 기본 브랜치 필드 값** (예: `main`, `develop`) 과 현재 HEAD 가 같을 때 거부. 하드코딩 `main|master` 체크가 아님.
5. **Claude 호출당 timeout 600초** — 무한 루프 방지. 전체 30분 cap 은 §12 Q11d.
6. **워킹 트리 점검** — dirty 상태면 작업 시작 자체를 거부 (§1.1 Invariant 2, §7 step 6). 실패 후 남는 로그는 repo 외부에 저장하므로 이 판정을 막지 않음.
7. **로그 파일** — 명령·결과를 `~/Library/Logs/jira-bot.log` 에 append. 실패 시 테스트 로그도 같은 디렉토리.
8. **토큰 위생 — Slack × 2 + GitHub × 1**
   - `SLACK_BOT_TOKEN` (xoxb-...) 유출 시: digest 발송 + DM 읽기·쓰기 + (봇 경유) 코드 수정 트리거 전부 노출
   - `SLACK_APP_TOKEN` (xapp-...) 유출 시: Socket Mode 연결 탈취 가능 → 봇 이벤트 스트림 가로채기·위조 가능
   - **`gh` CLI 인증 토큰** (`~/.config/gh/hosts.yml`) 유출 시: 인증된 스코프에 해당하는 모든 repo 에 push·PR 생성 가능. Q3 (push+PR 자동화) 결정으로 이 토큰이 공격 표면에 편입됨.
   - 아래는 세 자격증명에 공통 적용:
     - `.env` 파일 권한 `600` 유지 (`chmod 600 .env`), `~/.config/gh/` 권한 `700` 확인
     - `.gitignore` 로 `.env` 커밋 원천 차단
     - Time Machine/Dropbox/iCloud 백업 대상에서 `.env` 및 `~/.config/gh/` 제외
     - 로그에 토큰 값 echo 금지 (`${TOKEN:0:6}...${TOKEN: -4}` 마스킹만)
     - rotate 절차 문서화: Slack App 페이지에서 각각 re-issue → `.env` 갱신 → 봇 재시작 (+ cron digest 재시작). `gh auth refresh` 로 GitHub 토큰 재발급.
   - **GitHub 권한 범위 최소화**: `gh` 인증 스코프를 `repo`, `workflow` 정도로 한정. `admin:org` 등 과도 스코프 부여 금지.

## 11. 상주화 원칙

- macOS `launchd` 로 상주. 맥 부팅 후 자동 시작, 프로세스 비정상 종료 시 자동 재기동.
- 로그는 표준출력·표준에러를 각각 별도 파일로 분리 (`jira-bot.log`, `jira-bot.err.log`) — 디버깅 편의.
- 봇의 수명주기는 기존 cron digest 와 독립. 봇이 죽어도 아침 digest 는 영향 없음, 반대도 마찬가지.
- 구체적 plist 구조·등록 명령 등 실행 방법은 §13 구현 단계에서 정리.

## 12. 확정 필요한 결정 (여기서부터 대화로 수정)

| # | 항목 | 후보 | 잠정안 |
|---|---|---|---|
| Q1 | 명령 파싱 방식 | (a) `<type>/<repo>/<issue>/<instruction>` 슬래시 4토큰 (DM 본문), (b) `/fix` 슬래시 커맨드, (c) 자연어(LLM 파싱) | **(a) 확정** — Conventional Commits type 7종(fix, feat, refactor, chore, docs, test, perf) + repo + 이슈키 + 지시문. `text.split("/", 3)` 으로 파싱. §6 참조 |
| Q2 | git 작업 주체 | (a) 봇이 주도, claude는 편집만, (b) claude가 git까지 | **(a) 봇 주도 — 확정** |
| Q3 | push·PR 생성 포함? | (a) commit만, (b) push까지, (c) PR까지 (`gh pr create`) | **(c) PR까지 자동 — 확정.** 이 봇의 존재 이유("PC 에 못 갈 때") 와 commit-only 가 상충. PC 복귀 전에는 로컬 commit 이 쓸모없음. push + PR 까지 가야 모바일 GitHub 앱에서 review→merge 완결 가능. merge 는 사용자 수동 (§2). push/PR 실패는 치명적이지 않음: commit 은 로컬에 남고 `cleanup/<repo>` 로 복구 가능 (§7 step 13) |
| Q4 | 첫 테스트 대상 repo | `jira-slack-digest` 자기 자신 / `ceph-service-api` / 기타 | **자기 자신 — 확정** (부수는 비용 최소) |
| Q5 | Slack App 분리 | (a) 신규 앱, (b) 기존 digest 앱 확장 | **(b) 기존 앱 확장 — 확정** (UX 연속성 우선, 봇 이름·아이콘 유지, §8 참조) |
| Q6 | 명령 포맷 상세 | 필드 추가? (예: 모델 지정 `fix/ceph-api/CDS-99/@sonnet/...`) | **기본 필드만 — 확정.** 모델 오버라이드 등 확장은 실제 사용 중 필요성 드러나면 후속 버전 |
| Q6b | 브랜치 충돌 처리 | (a) 접미사 자동 증분(`-2`, `-3`), (b) 에러 회신, (c) 기존 브랜치 재사용 | **(c) 기존 브랜치 재사용 — 확정.** 로컬에 있으면 `checkout`, 원격만 있으면 `fetch + checkout`, 둘 다 없으면 신규. base pull 은 신규일 때만. 워킹 트리 깨끗하지 않으면 중단. §7 의사코드 참조 |
| Q7 | 실행 중 사용자 cancel 명령 지원 여부 _(재정의: 기존 "실행 중 요청 들어오면 에러 회신" 은 Q8 과 중복이라 제거)_ | (a) `cancel/<repo>` 같은 명령으로 진행 중 작업 중단 가능, (b) 시작한 작업은 끝까지 — cancel 불가 | **(b) cancel 불가 — 확정.** 구현 단순·런타임 중단 위험 회피. 실패해도 commit 안 됨 + 브랜치만 남음이라 사후 `cleanup/<repo>` 로 정리 가능 |
| Q8 | 동시 요청 처리 (같은 repo 또는 전역) | (a) 순차 mutex (큐잉), (b) 병렬 | **(a) repo 단위 mutex — 확정.** 같은 repo 에 두 요청이 겹치면 나중 것은 "대기 중" 회신 후 큐잉. 다른 repo 요청은 동시 실행 허용 |
| Q9 | claude 모델 | haiku / sonnet / opus / 기본 | **sonnet — 확정** (코딩 품질 균형). 실사용 중 과/부족 드러나면 Q6 확장으로 명령별 오버라이드 추가 검토 |
| Q10 | 실패 시 브랜치 자동 삭제 | 삭제 / 유지(디버깅용) | **유지 — 확정** (자동 삭제는 증거 파괴) |
| Q11 | 테스트 실패 시 동작 | (a) abort·commit 생략, (b) commit은 하고 Slack에 실패 표시, (c) claude에게 로그 돌려주고 자동 재시도 (에이전틱) | **(c) 재시도 — 확정.** 최대 3회, 실패 로그를 claude 에게 피드백. 최종 실패 시 uncommitted 유지 + `ATTEMPT_FAILED.log` 저장. 상세 §7 의사코드 참조 |
| Q11b | 재시도 간 파일 상태 | (a) 이어서 작업(누적 편집), (b) 매 attempt 리셋(`git checkout -- .`) | **(a) 이어서 — 확정.** 이전 시도 위에 claude 가 교정. 완전 리셋은 피드백 학습 효과 낭비 |
| Q11c | claude 재호출 방식 | (a) stateless (매번 전체 컨텍스트 재전송), (b) `--resume <session-id>` stateful | **(b) resume 우선, 실패 시 stateless 폴백 — 확정.** 첫 호출을 `--output-format json` 으로 돌려 session_id 추출, 재시도 때 `--resume` 사용. resume 지원 안 되면 자동으로 stateless 로 전환. 토큰 비용 3~5× 차이 |
| Q11d | 전체 타임아웃 cap | (a) attempt 당만 (600s×3회=최악 30분), (b) 전체 cap 별도 | **(b) 전체 30분 cap — 확정.** 루프 진입 전 `time.time()` 체크해 초과 시 중단. attempt 당 600s 는 유지 |
| Q12 | 테스트 명령 비어 있는 repo 허용 | (a) 무조건 테스트 요구, (b) 비었으면 스킵 허용 | **(b) 스킵 허용 — 확정** (단 commit 메시지에 `Tests: SKIPPED` 명시) |
| Q13 | bot 자체 테스트 커버리지 목표 | (a) 없음, (b) 핵심 경로만 그린, (c) 80%+ | **(b) 핵심 경로 그린 — 확정** |
| Q14 | claude -p 호출을 실제로 거는 통합 테스트 | (a) 0개 (순수 mock), (b) 1~2개 수동 flag | **(b) `pytest -m live` 로 opt-in — 확정** |
| Q15 | 원격 recovery 명령 `cleanup/<repo>` 도입 | (a) 도입 (dirty 상태 데드락 해소, PC 밖에서 `git reset --hard HEAD` + untracked 삭제), (b) 수동만 (PC 복귀 시 직접 정리) | **(a) 도입 — 확정.** §7 step 6 dirty 중단의 탈출구. 구현 범위: `git reset --hard HEAD`, `git clean -fd`, 상태 리포트 한 줄 |
| Q16 | 브랜치 재사용 키 | (a) `<issue>` 만, (b) `<type>+<issue>` 조합 | **(b) `<type>+<issue>` — 확정.** `fix(CDS-99)` 와 `refactor(CDS-99)` 는 별개 논리 변경이라 분리 PR 이 정상. §6, §7 step 7 참조 |
| Q17 | 기존 브랜치 재사용 시 base gap 대응 | (a) 자동 rebase, (b) Slack 에 "base 대비 N 커밋 뒤" 경고만 출력하고 진행, (c) gap 있으면 작업 거부 | **(b) 경고만 — 확정.** 자동 rebase 는 Invariant 위반(병합 충돌 시 모호) 위험. 거부는 과함. 사용자가 PC 복귀 시 판단 |
| Q-interactive | 사용자 상호작용 허용 범위 | (A) 진행 상태 중계만, (B) 승인 버튼, (C) thread 대화, (D) 실시간 개입 | **(A) 진행 중계만 — 확정.** Invariant 4 참조. 토큰 0 추가, 모바일 UX 안정, "봇이 살아있음" 체감 제공. B·C·D 는 기술·UX·토큰 모두 부적합 |

## 13. 작업 단계 (TDD 준수)

각 Step의 코드 작성은 **테스트부터**.

- [x] **Step 0 (설계 확정 완료)** — §12 Q1~Q17 + Q-interactive 모두 확정. §1.1 Invariant 1~4 모두 확정.
- [ ] **Step 1 (테스트 인프라)**: `pytest` + `conftest.py` + 빈 `tests/` 디렉토리, CI-less 상태에서 `pytest -q` 가 0개 통과로 끝나는 것 확인
- [ ] **Step 2 (`bot_lib/commands.py`)**: 명령 파서 — Red/Green 한 줄씩
- [ ] **Step 3 (`bot_lib/registry.py`)**: `projects.md` 파서 (테스트 명령·타임아웃 필드 포함)
- [ ] **Step 4 (`bot_lib/jira_client.py`)**: 기존 `jira_daily_digest.py` 에서 단일 이슈 조회 로직 추출·테스트화
- [ ] **Step 5 (`bot_lib/git_ops.py`)**: branch/diff/commit 래퍼, `tmp_path` 에 실제 git init 해서 검증
- [ ] **Step 6 (`bot_lib/claude_runner.py`)**: claude -p 호출 래퍼 (기본 mock, `-m live` 한 건)
- [ ] **Step 7 (`bot_lib/test_runner.py`)**: 프로젝트 테스트 실행기 (pass/fail/timeout/skip)
- [ ] **Step 8 (`bot.py` 통합)**: 위 모듈들을 조립, integration test 로 해피 패스·실패 경로 검증
- [ ] **Step 9 (Slack App)**: 신규 앱 생성, Socket Mode 활성화, 토큰 2종 발급
- [ ] **Step 10 (`projects.md` 채우기)**: 실 repo 경로·테스트 명령 기입
- [ ] **Step 11 (로컬 실행 PoC)**: `python bot.py` 포그라운드 → Slack에서 `fix ... in jira-digest: ...` 로 자기 자신 수정 (테스트 통과 확인)
- [ ] **Step 12 (회사 repo 확장)**: `ceph-api` 등 실제 repo 추가, 실제 Gradle 테스트 돌려보기
- [ ] **Step 13 (상주화)**: `launchd` plist 등록
- [ ] **Step 14 (관찰)**: 1주일 실사용, 로그 리뷰, 테스트 실패 빈도 집계

## 14. 남은 불확실성

- **`claude -p --permission-mode acceptEdits` + `--disallowedTools` 실동작** — `acceptEdits` 단독으로는 Bash 등 도구 차단이 보장되지 않을 수 있으므로, `--disallowedTools Bash WebFetch WebSearch` 조합으로 실제 shell·네트워크 차단이 되는지 실호출 검증 필요. 한 번의 호출로 멀티파일 편집이 가능한지도 함께 확인.
- **`claude -p --resume <session-id>` 헤드리스 지원 여부** — 재시도 로직의 핵심. 지원 안 되면 stateless 로 폴백하지만 토큰 비용 3~5배 영향 (§12 Q11c).
- **Jira 본문에 ADF 포함 시 품질** — `jira_daily_digest.py` 의 `adf_to_text()` 를 재사용하면 될 것으로 보이나, 이슈마다 포맷이 다를 수 있음.
- **맥 sleep 중 들어온 Slack 메시지** — Socket Mode 연결이 끊겼다 재연결될 때 메시지 유실 여부? (대체로 Slack이 몇 분은 재전송하지만 공식 가이드 재확인 필요)
- **토큰 사용량 추정** — `claude -p` 한 번당 (문맥 준비 + 코드 수정). 큰 repo 인덱싱 비용이 크면 `claude-code` 설정으로 스코프 제한 필요할 수 있음.
- **프로젝트 테스트 명령의 실제 수렴 시간** — Gradle/Maven/npm 프로젝트는 첫 실행 때 의존성 다운로드로 10분+ 걸릴 수 있음. `test_timeout` 기본값과 workspace 예열(예: 봇 기동 시 각 repo의 테스트를 한 번 도는 워밍업) 필요성 검토.
- **flaky 테스트 대응** — 실제 repo에 타이밍에 민감한 테스트가 있으면 false fail로 commit이 자주 막힐 수 있음. 정책 Q11 재검토 포인트.
- **회사 repo push 권한** — `okestro/ceph-service-api` 등 회사 repo 에 `gh` 인증이 push 권한을 갖는지, 조직 정책상 AI 에이전트의 자동 PR 생성이 허용되는지 사전 확인 필요. 금지라면 해당 repo 는 Q3 예외로 `projects.md` 에 `push_enabled=false` 같은 필드 추가 가능성 검토.
- **PR 본문의 Closes 키워드 효력** — Jira 이슈 키(`CDS-2099`) 는 GitHub 의 `Closes` 자동 연결 대상이 아님(GitHub Issues 만 연동). Jira-GitHub 연동 플러그인이 쓰는 Smart Commit 문법(`fixes CDS-2099`)이 실제로 작동하는지 조직 설정 확인.

## 15. 참고·링크

- 현재 작성 시점의 Jira 엔드포인트: `/rest/api/3/search/jql` (v2 폐기됨)
- claude CLI 권한 모드: `plan`, `acceptEdits`, `bypassPermissions` 중 `acceptEdits` 가 안전·생산 균형점
- slack-bolt 문서: https://slack.dev/bolt-python/
- Slack Socket Mode: https://api.slack.com/apis/socket-mode

---

## 변경 이력

- **2026-04-24 (v0.1)**: 초안. OpenClaw 경유 방식 대신 직접 구현으로 결정. `claude -p` 한 번만 호출하는 얇은 봇 구조.
- **2026-04-24 (v0.2)**: 테스트 자동 실행을 **목표에 포함** (비목표에서 제거). 전 개발을 **TDD** 로 진행하도록 개발 방식 명시. `projects.md` 에 `테스트 명령`·`테스트 타임아웃` 필드 추가. `bot_lib/` + `tests/` 구조 확정. Q11~Q14 추가. 작업 단계를 14개로 재편성 (테스트 인프라 우선).
- **2026-04-24 (v0.3)**: §8 재작성 — **기존 digest Slack App 확장으로 확정**. 신규 앱 분리안 폐기. 봇 이름·아이콘 유지. `SLACK_APP_TOKEN` 하나만 .env에 추가. 두 런타임(cron digest + Socket Mode bot)이 동일 봇 아이덴티티로 공존. 로그 파일은 경로별 분리. Q5 잠정안 (a)→(b) 뒤집음. §10에 "단일 봇 토큰 가치 상승"에 따른 토큰 위생 항목 추가.
- **2026-04-24 (v0.4)**: §6 명령 포맷 **확정** — `<type>/<repo>/<issue>/<instruction>` 슬래시 4토큰. type 화이트리스트 7종 (fix, feat, refactor, chore, docs, test, perf). `split("/", 3)` 으로 지시문 내부 슬래시 보존. 브랜치명 `<type>/<issue>`. 브랜치 충돌 시 **기존 브랜치 재사용** 정책 도입 (로컬/원격/없음 3분기). 워킹 트리 dirty 시 중단. `help`/`도움말` 회신 지원. §7 의사코드를 새 파싱·브랜치 로직으로 재작성. Q1·Q6 확정, Q6b(브랜치 충돌) 신설.
- **2026-04-24 (v0.5)**: **테스트 실패 시 자동 재시도 도입**. Q11 잠정안 (a)abort → **(c)재시도 3회** 확정. §7 의사코드를 retry 루프로 재작성 (resume 우선, stateless 폴백, diff 없음 시 조기 종료, 전체 30분 cap). 최종 실패 시 uncommitted 편집 + `ATTEMPT_FAILED.log` 로 작업물 보존. §12 에 Q11b(attempt 간 파일상태=이어서), Q11c(호출방식=resume우선), Q11d(타임아웃 cap=30분) 신설. 각 attempt 마다 Slack 진행 상태 중계 (attempt 번호·테스트 실행·재시도 준비 등).
- **2026-04-24 (v0.6)**: **문서 성격 재정의 — "기능을 어떻게 쓰는지" 만 기술, 구현 세부 제거.** §7 의사코드(Python 200줄) 를 11단계 행동 흐름 요약으로 교체. §4 파일 구성(소스 배치) 을 "사용자 산출물" 4줄 요약으로 축약. §9.5 TDD 세부 (디렉토리 구조·Mock 전략표·케이스 목록 등) 를 개발 원칙 4줄로 축약, §9 와 통합. §11 launchd plist XML 제거 후 상주 원칙 4줄로 축약. 실제 소스 구조·Mock 기법·plist 구체 형식 등은 모두 §13 구현 단계에서 결정.
- **2026-04-24 (v0.7)**: **내부 모순 대수술 (리뷰 기반 13건 반영).** §1.1 설계 전제 신설 (Invariant 1·2 확정, 3·4 미정 표기). §6 검증 표에 "빈 토큰 거부" 추가, `help` 정확 일치 규칙, 토큰 strip 정책, 브랜치 재사용 키 `<type>+<issue>` 명시, 커밋 메시지 포맷에서 `FAIL` 제거. §7 처리 흐름 전면 재정비: 수신 시각 기반 30분 cap 기준 명시, 동시성 점검(step 4) 별도 단계화, skip 경로가 재시도 루프를 건너뛰는 것 명시, "변경 없음" 판정을 attempt-간 incremental diff 로 명시, 최종 실패 로그를 repo 외부로 이동(데드락 방지), `cleanup/<repo>` 안내. §8 런타임 표에서 digest=cron(현행) / bot=launchd 로 명확화. §10 보안장치 재작성: point 3 에 `--disallowedTools` 명시, point 4 base 브랜치 보호를 `projects.md` 값 기반으로 정정, point 8 에 `SLACK_APP_TOKEN` 유출 영향 포함. §5 파싱 견고성 규약 추가. §12 Q7 재정의 (cancel 지원 여부), Q15(cleanup 명령 도입), Q16(브랜치 키 = type+issue 확정), Q17(base gap 경고) 신설. §13 Step 0 을 확정/잠정/미정으로 세분화. §14 에 `--disallowedTools` 실동작·`--resume` 헤드리스 지원 검증 필요 추가.
- **2026-04-24 (v0.8)**: **잔여 결정 일괄 확정 — Step 0 마감.** Q3 뒤집기: commit-only → **push + PR 자동**. 봇의 존재 이유("PC 접근 불가") 와 commit-only 상충 해소. §1 목표를 "PR 생성까지" 로 확장, §2 비목표는 "자동 merge 금지" 만 유지. §7 에 push → `gh pr create` 단계(13) 추가, push/PR 실패도 치명적이지 않게 처리 규정. §10-8 에 `gh` CLI 토큰 위생 추가 (3종 자격증명 공통 관리). §1.1 Invariant 3·4 확정: 자동 stash/복구 **금지**, interactive 는 **진행 중계(A)만**. §12 잠정 Q 전부 확정 라벨 변경 (Q2·Q4·Q6·Q7·Q8·Q9·Q10·Q12·Q13·Q14·Q15·Q17), Q-interactive 행 신설·확정. §14 에 회사 repo push 권한·Jira-GitHub 연동 키워드 검증 필요 추가. §13 Step 0 전체 완료 체크.
