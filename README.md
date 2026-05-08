# Jira ↔ Slack 자동화 도구

내 Jira 이슈를 매일 아침 Slack DM 으로 자동 받아보고, 외출 중에도 Slack DM 한 줄로 코드 작업을 시킬 수 있는 개인용 도구입니다.

> 이 README 는 **개발 경험이 적은 사용자도 따라갈 수 있도록** 설치 위주로 정리돼 있습니다. 명령 옵션·내부 동작 등 더 자세한 내용은 [`FEATURES.md`](./FEATURES.md), 설계 의도는 [`plan.md`](./plan.md), AI 에이전트가 따라가는 자동 setup 스크립트는 [`AGENTS.md`](./AGENTS.md) 참조.

---

## 이 도구로 무엇을 할 수 있나요?

저장소 안에 두 가지 도구가 들어 있습니다. **둘 중 하나만 써도 되고, 둘 다 켜도 됩니다.**

| | 무엇을 하나요? | 누구한테 좋은가요? |
|---|---|---|
| **A. Daily Digest** | 매일 아침 내게 할당된 Jira 이슈를 정리·요약해서 Slack DM 으로 보내줍니다. | "오늘 뭐부터 해야 하지?" 를 매일 아침 자동으로 보고 싶은 분 |
| **B. Slack 명령 봇** | Slack DM 에 한 줄(`run fix CDS-99 ...`) 보내면 봇이 알아서 코드 수정 → 테스트 → PR 생성까지. Slack DM 으로 새 Jira 이슈도 만들 수 있습니다. | 외출 중에도 iPhone Slack 으로 일을 시키고 싶은 분 |

> **처음이라면 A 부터.** A 만 쓰면 Slack 앱 설정이 절반으로 줄고, 결과를 바로 눈으로 확인할 수 있어 검증이 쉽습니다.

---

## 0. 시작하기 전에 — 사전 준비

다음이 컴퓨터에 있어야 합니다. 터미널에서 한 줄씩 쳐서 확인해 보세요.

### 0-1. Python 3.11 이상 (필수)

```bash
python3 --version
```

`Python 3.11.x` 이상이면 OK. 그렇지 않으면:

- macOS: https://www.python.org/downloads/ 에서 최신 버전 설치
- 또는 [Homebrew](https://brew.sh/) 설치 후 `brew install python@3.12`

### 0-2. Git (필수)

```bash
git --version
```

`command not found` 가 나오면 macOS 는 `xcode-select --install` 한 줄로 설치됩니다.

### 0-3. (B. 봇만 쓸 때) GitHub CLI + 코딩 에이전트 CLI

봇이 PR 을 만들려면 [GitHub CLI](https://cli.github.com/) 가 필요합니다. macOS 는 `brew install gh && gh auth login`.

또한 코드를 자동으로 고쳐줄 코딩 에이전트 CLI 가 한 개 깔려 있고 로그인돼 있어야 합니다. 추천: [Claude Code](https://docs.claude.com/en/docs/claude-code/quickstart) (`claude` 명령). 이미 평소에 쓰고 있다면 추가 설정 없음.

> A 디지스트만 쓸 거면 0-3 은 건너뛰어도 됩니다. (디지스트도 요약을 만들 때 코딩 에이전트 CLI 를 쓸 수는 있지만, 없어도 요약만 빠지고 동작합니다.)

---

## 1. 토큰 발급 — Jira·Slack 에서 "비밀번호 같은 것" 받아오기

이 도구가 내 Jira·Slack 에 접속하려면 두 서비스에서 **토큰(token)** 을 발급받아야 합니다. 토큰은 비밀번호처럼 다뤄야 하고, 이 저장소는 토큰이 git 에 올라가지 않도록 `.gitignore` 에 미리 막아뒀습니다.

발급받은 값은 **메모장에 임시로 모아두세요.** 다음 장에서 한꺼번에 입력하게 됩니다.

### 1-1. Jira API 토큰

1. https://id.atlassian.com/manage-profile/security/api-tokens 접속 (Atlassian 계정 로그인)
2. **Create API token** 클릭 → 이름은 아무거나 (예: `daily-digest`) → **Create**
3. **Copy** 로 복사 — 이 화면을 닫으면 다시 못 봅니다.

### 1-2. Slack 봇 만들기 (A·B 공통)

1. https://api.slack.com/apps 접속 → **Create New App** → **From scratch** → 본인 워크스페이스 선택, 이름 아무거나 (예: `My Jira Helper`)
2. 좌측 **OAuth & Permissions** → **Bot Token Scopes** → **Add an OAuth Scope** 두 개 추가:
   - `chat:write` (DM 보내기)
   - `im:write` (DM 채널 열기)
3. 페이지 상단으로 돌아가 **Install to Workspace** → 권한 허용
4. 설치 직후 보이는 **Bot User OAuth Token** (`xoxb-...` 로 시작) 복사
5. **본인 Slack 사용자 ID 확인**: Slack 앱에서 본인 프로필 클릭 → 점 세 개(`⋮`) 메뉴 → **Copy member ID** → `U01ABC23DEF` 형태의 값

### 1-3. (B. 봇 쓸 때만) Slack 추가 설정

봇이 DM 을 **받으려면** 같은 Slack 앱 페이지에서 4가지를 더 켜야 합니다.

1. 좌측 **Basic Information** → 아래쪽 **App-Level Tokens** → **Generate Token and Scopes** → scope `connections:write` 추가 → **Generate** → `xapp-...` 토큰 복사
2. 좌측 **Socket Mode** → 토글 ON
3. 좌측 **App Home** → **Show Tabs** 섹션 → **Messages Tab** 토글 ON, 그 아래 체크박스 **"Allow users to send Slash commands and messages from the messages tab"** 체크 ✅
4. 좌측 **Event Subscriptions** → 토글 ON → **Subscribe to bot events** 에 `message.im` · `app_mention` 추가
5. 페이지 상단에 "reinstall your app" 배너가 뜨면 클릭

> 4단계 중 하나라도 빠지면 Slack 메시지가 봇에게 도달조차 안 합니다. 자세한 체크리스트는 [`AGENTS.md` §1.1](./AGENTS.md) 참조.

### 정리: 받아둔 값 체크

| 값 | 형태 | 어디서 |
|---|---|---|
| Jira 회사 도메인 | `https://회사명.atlassian.net` | 평소 쓰는 Jira URL 의 루트 |
| Jira 로그인 이메일 | `you@company.com` | Jira 로그인 이메일 (표시 이름 X) |
| Jira API 토큰 | `ATATT3xFf...` | 1-1 |
| Slack Bot Token | `xoxb-...` | 1-2 step 4 |
| Slack 사용자 ID | `U01ABC23DEF` | 1-2 step 5 |
| (B 만) Slack App Token | `xapp-...` | 1-3 step 1 |

---

## 2. 설치

터미널에서 이 저장소 폴더로 이동한 뒤:

```bash
# 가상환경 만들기 (이 도구만의 격리된 Python 공간)
python3 -m venv .venv
source .venv/bin/activate

# 도구 설치 — 30초~1분
pip install -e .
```

설치가 끝나면 `jira` 라는 새 명령어가 등록됩니다.

> **터미널을 새로 열 때마다** `source .venv/bin/activate` 를 한 번 쳐야 `jira` 명령이 잡힙니다. 안 그러면 `command not found: jira`.

---

## 3. 첫 실행 — 자동 설정 마법사

설정 파일을 손으로 만들 필요 없습니다. 그냥:

```bash
jira
```

라고 치면 마법사(wizard)가 떠서 §1 에서 받아둔 값들을 하나씩 물어봅니다.

- **시크릿(토큰) 입력은 화면에 표시되지 않습니다** — 안심하고 붙여넣기 (cmd+V)
- 입력이 끝나면 Jira·Slack 에 실제로 통화해서 **토큰이 살아있는지 검증**
- 통과하면 그대로 봇 실행 (B), 검증만 하고 끝내고 싶으면 `Ctrl+C`
- 토큰이 잘못됐으면 **그 키만** 다시 물어봄 (멀쩡한 값은 그대로)

이후 토큰이 만료됐거나 값을 바꾸고 싶으면 `jira setup` 을 다시 실행하면 됩니다.

---

## 4. (A) Daily Digest 사용하기

### 4-1. 먼저 미리보기 (Slack 안 보냄)

```bash
jira digest --dry-run
```

`📋 Jira 할당 이슈 N건` 같은 로그가 뜨고, Slack 으로 보낼 메시지의 형태가 콘솔에 출력됩니다. 이슈가 0건이라고 뜨면 Jira 에서 본인한테 할당된 열린 이슈가 없거나, JQL 필터(아래 §4-4) 가 너무 빡빡한 것.

### 4-2. 진짜로 Slack DM 받아보기

```bash
jira digest
```

본인 Slack 에 DM 이 도착했는지 확인.

### 4-3. 매일 아침 9시에 자동으로 받기 (cron)

`crontab -e` 를 터미널에서 열고 (vim 이 뜹니다 — `i` 누르면 입력, `Esc` 후 `:wq` 로 저장):

```cron
0 9 * * 1-5 /이/저장소의/절대경로/.venv/bin/jira digest >> /이/저장소의/절대경로/digest.log 2>&1
```

위 한 줄이면 평일 오전 9시마다 자동 실행됩니다.

> **macOS 노트북이 자고 있으면 cron 은 안 뜹니다.** 항상 켜둘 자신이 없으면 GitHub Actions 같은 클라우드 cron 으로 옮기는 것을 고려하세요. (고급 — 이 README 범위 밖)

### 4-4. 자주 쓰는 커스터마이즈

`.env` 파일에서 한 줄을 추가/수정하면 됩니다 (`jira setup` 으로 넣어도 되고, 직접 편집해도 됨):

- **할 일 상태만 보내기** (추천): `JIRA_EXTRA_JQL='statusCategory = "To Do"'`
- **특정 프로젝트만**: `JIRA_EXTRA_JQL='project = ABC'`
- **요약 끄기**: `LLM_BACKEND=none`

더 다양한 커스터마이즈 (요약 톤 변경, 정렬 기준, 출력 포맷, API 백엔드 사용 등) 는 [`AGENTS.md` §2](./AGENTS.md), 또는 `jira_daily_digest.py` 의 `sort_issues()` / `format_issue_line()` / `SUMMARY_SYSTEM` 부분 참조.

---

## 5. (B) Slack 명령 봇 사용하기

### 5-1. 다룰 코드 저장소 등록 — `projects.toml`

봇이 작업할 코드 저장소를 `projects.toml` 에 적습니다. 한 섹션 = 한 저장소.

```toml
[my-api]
path = "/Users/내이름/dev/my-api"      # 이 컴퓨터의 절대 경로
default_branch = "main"                # 기본 브랜치
remote = "origin"                      # (선택) git remote 이름, 기본값 "origin"
test_cmd = "pytest"                    # (선택) 테스트 명령. 비우면 테스트 스킵
```

여러 저장소를 등록하려면 섹션을 더 추가합니다.

### 5-2. 봇 켜기

```bash
jira
```

`✓ Bot started` 로그가 뜨면, Slack 에서 봇 앱과의 DM 창을 열고 한 줄 보내보세요:

```
help
```

봇이 사용 가능한 명령 목록을 답해줍니다.

### 5-3. 자주 쓰는 명령

| Slack DM 한 줄 | 봇이 하는 일 |
|---|---|
| `help` | 사용법 보기 |
| `init my-api` | 이번 세션에서 다룰 저장소 지정 |
| `run fix CDS-99 -d 버튼이 두 번 클릭됨` | CDS-99 이슈 fetch → 코드 수정 → 테스트 → 커밋 → PR 생성까지 |
| `jira create -k 버그 -t 로그인 안 됨` | Jira 에 새 이슈 등록 |
| `jira get` | 내가 맡은 이슈 목록 |
| `cancel my-api` | 진행 중인 작업 즉시 중단 |
| `cleanup my-api` | 작업 도중 깨진 워킹 트리 초기화 |

`run` 의 `<type>` 자리에 들어갈 수 있는 값: `fix` / `feat` / `refactor` / `chore` / `docs` / `test` / `perf`. 전체 명령 카탈로그·옵션은 [`FEATURES.md`](./FEATURES.md).

### 5-4. 24시간 켜두기 (launchd)

맥에서 봇을 항상 켜두려면:

```bash
jira install     # macOS launchd 에 등록
jira status      # 동작 상태 확인
jira logs -f     # 로그 실시간 보기
jira logs --err  # 에러 로그만
jira uninstall   # 해제
```

> macOS 가 잠들면 봇도 멈춥니다. 시스템 환경설정 → 배터리 → 전원 어댑터 → "잠자기 방지" 체크 권장. 노트북 닫고 외출하려면 클램쉘 모드(외부 전원·디스플레이·키보드 연결) 권장.

---

## 6. 문제 해결 (FAQ)

**Q. `command not found: jira` 가 나옵니다.**
A. 가상환경 활성화가 빠졌습니다. 터미널을 새로 열 때마다 먼저 `source .venv/bin/activate`.

**Q. `401 Unauthorized` — Jira 인증 실패.**
A. 토큰·이메일 중 하나가 틀렸습니다. `jira setup` 으로 다시 입력. 이메일은 **Jira 로그인 이메일** (표시 이름 X).

**Q. `jira digest` 는 잘 도는데 Slack DM 이 안 옵니다.**
A. ① Slack 앱에서 만든 봇과 DM 창을 한 번 열어두세요. ② `SLACK_USER_ID` 가 본인 ID 가 맞는지 확인.

**Q. 봇한테 Slack DM 을 보냈는데 답이 없습니다.**
A. §1-3 의 4가지 (App-Level Token + Socket Mode + Messages Tab + Event Subscriptions) 가 모두 ON 인지 다시 확인. 하나라도 빠지면 메시지가 봇한테 도달조차 안 합니다.

**Q. 토큰을 잘못 입력했어요.**
A. `jira setup` 을 다시 실행. 빈 입력으로 Enter 하면 기존 값 유지.

**Q. 더 자세한 동작·에러를 보고 싶어요.**
A. `jira validate` (토큰만 검증), `jira status` (전체 상태), `jira logs -f` (실시간 로그).

**Q. 코드를 바꾼 뒤 봇을 재기동하고 싶어요.**
A. `launchctl kickstart -k gui/$(id -u)/local.jira-bot`

---

## 7. 더 알아보기 (개발자용)

- 봇 명령 카탈로그·옵션 전체: [`FEATURES.md`](./FEATURES.md)
- 설계 의도·결정사항: [`plan.md`](./plan.md)
- AI 에이전트가 자동 setup 시 따라가는 스크립트: [`AGENTS.md`](./AGENTS.md)
- 환경변수 전체 목록·설명: [`.env.example`](./.env.example)
- 테스트 실행: `pip install -e ".[dev]"` 후 `pytest` (385건 + opt-in live 1건)
- 디지스트 안전장치, API 백엔드 사용, mock 모드 (`--mock --dry-run --no-llm`), GitHub Actions cron 등: 위 문서들 참조
- 봇 안전장치 (self-repo 가드, dirty-check, 호출 cap, allowlist) 는 [`plan.md`](./plan.md) 참조

### 파일 구조 (요약)

```
jira_daily_digest.py     # A. Daily Digest 메인
bot.py                   # B. Slack 봇 진입점
bot_lib/                 # 봇 라이브러리 (commands, jira_client, claude_runner, ...)
jira_cli.py              # `jira <subcommand>` 디스패처
projects.toml            # 봇이 다룰 repo 등록
launchd/                 # local.jira-bot.plist 예시
.env.example             # 환경변수 템플릿
pyproject.toml           # 패키지 메타데이터 + 의존성
tests/                   # pytest
```
