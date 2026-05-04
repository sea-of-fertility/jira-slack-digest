# AI 자동 세팅 플레이북

이 문서는 **AI 에이전트(Claude Code 등)** 가 읽고 따라가며 이 프로젝트를 처음 쓰는 사용자에게 자동 설정해 주기 위한 스크립트입니다. 사람이 읽어도 되지만, 각 섹션은 AI가 한 스텝씩 실행·검증하도록 짜여 있습니다.

---

## 0. AI에게 주는 규칙

1. **절대 `.env` 를 git에 커밋하지 말 것.** `.gitignore`에 이미 포함됐는지 확인하고, 없으면 추가부터.
2. 사용자에게 값을 받기 전 **반드시 프롬프트로 물을 것.** 추측·하드코딩 금지.
3. 각 단계는 **순서대로** 실행하고, 실패하면 다음 단계로 넘어가지 말 것.
4. Slack에 실제 DM을 보내는 최종 단계(`8`)는 사용자 명시 승인 후에만 실행.
5. 시크릿 값(`*_TOKEN`, `*_API_KEY`)은 **출력에 그대로 찍지 말 것.** 필요하면 앞 6자/뒤 4자만 마스킹해 표시.

---

## 1. 사용자에게 받아야 하는 필수 입력 (6개)

아래 여섯 값만 받으면 나머지는 전부 AI가 세팅합니다. 하나라도 비면 단계 진행 불가.

| 키 | 예시 | 얻는 법 |
|---|---|---|
| `JIRA_BASE_URL` | `https://your-company.atlassian.net` | 회사 Jira 도메인. `/browse/...` 빼고 루트만. |
| `JIRA_EMAIL` | `you@company.com` | Jira 로그인 이메일(표시 이름 아님). |
| `JIRA_API_TOKEN` | `ATATT3xFf...` | https://id.atlassian.com/manage-profile/security/api-tokens → Create API token |
| `SLACK_BOT_TOKEN` | `xoxb-123-456-...` | https://api.slack.com/apps → 앱 생성 → OAuth & Permissions → `chat:write`, `im:write` → Install → Bot User OAuth Token |
| `SLACK_APP_TOKEN` | `xapp-1-...` | https://api.slack.com/apps → 해당 앱 → **Basic Information** → Scroll down to **App-Level Tokens** → Generate Token and Scopes → scope `connections:write` 추가 → Generate. Socket Mode (`bot.py`) 전용. |
| `SLACK_USER_ID` | `U01ABC23DEF` | Slack에서 본인 프로필 → 점 세 개 메뉴 → Copy member ID |

AI는 위 여섯 값을 사용자에게 한 번에 요청하되, `JIRA_API_TOKEN`·`SLACK_BOT_TOKEN`·`SLACK_APP_TOKEN`은 **입력 즉시 메모리에서만 다룰 것** (터미널 스크롤백에 남지 않게).

### 1.1 Slack App 추가 설정 (Socket Mode / DM 수신용)

`bot.py` 가 Slack DM·Slash command 를 수신하려면 https://api.slack.com/apps → 해당 앱 화면에서 아래 항목들을 **반드시 활성화**해야 함. AI는 사용자에게 체크리스트로 안내하고, 각 항목 완료 여부를 한 줄씩 확인.

1. **Socket Mode 활성화**
   - 좌측 메뉴 → **Socket Mode** → Enable Socket Mode 토글 ON
   - (App-Level Token 미생성 시 위 1번 표 안내대로 먼저 생성)

2. **App Home → Messages Tab 활성화**
   - 좌측 메뉴 → **App Home** → **Show Tabs** 섹션
   - **Messages Tab** 토글 ON
   - 그 아래 체크박스 **"Allow users to send Slash commands and messages from the messages tab"** 도 반드시 체크 ✅
   - (이 체크가 없으면 사용자가 봇 DM 입력창에 메시지를 못 씀 → 명령 입력 자체가 불가)

3. **Event Subscriptions 활성화**
   - 좌측 메뉴 → **Event Subscriptions** → Enable Events 토글 ON
   - **Subscribe to bot events** 에 최소: `message.im` (DM 수신), `app_mention` (멘션 수신) 추가
   - Socket Mode 사용 중이므로 Request URL 입력란은 무시 (회색으로 비활성)
   - 저장 후 페이지 상단에 **"reinstall your app"** 배너가 뜨면 클릭하여 재설치 (스코프 갱신 반영)

4. **Slash Commands** (선택, `/run` 등 슬래시 명령 쓸 때)
   - 좌측 메뉴 → **Slash Commands** → Create New Command 로 등록
   - Socket Mode 이므로 Request URL 은 비워둬도 OK

검증: 위 4개 (또는 슬래시 명령 미사용 시 3개) 항목이 모두 ON 상태인지 사용자에게 스크린 한번 확인 요청. 누락 시 `bot.py` 실행해도 메시지가 도착하지 않음.

---

## 2. 선택 입력 (기본값 있음)

사용자가 안 주면 AI가 아래 기본값으로 세팅. 각 값의 의미는 사용자에게 한 줄로 설명해준 뒤 "기본값 쓸게요" 로 넘어가면 됨.

| 키 | 기본값 | 의미 |
|---|---|---|
| `JIRA_EXTRA_JQL` | `'statusCategory = "To Do"'` | 할 일 상태 이슈만 필터. 비활성은 `# JIRA_EXTRA_JQL=` 주석 처리. |
| `LLM_BACKEND` | `cli` | `cli` / `api` / `none` |
| `LLM_CLI` | `"claude -p --model haiku"` | CLI 모드에서 쓸 명령. Haiku가 저렴·빠름. |
| `LLM_CLI_TIMEOUT` | `90` | 초 단위 |

---

## 3. 사전 점검

```bash
python3 --version   # 3.10+
which claude        # LLM_BACKEND=cli 를 쓸 때만 필요; 없으면 api/none 로 폴백 권장
```

- `python3` 없으면 **중단** → 사용자에게 "Python 3.10+를 설치해 주세요"
- `claude` 없으면 `LLM_BACKEND=none` 로 내려서 진행 (요약만 생략, 동작은 OK)

---

## 4. `.env` 생성

`.env.example` 을 복사해 사용자 값으로 치환. 값은 **반드시 쌍따옴표로 감쌀 것** — 공백 포함 값 (`claude -p --model haiku`) 이 `source` 시 깨지는 것 방지.

```bash
cp .env.example .env
```

치환 대상 (Edit 툴로 한 줄씩 교체):

```
JIRA_BASE_URL=<받은 값, 끝 슬래시 제거>
JIRA_EMAIL=<받은 값>
JIRA_API_TOKEN=<받은 값>
SLACK_BOT_TOKEN=<받은 값>
SLACK_APP_TOKEN=<받은 값>     # bot.py Socket Mode 용
SLACK_USER_ID=<받은 값>

# 아래는 기본값
JIRA_EXTRA_JQL='statusCategory = "To Do"'
LLM_BACKEND=cli
LLM_CLI="claude -p --model haiku"
```

생성 후 검증:

```bash
set -a && source .env && set +a
echo "base=$JIRA_BASE_URL email=$JIRA_EMAIL token=${JIRA_API_TOKEN:0:6}...${JIRA_API_TOKEN: -4}"
```

- `command not found` 경고 나오면 **따옴표 누락** → 다시 확인.
- 토큰 전체를 echo 하지 말 것.

---

## 5. `.gitignore` 확인 및 보강

반드시 아래 엔트리가 존재해야 함. 없으면 추가.

```
.env
.env.local
.env.*.local
.venv/
__pycache__/
.idea/
*.log
```

검증 (git repo 안에서):

```bash
git check-ignore -v .env   # → ".gitignore:N:.env  .env"  가 뜨면 OK
```

---

## 6. 의존성 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` 는 `requests` 한 개뿐이라 30초 이내.

---

## 7. 단계별 dry-run 검증 (Slack 안 보냄)

각 단계는 exit code 0 확인. 실패 시 다음으로 진행 금지.

### 7.1 Mock + no-LLM — 포맷 구조 확인
```bash
python jira_daily_digest.py --mock --dry-run --no-llm
```
기대: `"type": "header"` 블록 포함된 JSON 출력.

### 7.2 Mock + CLI 요약 — LLM 파이프라인 확인
```bash
python jira_daily_digest.py --mock --dry-run --backend cli
```
기대: 4개 이슈 각 라인 아래 `📝 …` 요약 붙음. 실패 시 `LLM_BACKEND=none` 로 폴백하고 사용자에게 알림.

### 7.3 실제 Jira + dry-run — 인증/JQL 확인
```bash
python jira_daily_digest.py --dry-run
```
기대: `📋 Jira 할당 이슈 N건` 로그, exit 0. 실패 패턴별 대응:

| 증상 | 원인 | 조치 |
|---|---|---|
| `401 Unauthorized` | 토큰/이메일/URL 중 틀림 | `.env` 재확인 |
| `410 Gone` on `/rest/api/3/search` | 구 엔드포인트 | 최신 코드 확인 (현 코드는 `/search/jql` 사용) |
| `HTTPError` with JQL error | `JIRA_EXTRA_JQL` 문법 오류 | 따옴표/대소문자 확인 |
| 이슈 0건 | JQL이 너무 빡빡 | `JIRA_EXTRA_JQL` 완화 제안 |

---

## 8. 실제 Slack 전송 (사용자 승인 후)

`7.3` 까지 통과했으면 **사용자에게 명시적으로** "실제 DM을 보냅니다. 진행할까요?" 질문. "예" 받은 후에만:

```bash
python jira_daily_digest.py
```

기대: `[ok] posted to Slack (ts=..., channel=D...)`.

### 8.1 수신 확인

봇 토큰에 `im:history` 스코프가 보통 없으므로 본문 조회는 불가. 대신 permalink로 메시지 존재만 확인:

```bash
curl -s "https://slack.com/api/chat.getPermalink?channel=<반환된 channel>&message_ts=<반환된 ts>" \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN"
```

`{"ok":true,"permalink":"https://..."}` 나오면 성공. 사용자에게 permalink를 전달하고 "Slack에서 직접 확인해 보세요" 로 마무리.

---

## 9. 자동 실행 (선택)

사용자가 "매일 아침 자동" 을 원하면 `README.md § 5` 를 참조해 cron/launchd 설정. AI가 자동으로 cron을 수정하지는 말고, `run.sh` 만 생성해두고 `crontab -e` 는 사용자가 직접 하도록 안내.

---

## 10. 정리 체크리스트 (AI가 마지막에 사용자에게 보고)

- [ ] `.env` 생성 완료 (파일 존재·따옴표 OK, `SLACK_APP_TOKEN` 포함)
- [ ] `.gitignore` 에 `.env` 포함 확인
- [ ] Slack App: Socket Mode ON / Messages Tab ON + "Allow users to send..." 체크 / Event Subscriptions ON (`message.im`, `app_mention`)
- [ ] `python3 -m venv .venv` + `pip install -r requirements.txt` 완료
- [ ] dry-run 3종 통과 (mock/no-llm, mock/cli, real/dry-run)
- [ ] 실제 발송 1회 성공, permalink 확인
- [ ] (선택) cron 안내 전달

이 중 실패한 항목이 있으면 사용자에게 원인·해결책을 함께 보고.
