# 대시보드 인터랙티브 확장 계획 (v1)

**상태**: 초안 — 사용자 리뷰 대기
**작성일**: 2026-07-12
**개정**: 2026-07-12 — §8 텔레그램 봇을 커스텀 구현에서 공식 `telegram@claude-plugins-official`
플러그인 기반으로 교체(§3, §7.3, §8, §10, §11, §12, §14 반영)
**작성 배경**: 현재 `python scripts/build_dashboard.py`는 스캔 결과(`data/scans/*.json`)와 저널 요약을
읽어 정적 HTML을 렌더링하는 읽기 전용 모니터링 페이지다(`scripts/build_dashboard.py`, 141줄,
표준 라이브러리 `http.server`만 사용). 이 문서는 이를 "클릭 한 번으로 분석을 트리거하고, 결과를
세션으로 저장·조회하며, 웹/모바일/텔레그램으로 인터랙티브하게 운영할 수 있는" 시스템으로 확장하는
계획을 정리한다.

---

## 1. 요구사항 정리 (사용자 확정 사항)

| # | 항목 | 확정 내용 |
|---|---|---|
| 1 | 권한 | 백그라운드 트리거 실행 시 대화형 승인 프롬프트 없이 동작하는 것에 동의 |
| 2 | 트리거 종류 | 개별 종목 트리거 = 해당 종목 단독 trade-decision 분석. 복수 종목 트리거 = 종목 간 비교 분석(오늘 세션에서 한 C/JPM/HIG 비교와 동일 패턴) |
| 3.1 | 결과 표시(스캔 테이블) | 각 종목 행에 BUY/HOLD/SELL + 확신도 배지 부착 |
| 3.2 | 결과 표시(상세) | "결과" 탭 신설 — 개별/비교 리포트 전문을 보여줌. 분석 세션마다 데이터로 영구 저장. 우측에 History 패널로 과거 세션 탐색 |
| 4-a | 로그 | 각 분석에 사용된 모든 프롬프트(및 도구 호출)를 남기는 "로그" 탭 신설 |
| 4-b | 인프라 | 정적 운영 → 백엔드/프론트엔드 구조로 전환, 웹/모바일 접근 지원 |
| 4-c | 봇 | 텔레그램 명령으로 분석을 트리거하고 결과를 받는 봇 운영 — 사용자와 인터랙티브 운영 |

## 2. 현재 상태 (베이스라인)

- `scripts/build_dashboard.py`: 정적 렌더러 + `--serve` 시 `ThreadingHTTPServer`가 매 요청마다
  최신 스캔/저널 요약을 다시 렌더링해서 내려줌. 상태 저장 없음, API 없음.
- `scripts/dashboard_template.html`: 클라이언트 JS가 주입된 JSON(`/*__DATA__*/`)을 읽어 테이블/차트를
  그림. 티커 행은 `<span class="tkr">${e.ticker}</span>` 형태.
- `scripts/journal.py` + `tradingagents/scanner/journal.py`: append-only `data/journal/journal.jsonl`에
  pick/decision/review 기록. `journal.py decision <TICKER> <BUY|HOLD|SELL> --conviction --note`로
  기록, `summary()`로 저널 스코어카드 집계.
- `.claude/settings.local.json`: 이미 `"Bash(python *)"` 허용 — market_data.py/journal.py 호출은
  현재 세션에서도 승인 없이 실행됨. 이 파일이 헤드리스 실행 시 권한 범위의 기준점이 된다.
- 트리거·세션 저장·로그·원격접근·봇 — 전부 미구현.

## 3. 아키텍처 개요

```
┌─────────────┐      ┌───────────────────────┐      ┌────────────────────┐
│  웹 대시보드  │ ───▶ │   백엔드 (FastAPI)      │ ───▶ │  Job Worker Pool    │
│ (탭: 스캔/   │ ◀─── │  /api/analyze          │      │  claude -p 서브프로세스│
│  결과/History│ SSE  │  /api/sessions         │◀────▶│  (헤드리스 실행)      │
│  /로그)      │      │  /api/sessions/{id}    │      └─────────┬──────────┘
└─────────────┘      │  /api/sessions/{id}/log │                │
                      └────────────────────────┘                ▼
┌─────────────┐      ┌───────────────────────┐      ┌────────────────────┐
│  텔레그램     │◀───▶│ telegram@claude-plugins-│─────▶│ journal.jsonl (기존) │
│ (사용자 DM)  │      │ official (MCP, 상시세션) │      │ + analyses/*.json   │
└─────────────┘      └───────────────────────┘      │ + analyses/*.jsonl  │
                                                       └────────────────────┘
```

- **웹 트리거와 텔레그램 트리거는 실행 경로가 다르다** — 웹은 백엔드가 요청마다 `claude -p` 헤드리스
  서브프로세스를 띄우는 방식(§5)이고, 텔레그램은 [공식 `telegram` MCP 플러그인](https://github.com/anthropics/claude-plugins-official/tree/main/external_plugins/telegram)이
  붙은 **상시 실행 중인 대화형 Claude Code 세션**이 DM을 그대로 프롬프트로 받아 처리한다(§8). 두
  경로 모두 자체 분석 로직을 갖지 않고 동일한 스킬(trade-decision 등)을 그대로 호출한다는 점은
  같다 — "누가 트리거했든 같은 스킬, 같은 저장 규약"이 단일 정보원칙의 핵심이다.
- **저널은 계속 진실의 원천(source of truth)**으로 유지한다. 배지는 저널의 `decision` 엔트리를
  기준으로 표시하고, "결과" 탭의 리포트 본문은 별도 세션 파일에 저장한다(저널은 요약용,
  세션 파일은 전문 보관용으로 역할 분리). 텔레그램에서 트리거된 분석도 **같은 세션 파일 규약**
  (`data/dashboard/analyses/<id>.json`)을 따라야 결과/History/로그 탭에 소스 구분 없이 함께
  나타난다 — §8 참고.

## 4. 데이터 모델

### 4.1 분석 세션 (`data/dashboard/analyses/<session_id>.json`)

```jsonc
{
  "id": "20260712-103245-aapl",          // {timestamp}-{tickers 요약}
  "type": "single" | "compare",
  "tickers": ["AAPL"],                    // compare는 여러 개
  "source": "dashboard" | "telegram" | "cli",
  "requested_by": "web" | "<telegram_user_id>",
  "requested_at": "2026-07-12T10:32:45+09:00",
  "completed_at": "2026-07-12T10:37:02+09:00",
  "status": "queued" | "running" | "done" | "error",
  "decisions": {                          // 저널에서 교차 조회해 캐시 (배지용)
    "AAPL": {"decision": "BUY", "conviction": "Medium-High"}
  },
  "report_markdown": "...",               // 최종 응답 전문 (결과 탭에 렌더링)
  "transcript_path": "analyses/20260712-103245-aapl.jsonl",  // 로그 탭용 원본 이벤트 스트림
  "error": null
}
```

### 4.2 로그(트랜스크립트) — `data/dashboard/analyses/<session_id>.jsonl`

`claude -p ... --output-format stream-json`으로 캡처한 원본 이벤트 스트림(최초 프롬프트, 서브에이전트
디스패치, Bash/도구 호출과 결과, 최종 응답)을 그대로 JSONL로 저장. "로그" 탭은 이를 타임라인으로
렌더링한다 — 단순 프롬프트 1줄이 아니라 "이 분석이 실제로 어떤 도구를 어떤 인자로 호출했는지"까지
투명하게 보여주는 것이 목표.

### 4.3 저장 위치 정리

| 데이터 | 위치 | 성격 |
|---|---|---|
| 배지/스코어카드 | `data/journal/journal.jsonl` (기존) | 요약, 이미 존재 |
| 세션 메타+리포트 | `data/dashboard/analyses/<id>.json` (신규) | 신규 |
| 세션 원본 로그 | `data/dashboard/analyses/<id>.jsonl` (신규) | 신규 |

## 5. API 설계 (초안)

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/analyze` | body `{tickers: string[], mode: "single"|"compare"}` → 잡 큐 등록, `session_id` 반환 |
| GET | `/api/sessions?limit=50&ticker=AAPL` | History 목록 (최신순) |
| GET | `/api/sessions/{id}` | 세션 상세(리포트 전문 포함) — 결과 탭용 |
| GET | `/api/sessions/{id}/log` | 트랜스크립트 — 로그 탭용 |
| GET | `/api/sessions/{id}/status` | 상태 폴링(초기 단계) |
| GET | `/api/stream` | SSE — 진행 중 잡 상태를 실시간 push (Phase 4에서 폴링 대체) |
| GET | `/api/scan` | 기존 스캔/저널 요약 (현재 서버사이드 렌더링을 API로 분리) |

인증(원격 노출 시): 모든 `/api/*`에 `Authorization: Bearer <DASHBOARD_TOKEN>` 필요(§8).

## 6. 프론트엔드 구성

기존 단일 페이지에 탭을 추가하는 구조로 확장(무거운 SPA 프레임워크 도입은 지양, 현재의 vanilla JS
스타일 유지):

- **스캔** (기존): KPI/차트/랭킹 테이블. 각 행에 "분석" 버튼 + 결과 배지. 다중 선택 체크박스 +
  "선택 종목 비교" 버튼 추가.
- **결과** (신규): 선택된 세션의 리포트 전문 렌더링(마크다운 → HTML). 우측 사이드바에 History
  리스트(티커/일자/결과 필터링 가능), 클릭 시 해당 세션 로드.
- **로그** (신규): 세션별 트랜스크립트 타임라인(프롬프트 → 도구 호출 → 서브에이전트 → 최종 응답).
- **테마/자동새로고침**: 기존 로직 유지.

## 7. 권한 & 보안

### 7.1 헤드리스 실행 권한 범위 (요구사항 #1)

`claude -p "<prompt>" --output-format stream-json`을 서브프로세스로 실행할 때, TTY가 없으므로
대화형 승인 프롬프트가 뜨면 그 잡은 그대로 멈춘다. 두 가지 방식 중 선택 필요:

- **(권장) 범위를 좁힌 허용목록**: `.claude/settings.local.json`에 이 파이프라인이 실제로 쓰는 것만
  명시적으로 허용 — `Bash(python scripts/market_data.py *)`, `Bash(python scripts/journal.py *)`
  등. Agent/Skill 도구 자체는 일반적으로 별도 승인이 필요 없음(서브에이전트 스폰은 이미 이번 세션에서
  프롬프트 없이 동작했음). 뉴스 에이전트가 외부 웹/뉴스 콘텐츠를 읽어오는 만큼, 임의 Bash나 파일쓰기
  범위를 넓히지 않는 것이 프롬프트 인젝션 방어선이 된다.
- **(비권장) `--dangerously-skip-permissions`**: 모든 승인을 생략. 구현은 가장 간단하지만, 외부
  콘텐츠(뉴스 헤드라인 등)에 악의적 지시가 섞여 있을 경우 의도치 않은 도구 호출로 이어질 잔여 위험이
  가장 큼. 이 문서는 이 옵션을 기본값으로 권장하지 않는다.

**결정 필요**: 위 두 방식 중 하나를 승인해달라 (권장: 좁힌 허용목록).

### 7.2 비용/동시성 가드레일

트리거 1건 = LLM API 호출 다수(단일 종목 3개 서브에이전트, 비교는 종목수×3). 무제한 트리거를
막기 위해:

- 동시 실행 잡 수 제한 (기본값 제안: 2)
- 일일 트리거 한도 (기본값 제안: 20건, `.env`로 조정 가능)
- 모든 트리거에 `source` 필드 기록(웹/텔레그램/CLI) — 남용 추적용

### 7.3 원격/모바일 노출 시 인증

로컬(`127.0.0.1`) 전용일 때는 별도 인증 불필요. 원격/모바일 접근을 열면(§9) 반드시:

- 단일 사용자 기준 Bearer 토큰(`.env`의 `DASHBOARD_TOKEN`) 발급 후 프론트엔드 로그인 화면에서 저장
- 텔레그램은 자체 접근 제어를 쓴다 — 페어링 후 반드시 `/telegram:access policy allowlist`로 전환해
  본인 계정만 응답받도록 잠근다(§8). 대시보드 자체 `.env` 변수가 아니라 플러그인 쪽 `access.json`이
  기준이라는 점에 유의.

## 8. 텔레그램 봇 (요구사항 4-c)

당초 초안은 "커스텀 파이썬 봇 + python-telegram-bot 라이브러리"를 전제로 했으나, Anthropic 공식
마켓플레이스(`claude-plugins-official`)에 이미 [`telegram` 플러그인](https://github.com/anthropics/claude-plugins-official/tree/main/external_plugins/telegram)이
있어 이를 그대로 활용한다 — 직접 구현 대비 훨씬 적은 코드로 동일한 결과를 얻는다. (참고: 공식
저장소의 `external_plugins/`에 있다는 것은 Anthropic이 마켓플레이스에 큐레이션해 배포한다는 뜻이지,
Anthropic이 직접 작성한 코드라는 뜻은 아니다 — 커뮤니티 기여 + 공식 유통.)

### 8.1 동작 방식

이 플러그인은 우리 백엔드 API를 호출하는 별도 봇이 **아니다**. Telegram 봇을 MCP 서버로 Claude
Code 세션에 붙여서, DM으로 온 메시지를 **그 세션에 그대로 프롬프트로 전달**하고 Claude가
`reply`/`react`/`edit_message` 도구로 답장하는 구조다. 즉 이 저장소 디렉터리에서 그 세션을 띄워두면
텔레그램 메시지가 오늘 우리가 대화하듯 스킬(trade-decision 등)을 그대로 트리거할 수 있다 — §5의
백엔드 API 경유가 필요 없다.

### 8.2 설치 및 설정

```bash
# 1. Bun 런타임 설치 (플러그인 실행 요건)
curl -fsSL https://bun.sh/install | bash

# 2. Claude Code 세션에서 플러그인 설치
/plugin install telegram@claude-plugins-official
/reload-plugins

# 3. BotFather(t.me/BotFather)에서 봇 생성 후 받은 토큰 등록
/telegram:configure <BOT_TOKEN>

# 4. 채널 플래그로 재실행해야 연결됨 — 이 저장소 루트에서
claude --channels plugin:telegram@claude-plugins-official
```

이후 텔레그램에서 봇에 DM → 6자리 페어링 코드 수신 → 세션에서 `/telegram:access pair <코드>` →
**반드시** `/telegram:access policy allowlist`로 전환(기본 `pairing` 상태로 두면 아무나 페어링 시도
가능, §7.3).

### 8.3 세션 저장 규약 통일

텔레그램으로 트리거된 분석도 결과/History/로그 탭에 함께 나타나야 하므로(§4.3), Claude가 텔레그램
채널로 trade-decision을 수행한 뒤 §4.1의 세션 파일 규약(`data/dashboard/analyses/<id>.json`,
`source: "telegram"`, `requested_by: <telegram user id>`)을 따르도록 지시문(스킬 또는 프로젝트
`CLAUDE.md`)에 명시한다 — 예: "텔레그램 채널에서 분석을 마치면 세션 기록 헬퍼로 결과를 남겨라." 이
헬퍼(예: `scripts/session_log.py`)는 웹 트리거 경로(헤드리스 `claude -p`)와 텔레그램 경로(상시
세션)가 공유하는 유일한 접점이 되며, Phase 2에서 함께 설계한다.

### 8.4 참고 명령 (플러그인이 이미 제공)

| 명령 | 용도 |
|---|---|
| `/telegram:configure <token>` | 봇 토큰 등록 |
| `/telegram:access pair <code>` | 페어링 완료 |
| `/telegram:access policy allowlist` | 접근 정책을 허용목록으로 전환(필수) |

우리 쪽에서 만드는 것은 슬래시 명령이 아니라 **자연어/트리거 문구를 trade-decision 스킬 호출로
연결하는 지시문**뿐이다 — "AAPL 분석해줘", "HIG랑 JPM 비교해줘" 같은 문장이 텔레그램에서도 오늘과
동일하게 동작해야 한다.

## 9. 원격/모바일 접근 (요구사항 4-b)

`0.0.0.0` 바인딩 자체는 쉽지만, 노출 방식에 따라 보안 리스크가 다르다:

| 방식 | 장점 | 단점 | 권장도 |
|---|---|---|---|
| **Tailscale (사설망)** | 설정 간단, 공인 인터넷 노출 없음, 모바일 앱 지원 | 기기마다 Tailscale 설치 필요 | ★ 1순위 권장 |
| Cloudflare Tunnel | 공인 URL 필요할 때 유용, 무료 | 공인 노출이므로 인증 필수 | 필요시 대안 |
| 공유기 포트포워딩 | — | 보안 위험 큼 | 비권장 |

모바일은 반응형 CSS로 충분한지, 아니면 "홈 화면에 추가(PWA)" 수준이 필요한지 확인이 필요하다.
네이티브 앱은 이번 범위에 포함하지 않는다(별도 논의 필요 시 확장).

**결정 필요**: 원격 접근 방식(Tailscale 권장) 확정.

## 10. 기술 스택 변경 제안

현재 `build_dashboard.py`는 표준 라이브러리 `http.server`만 사용하는 무의존성 스크립트다. 이번
확장(잡 큐, SSE, 인증 미들웨어, 라우팅 다수)은 표준 라이브러리만으로 유지보수하기 번거로워지므로
아래를 새 optional extra로 제안한다:

```toml
# pyproject.toml 신규 extra 제안
dashboard = ["fastapi>=0.115", "uvicorn>=0.30"]
```

기존 스캐너/CLI 코어는 지금처럼 의존성 최소 상태를 유지하고, 웹 백엔드를 실제로 쓰는 사람만
`pip install ".[dashboard]"`를 추가 설치하는 구조 — 레포의 기존 extras 철학(`bedrock`, `scanner`)과
동일한 패턴이다. 텔레그램은 §8로 대체되어 파이썬 패키지가 아니라 **Bun 런타임**이 별도 필요하다
(`curl -fsSL https://bun.sh/install | bash`) — `pyproject.toml`과는 무관.

**결정 필요**: FastAPI+Uvicorn 도입에 동의하는지, 아니면 표준 라이브러리 유지를 원하는지.

## 11. 단계별 실행 계획

작은 단위로 끊어서 각 단계마다 눈으로 확인 가능한 결과물을 내는 것을 목표로 한다.

| Phase | 범위 | 산출물 | 비고 |
|---|---|---|---|
| **1** | 개별 종목 트리거 + 배지 | 스캔 테이블 "분석" 버튼 → 헤드리스 실행 → 저널 기록 → 배지 표시 | 기존 stdlib 서버 확장으로 시작, 헤드리스 슬래시커맨드 동작을 실제 검증 |
| **2** | 비교 트리거 + 세션 저장 + 결과 탭 + History | 다중 선택 → 비교 분석 → `analyses/*.json` 저장 → 결과 탭/History 패널 | |
| **3** | 로그 탭 | `stream-json` 트랜스크립트 캡처 → 로그 탭 렌더링 | |
| **4** | 백엔드 전환 | stdlib → FastAPI, SSE로 실시간 갱신, 토큰 인증 도입 | 기술스택 결정(§10) 필요 |
| **5** | 원격/모바일 접근 | Tailscale(또는 대안) 연결, 반응형 UI 점검 | 접근 방식 결정(§9) 필요 |
| **6** | 텔레그램 연동 | 공식 `telegram` 플러그인 설치+페어링, 세션 저장 규약 연결(§8.3) | 봇 토큰 발급 필요, Bun 설치 필요 |

각 Phase 완료 시 다음 Phase 진행 여부를 확인받는다(한 번에 전부 구현하지 않음).

**의존성이 느슨해진 점**: 애초 Phase 6은 §5 백엔드 API를 전제로 순서를 매겼으나, 공식 플러그인은
백엔드를 거치지 않고 세션에 직접 붙으므로 **Phase 4(백엔드 전환) 완료를 기다릴 필요가 없다** —
원하면 Phase 2(세션 저장 규약이 정해진 시점) 직후로 앞당겨도 된다. 순서를 당길지는 §14에서 확인.

## 12. 리스크 & 미해결 사항

- **헤드리스 슬래시커맨드 동작 가정**: `claude -p "/trade-decision TICKER ..."`가 대화형과 동일하게
  스킬/서브에이전트를 인식하는지 Phase 1에서 실제로 검증이 필요한 가정 사항.
- **프롬프트 인젝션 잔여 위험**: news-analyst가 외부 뉴스를 읽어오므로, 허용목록을 아무리 좁혀도
  "허용된 스크립트를 예상외 인자로 호출" 같은 잔여 위험은 완전히 0이 되지 않는다. 허용목록을
  스크립트 경로 단위로 최대한 좁게 유지하는 것으로 리스크를 낮춘다(제거는 아님).
- **상시 구동 프로세스 관리**: 현재는 `--serve`를 수동 실행할 때만 떠 있음. 웹/모바일/봇을 상시
  운영하려면 백그라운드 프로세스 관리(예: macOS `launchd`)가 필요 — Phase 4~6 진행 시 별도 정리.
  **주의**: 이 프로세스를 상시로 켜두는 것은 웹서버를 상시 노출·운영하는 행위이므로, 실제 등록
  전에 반드시 최종 확인을 받는다.
- **기존 "정적 HTML, 무의존성" 철학과의 충돌**: 이 계획은 상시 구동 백엔드를 전제로 한다 — 필요할
  때만 켜는 현재 운영 방식과 다르다는 점을 인지하고 진행.
- **비용 남용**: 텔레그램/원격 노출은 트리거 진입점이 늘어난다는 뜻 → §7.2 가드레일 필수.
- **텔레그램 플러그인의 알려진 버그**: [공식 이슈](https://github.com/anthropics/claude-plugins-official/issues/1478)에 따르면
  Linux에서 8~12시간 유휴 시 봇 프로세스가 자동 재기동 없이 죽는다(수동 `/reload-plugins` 필요) —
  macOS에서도 재현되는지 Phase 6에서 직접 확인 필요. "상시 구동 세션"이 죽어있는 동안 텔레그램
  트리거는 그냥 무응답으로 유실된다(메시지 큐 없음).
- **텔레그램 메시지 히스토리 없음**: Telegram Bot API 자체의 제약으로 플러그인이 과거 메시지를
  가져오거나 검색할 수 없다 — 매번 새로 도착하는 메시지만 처리 가능. 우리 트리거 용도(명령 하나 →
  분석 실행)엔 문제 없지만, "이전에 물어본 거 이어서" 같은 맥락 참조는 안 된다는 점을 사용자에게
  안내해야 한다.

## 13. 이번 문서 범위 밖

- 다중 사용자/팀 계정 관리, 결제/과금
- 고가용성 인프라(클라우드 배포, 오토스케일링)
- 네이티브 모바일 앱 (반응형 웹으로 우선 커버)

## 14. 승인 체크리스트

- [ ] §7.1 권한 범위 방식: **좁힌 허용목록** vs `--dangerously-skip-permissions`
- [ ] §9 원격 접근 방식: **Tailscale** vs Cloudflare Tunnel vs 기타
- [ ] §10 기술 스택: **FastAPI+Uvicorn 도입** vs 표준 라이브러리 유지
- [ ] §7.2 가드레일 기본값(동시 2건 / 일일 20건)에 동의
- [ ] §11 Phase 순서대로 진행 vs 텔레그램(Phase 6)을 Phase 2 직후로 앞당기기
- [ ] 텔레그램: 공식 `telegram@claude-plugins-official` 플러그인 사용에 동의 (커스텀 봇 아님)
- [ ] 텔레그램 봇 토큰 발급 주체 확인 — 사용자가 직접 BotFather(t.me/BotFather)에서 생성
- [ ] Bun 런타임 설치 동의(텔레그램 플러그인 실행 요건, `pyproject.toml`과 무관한 별도 런타임)

---

*이 문서는 리뷰용 초안이다. 위 승인 체크리스트가 확정되면 Phase 1 구현에 착수한다.*
