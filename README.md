# Jira ↔ Slack ↔ Claude Code 통합 도구

이 저장소는 **두 개의 도구**를 함께 제공합니다.

| 도구 | 진입점 | 역할 |
|---|---|---|
| **Daily Digest** | `jira_daily_digest.py` | 매일 아침 cron/launchd 로 실행 — 내게 할당된 Jira 이슈를 정리해 Slack DM 발송 |
| **Slack DM 명령 봇** | `bot.py` + `bot_lib/` | launchd 24/7 상주 — Slack DM 한 줄로 Jira 이슈 fetch → Claude Code 자동 코드 변경 → 테스트 → PR 생성. `jira create` 로 이슈도 만들 수 있음 |

봇의 명령 카탈로그·동작은 [`FEATURES.md`](./FEATURES.md), 설계 의도는 [`plan.md`](./plan.md), AI 자동 세팅 가이드는 [`AGENTS.md`](./AGENTS.md) 참조.

> **⚡ 빠른 setup** — 디지스트만 쓰든 봇까지 쓰든, `.venv` 활성화 + `pip install -e .` 후 그냥 `jira-bot` 만 실행하면 끝납니다. 매 기동마다 `.env` 의 토큰을 Jira `/myself` · Slack `auth.test` 로 라이브 검증하고, 누락이거나 만료됐으면 그 키만 인터랙티브 wizard 가 다시 묻고 (시크릿은 `getpass` 마스킹), 그대로 봇이 돌기 시작합니다. 수동으로 하려면 아래 1~5장을 순서대로 따라가세요.

---

## A. Daily Digest

매일 아침 **내게 할당된 열린 Jira 이슈**를 정리해서 Slack DM으로 받는 스크립트.

두 단계로 "정리"가 들어갑니다.

1. **구조적 정리** — 우선순위·마감일 순 정렬 + "지난 마감 / 오늘 마감 / 다가오는 마감 / 마감일 없음" 버킷.
2. **내용 요약 (선택)** — 각 이슈의 description과 최근 댓글을 LLM에 보내 한국어 1문장(약 80자) 요약을 생성하고, 이슈 라인 아래 "📝 …"로 붙입니다.

요약에는 두 가지 백엔드를 쓸 수 있습니다.

- **`cli` (기본, 추천)** — 이미 로컬에 깔려 있는 코딩 에이전트 CLI(`claude`, `codex`, `gemini`, `opencode`)를 subprocess로 호출합니다. [cokacdir](https://github.com/kstost/cokacdir)과 같은 방식입니다. 기존 구독(Claude Pro/Max 등) 안에서 돌아가므로 **별도 API 비용이 들지 않습니다.**
- **`api`** — Anthropic API를 직접 호출. 가장 빠르지만 토큰 비용이 발생합니다.
- **`none`** — 요약 건너뜀.

---

## 1. Jira API 토큰 발급

1. https://id.atlassian.com/manage-profile/security/api-tokens 접속
2. **Create API token** → 이름 아무거나(예: `daily-digest`) → **Copy**
3. 이 토큰을 `.env`의 `JIRA_API_TOKEN`에 넣고, 계정 이메일을 `JIRA_EMAIL`에 넣습니다.
4. `JIRA_BASE_URL`은 `https://<회사>.atlassian.net` 형태.

## 2. Slack 앱 만들고 DM 권한 주기

1. https://api.slack.com/apps → **Create New App** → *From scratch* → 워크스페이스 선택
2. 좌측 **OAuth & Permissions**
3. **Bot Token Scopes**에 다음 두 개 추가:
   - `chat:write`
   - `im:write` (봇이 사용자에게 DM 열 수 있음)
4. 상단 **Install to Workspace** → 설치 → **Bot User OAuth Token** (`xoxb-...`) 복사 → `.env`의 `SLACK_BOT_TOKEN`
5. **본인 Slack 유저 ID 찾기**: Slack에서 본인 프로필 클릭 → 점 세 개(`⋮`) → **Copy member ID** → `U01ABC23DEF` 같은 값 → `.env`의 `SLACK_USER_ID`
6. 설치한 봇이 본인한테 DM을 보내려면, Slack에서 앱 이름으로 DM 창을 한 번 열어 대화방을 생성해 두면 가장 매끄럽습니다. (`im:write` 스코프가 있으면 자동으로도 됩니다.)
7. **(봇 부분 사용 시 추가)** Socket Mode 용 App-Level Token 발급 — **Basic Information** → 아래쪽 **App-Level Tokens** → *Generate Token and Scopes* → scope `connections:write` 추가 → Generate → `xapp-...` 값 → `.env`의 `SLACK_APP_TOKEN`. 추가로 좌측 메뉴에서 **Socket Mode** 토글 ON, **App Home → Messages Tab** ON + "Allow users to send Slash commands and messages from the messages tab" 체크, **Event Subscriptions** ON + bot events 에 `message.im`/`app_mention` 추가. 자세한 체크리스트는 `AGENTS.md §1.1` 참조. (디지스트만 쓸 거면 이 7번은 건너뛰어도 됩니다.)

## 3. 요약 백엔드 설정

### 3-A. CLI 백엔드 (추천, API 비용 없음)

Claude Code, Codex CLI, Gemini CLI, OpenCode 중 하나가 깔려 있고 로그인돼 있어야 합니다. 이미 쓰고 계시다면 추가 설정은 거의 없습니다.

```bash
# 설치 확인 (예: Claude Code)
which claude
claude --version

# 헤드리스 모드 동작 확인 - 프롬프트 한 줄 답변이 와야 정상
echo "Say hi in Korean" | claude -p
```

`.env`에서 사용할 CLI를 지정합니다.

```bash
LLM_BACKEND=cli
LLM_CLI="claude -p --model haiku"   # 공백이 들어가면 반드시 따옴표로 감쌀 것
LLM_CLI_TIMEOUT=90
```

> **팁 — 값은 따옴표로 감싸기.** `source .env` 로 로드할 때 공백이 있는 값(`claude -p ...`)을 따옴표 없이 두면 shell이 `-p` 를 명령어로 오해해 `command not found: -p` 경고가 납니다. 동작에는 영향 없지만, 잡음이 거슬리면 `"..."` 또는 `'...'` 로 감싸세요.
>
> **팁 — `--model haiku` 로 요금/쿼터 절약.** Claude Code CLI 기본 모델 대신 Haiku를 쓰면 Jira 요약처럼 가벼운 작업에서 **입력 $1/MTok, 출력 $5/MTok** 수준으로 떨어지고 응답 속도도 빠릅니다. 품질은 80자 한 문장 요약엔 충분합니다. `claude -p --model sonnet`, `--model opus` 등도 가능합니다.

스크립트는 `LLM_CLI`에 적힌 명령을 그대로 subprocess로 띄우고, 프롬프트를 **stdin으로 파이프**합니다. 그래서 위의 4개 외에 `-p`/stdin 조합을 받는 다른 CLI도 대부분 꽂아 쓸 수 있습니다. 동시 실행은 기본 3개로 제한합니다(구독 rate limit 보호).

### 3-B. API 백엔드 (선택)

CLI를 안 쓰고 API로만 돌리고 싶으면 https://console.anthropic.com/ 에서 키 발급 후:

```bash
LLM_BACKEND=api
ANTHROPIC_API_KEY=sk-ant-...
# LLM_MODEL=claude-haiku-4-5-20251001   # 기본값
```

Haiku 기준 이슈 20건이어도 하루 약 1~2센트 수준입니다.

### 3-C. 요약 꺼두기

`LLM_BACKEND=none` 또는 실행 시 `--no-llm` 플래그.

## 4. 설치 & 로컬 테스트

```bash
cd <이 폴더>
python3 -m venv .venv
source .venv/bin/activate
pip install -e .              # 봇·디지스트 + 의존성 설치, `jira-bot` / `jira-digest` 콘솔 스크립트 등록
# pip install -e ".[dev]"     # 테스트도 돌릴 거면 (pytest 포함)

cp .env.example .env
# .env 파일을 열어 실제 값으로 교체  (또는 `jira-bot --setup` 으로 wizard 사용)

# 환경 변수 로드
set -a; source .env; set +a

# mock 데이터 + 요약 스킵 → 완전 오프라인, 블록 구조만 확인
jira-digest --mock --dry-run --no-llm

# mock 데이터 + CLI 요약 테스트 (Slack 안 보냄)
jira-digest --mock --dry-run --backend cli

# 실제 Jira 조회 + 요약까지 하되 Slack 안 보내고 페이로드만 출력
jira-digest --dry-run

# 진짜 실행 (Slack DM 전송)
jira-digest
```

## 5. 매일 아침 9시에 자동 실행 (cron)

`crontab -e` 로 열고 추가:

```cron
# 평일 오전 9:00 Jira digest
0 9 * * 1-5 /absolute/path/to/.venv/bin/jira-digest >> /absolute/path/to/digest.log 2>&1
```

환경 변수는 cron이 기본적으로 읽어주지 않으니, 두 가지 중 하나:

**방법 A — 래퍼 스크립트 사용**

`run.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
set -a; source .env; set +a
./.venv/bin/jira-digest
```

```bash
chmod +x run.sh
```

crontab:
```cron
0 9 * * 1-5 /absolute/path/to/run.sh >> /absolute/path/to/digest.log 2>&1
```

**방법 B — crontab 안에 직접 export**

```cron
0 9 * * 1-5 JIRA_BASE_URL=https://... JIRA_EMAIL=... JIRA_API_TOKEN=... SLACK_BOT_TOKEN=xoxb-... SLACK_USER_ID=U... /path/.venv/bin/jira-digest
```

### macOS에서 맥이 자고 있어도 실행되게 하려면

cron은 잠든 맥에서는 안 뜹니다. 필요하면 `launchd`로 감싸거나, GitHub Actions cron을 쓰시면 됩니다 (secrets에 토큰 넣고 `.github/workflows/digest.yml`에서 `on: schedule`).

## 6. 커스터마이즈 팁 (디지스트)

- **특정 프로젝트만**: `.env`에 `JIRA_EXTRA_JQL='project = ABC'` 같은 식으로 추가. (값에 공백이 있으면 따옴표 필수.)
- **To Do 상태만 보내기** (추천): `JIRA_EXTRA_JQL='statusCategory = "To Do"'` — 언어 중립적이라 한글 Jira(`해야 할 일`)에서도 그대로 동작합니다. `"In Progress"` 도 포함하려면 `JIRA_EXTRA_JQL='statusCategory in ("To Do", "In Progress")'`.
- **다른 정렬**: `jira_daily_digest.py`의 `sort_issues()` 안 `key` 함수 조정.
- **주말 제외 / 공휴일 제외**: cron의 `1-5`가 월~금. 더 정교하게는 스크립트 상단에서 `date.today().weekday()` 체크.
- **출력 포맷 변경**: `format_issue_line()` / `build_slack_blocks()` 수정.
- **여러 사람에게 보내기**: `SLACK_USER_ID`를 리스트로 받고 루프 돌리는 식으로 확장.

- **요약 톤/길이**: `jira_daily_digest.py`의 `SUMMARY_SYSTEM` 문구 수정. "1문장 80자"를 "2문장 150자"로 바꾸거나, "진행 현황"에 더 무게를 둘지 "무엇을 해야 하는지"에 둘지 지시 바꿀 수 있음.
- **요약 대상 제한**: 요약 비용이 걱정되면 `summarize_all()` 호출 전에 `issues = [i for i in issues if i['priority'] in ('Highest', 'High')]` 같은 필터 추가.

---

## B. Slack DM 명령 봇 (`bot.py`)

외출 중 iPhone Slack DM 한 줄로 Jira 이슈 fetch → Claude Code 자동 코드 변경 → 테스트 → 커밋 → push → PR 생성까지 자동화하는 봇. 단일 사용자 / 단일 맥 / launchd 24/7.

### 빠른 시작

```bash
# 1) 의존성 (디지스트와 같은 .venv 사용 — 4장에서 `pip install -e .` 끝났다고 가정)
source .venv/bin/activate

# 2) 봇 실행 — 한 명령으로 검증 → (필요 시) 입력 → 실행이 한 번에 끝남
jira-bot
#  → .env 누락/잘못된 토큰이 있으면 그 키만 wizard 가 묻고, 입력 후 그대로 봇이 시작됩니다.
#  → 모두 유효하면 즉시 Socket Mode 진입.

# (선택) wizard 만 돌리고 끝내고 싶을 때 — launchd 가 봇 재기동
jira-bot --setup
```

> **토큰 라이브 검증** — 매 기동마다 Jira `GET /rest/api/3/myself`, Slack `auth.test` 를 호출해 401/`invalid_auth` 를 잡습니다. `SLACK_APP_TOKEN` (`xapp-…`)·`SLACK_USER_ID` (`U…`) 는 호출 가능한 검증 엔드포인트가 없어 형식만 검사합니다. 비대화 환경(launchd) 에서 검증 실패 시 stderr 에 어느 키가 어떤 이유로 거부됐는지 찍고 exit 2 — 터미널에서 `jira-bot --setup` 으로 갱신.

### 명령 카탈로그 (요약)

| 명령 | 동작 |
|---|---|
| `run <type> <issue> [-d <지시문>]` | claude 호출 → 코드 변경 → 테스트 → 커밋 → PR (`<type>`: fix/feat/refactor/chore/docs/test/perf) |
| `jira create -k <분류> -t <제목> [-d <본문>]` | Jira 이슈 생성 (분류: 에픽/작업/버그/스토리, 프로젝트 CDS 고정) |
| `jira get [-s <상태>] [-p <키\|all>]` | 내 이슈 조회 (assignee=나) — 기본 CDS / `-p all` 로 전 프로젝트, 최대 20개 |
| `init <repo> [-r <remote>] [-b <branch>]` | 세션 컨텍스트 저장 — 이후 `run` 명령에서 repo 자동 사용 |
| `who [<name>] / who clear` | 브랜치 namespace `<type>/<who>/<issue>` 용 사용자 이름 |
| `repo / remote / branch / find / status` | 조회 (read-only) |
| `cancel <repo>` | 진행 중 claude SIGTERM |
| `cleanup <repo>` | 워킹 트리 `git reset --hard` + `clean -fd` |
| `clear` | 세션 컨텍스트 삭제 |
| `help` / `도움말` | 전체 사용법 |

자세한 동작·옵션은 [`FEATURES.md`](./FEATURES.md), 설계 의도는 [`plan.md`](./plan.md).

### 24/7 상주화 (launchd)

```bash
# plist 위치
~/Library/LaunchAgents/com.hjpark.jira-bot.plist

# 코드 변경 후 재기동
launchctl kickstart -k gui/$(id -u)/com.hjpark.jira-bot

# 로그
~/Library/Logs/jira-bot.log
~/Library/Logs/jira-bot.err.log
```

자세한 plist 예시는 [`launchd/`](./launchd) 디렉토리.

### macOS sleep 주의

macOS 가 시스템 sleep 에 들어가면 launchd 봇도 정지됩니다. 외출 중 24/7 동작이 필요하면:

- 시스템 설정 → 배터리 → 전원 어댑터 → "잠자기 방지" 체크
- 클램쉘 모드 (외부 전원·디스플레이·키보드 연결 시 노트북 닫아도 깨어 있음)
- 또는 `caffeinate -di .venv/bin/jira-bot` 형태로 foreground 실행

### 안전장치

- self-repo 가드, dirty-check (시작 거부)
- claude 호출당 600s · 전체 30분 cap · 재시도 max 3회
- `--permission-mode acceptEdits` + `--disallowedTools Bash WebFetch WebSearch`
- `SLACK_USER_ID` allowlist 외 silent ignore

---

## 7. 파일 구성

```
jira_daily_digest.py     # A. Daily Digest 메인 스크립트
bot.py                   # B. Slack DM 봇 진입점 (Socket Mode)
bot_lib/                 # 봇 라이브러리
├─ commands.py           #   run / jira create / jira get 파서
├─ registry.py           #   projects.toml 파싱 (tomllib)
├─ jira_client.py        #   Jira REST + ADF 변환 + create_issue + search_my_issues
├─ git_ops.py            #   git 래퍼 (find_files, branches, push, ...)
├─ claude_runner.py      #   claude -p Popen + 콜백
├─ test_runner.py        #   pytest/gradle 등 외부 테스트 실행
├─ orchestrator.py       #   처리 흐름 + 재시도 + token 누적
├─ slack_handler.py      #   명령 라우팅 + 응답 포매팅
├─ context.py            #   ContextStore (JSON 영속)
├─ mutex.py              #   RepoMutex (repo 단위 직렬화)
├─ cancellation.py       #   진행 중 claude SIGTERM 추적
└─ setup_wizard.py       #   인터랙티브 wizard + 매 기동 토큰 라이브 검증

projects.toml            # 봇이 다룰 repo 등록 (TOML, 한 섹션 = 한 repo)
plan.md                  # 봇 설계 의도·결정사항
FEATURES.md              # 봇 기능 정의서·명령 카탈로그
AGENTS.md                # AI 에이전트용 자동 setup 가이드
launchd/                 # com.hjpark.jira-bot.plist 예시 + README
.env.example             # 환경 변수 템플릿 (디지스트 + 봇 공용)
pyproject.toml           # 패키지 메타데이터 + 의존성 + 콘솔 스크립트 (`jira-bot`, `jira-digest`)
tests/                   # pytest 385건 (live 마커 1건 opt-in)
```

---

## 8. 문제 해결

- `401 Unauthorized`: Jira 이메일/토큰/베이스 URL 다시 확인. 이메일은 표시 이름이 아니라 **로그인 이메일**. (봇 쪽은 매 기동의 라이브 검증이 이 에러를 먼저 잡아 `[error] 유효하지 않은 토큰 — JIRA_API_TOKEN: 401 ...` 로그를 stderr 에 찍고 wizard 를 띄웁니다.)
- `410 Gone` on `/rest/api/3/search`: Atlassian이 2025년에 해당 엔드포인트를 제거했습니다. 이 저장소는 이미 신 엔드포인트(`/rest/api/3/search/jql`, `nextPageToken` 페이지네이션)를 사용하므로 최신 코드로 받았는지 확인하세요.
- `not_in_channel` / `channel_not_found`: `SLACK_USER_ID`가 본인 user ID가 맞는지, Slack 앱이 워크스페이스에 설치됐는지 확인.
- 출력이 너무 길다: 스크립트가 2800자 단위로 자동 청크 분할합니다. 그래도 많으면 `JIRA_EXTRA_JQL`로 범위를 좁히세요.
- `--dry-run`으로 항상 먼저 확인하세요. 실제 DM 보내기 전에 Block 구조가 어떻게 생겼는지 JSON으로 찍어줍니다.
- `요약 실패: ReadTimeout` 같은 메시지가 떠도 스크립트는 멈추지 않고 그 이슈만 요약 없이 전송됩니다. 빈번하면 `summarize_issue`의 `timeout` 값을 늘리세요.
