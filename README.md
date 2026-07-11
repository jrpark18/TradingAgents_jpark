# TradingAgents — Claude 멀티에이전트 트레이딩 스킬

미국 주식을 대상으로 **기본적 분석 · 기술적 분석 · 뉴스 분석**을 각각의 에이전트가
수행하고, 이를 **오케스트레이션**하여 종목 추천 및 매수·매도 결정을 돕는 Claude Code
기반 멀티에이전트 시스템입니다.

기존 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
LangGraph 프레임워크의 데이터·LLM 엔진(`tradingagents/`)을 그대로 활용하고, 그 위에
Claude Code **스킬(`.claude/skills/`)** 과 **서브에이전트(`.claude/agents/`)** 계층을
새로 얹어 재편했습니다.

---

## 기능 구성 (5대 기능)

| # | 기능 | 스킬 / 에이전트 | 역할 |
|---|------|----------------|------|
| 1 | **트레이딩 에이전트 스킬 개발** | `trading-agent-dev` | 스킬·에이전트·데이터 소스를 일관되게 확장하는 메타 스킬 |
| 2 | **기본적 분석** (종목 지표) | `fundamental-analysis` / `fundamental-analyst` | 재무제표·밸류에이션·수익성·성장성·재무건전성 분석 |
| 3 | **기술적 분석** (차트) | `technical-analysis` / `technical-analyst` | 추세·모멘텀·변동성·거래량·주요 레벨 분석 |
| 4 | **뉴스 분석** (트렌드·맥락) | `news-analysis` / `news-analyst` | 종목/거시 뉴스·센티먼트·트렌드 일일 브리핑 |
| 5 | **매수·매도 결정** (오케스트레이션) | `trade-decision` / `trade-orchestrator` | 세 에이전트를 병렬 실행 → 불/베어 토론 → 최종 결정 |

각 분석 에이전트는 자기 관점의 **BUY/HOLD/SELL lean**만 제시하고, **최종 결정은
`trade-decision`** 이 세 관점을 종합하여 내립니다.

### 추가 기능 (연속 시장 스캔 · 메모리 · 백테스트)

| 기능 | 스킬 / 스크립트 | 역할 |
|------|----------------|------|
| **시장 순환 스캔** | `market-scanner` / `scripts/screen_universe.py` | 대형 지수 유니버스를 순환하며 저평가·상승 후보 발굴 (2단계 퍼널) |
| **메모리/학습** | `trade-journal` / `scripts/journal.py` | picks·decisions 기록 → 리뷰로 실현수익·알파 학습 |
| **백테스트** | `backtest` / `scripts/backtest_screen.py` | 모멘텀 스크린을 과거 시점에 재구성해 성과 검증 |

---

## 아키텍처

```
.claude/skills/                 각 에이전트가 "무엇을 어떻게 분석하는가"
  trading-agent-dev/            (1) 스킬 개발 메타 스킬
  fundamental-analysis/         (2) 기본적 분석
  technical-analysis/           (3) 기술적 분석
  news-analysis/                (4) 뉴스 분석
  trade-decision/               (5) 오케스트레이션 → 매수·매도 결정
.claude/agents/                 병렬 실행 가능한 서브에이전트 (Task 대상)
  fundamental-analyst.md  technical-analyst.md  news-analyst.md  trade-orchestrator.md
scripts/market_data.py          단일 데이터 시밍(seam) — 모든 스킬이 여기로만 데이터 접근
tradingagents/                  데이터·LLM 엔진 (dataflows, llm_clients, config)
```

**핵심 설계 원칙:** 모든 스킬/에이전트는 실데이터를 오직 `scripts/market_data.py`
를 통해서만 가져옵니다. 벤더 라우팅·API 키·폴백은 `tradingagents/dataflows/` 한 곳에
모여 있어, 데이터 소스 교체·추가가 스킬 코드에 영향을 주지 않습니다.

---

## 데이터 CLI (`scripts/market_data.py`)

`tradingagents.dataflows.interface.route_to_vendor` 를 감싼 독립 실행 CLI입니다.
기본 벤더는 **키 없이 동작(yfinance)** 하며, macro만 `FRED_API_KEY` 가 필요합니다.

```bash
# 설치 (엔진 의존성)
pip install -e .          # 또는 최소: pip install yfinance pandas stockstats requests pytz parsel

# 사용 예 (레포 루트에서)
python scripts/market_data.py prices        AAPL --start 2026-06-01 --end 2026-07-01
python scripts/market_data.py indicators    AAPL --indicator rsi,macd,close_50_sma --date 2026-07-01
python scripts/market_data.py fundamentals  AAPL --date 2026-07-01
python scripts/market_data.py income        AAPL --freq quarterly --date 2026-07-01
python scripts/market_data.py news          AAPL --start 2026-06-24 --end 2026-07-01
python scripts/market_data.py global-news   --date 2026-07-01 --lookback 7 --limit 10
python scripts/market_data.py macro         cpi --date 2026-07-01       # FRED_API_KEY 필요
python scripts/market_data.py prediction-markets "Fed rate cut" --limit 5
```

전체 명령: `python scripts/market_data.py --help`

**동작 규약:** 성공 시 리포트를 stdout에 출력하고 exit 0. 데이터가 없으면
`NO_DATA_AVAILABLE`/`DATA_UNAVAILABLE` 문자열을 출력(exit 2), 호출 오류는 stderr +
exit 1. 에이전트는 값을 추정하지 않고 "데이터 없음"으로 보고합니다.

---

## 사용법

Claude Code 세션에서 자연어로 요청하면 해당 스킬이 자동 활성화됩니다.

- "NVDA 기본적 분석 해줘" → `fundamental-analysis`
- "테슬라 차트 어때?" → `technical-analysis`
- "오늘 애플 관련 뉴스/시장 브리핑" → `news-analysis`
- "AAPL 지금 사도 될까? 전체 분석" → `trade-decision` (세 에이전트 병렬 → 최종 결정)
- "새 분석 에이전트 추가하고 싶어" → `trading-agent-dev`

`trade-decision` 은 `fundamental-analyst`·`technical-analyst`·`news-analyst`
서브에이전트를 **병렬로** 띄워 각 리포트를 모은 뒤, 불/베어 토론과 리스크 점검을
거쳐 **매수·매도 결정 메모**(확신도·진입/손절/목표·근거·리스크)를 출력합니다.

> **일일 분석**: 시장은 미국 주식, 실행은 수동입니다. 매일 브리핑이 필요하면
> 관심 종목에 대해 `trade-decision` 또는 `news-analysis` 를 그날 날짜로 실행하세요.

---

## 연속 시장 스캔 (Continuous scanning)

수백~수천 종목(나스닥100 · S&P100 · 다우30 · 러셀1000)을 **무인·헤드리스**로 순환
스캔하여 저평가·상승 후보를 찾는 2단계 퍼널입니다.

- **Stage 1 (저비용, LLM 없음):** `scripts/screen_universe.py` 가 종목별 지표
  스냅샷 1회 호출로 밸류에이션·품질·모멘텀·성장을 점수화 → 저평가/상승/스위트스팟
  후보를 랭킹. **로테이션 커서**로 매 실행마다 유니버스를 배치 단위로 순환합니다.
- **Stage 2 (심층):** 상위 후보에만 `trade-decision` 멀티에이전트 분석을 적용.

```bash
# 유니버스 구성 (네트워크 필요; 4대 지수 전체 채우기)
python scripts/refresh_universe.py            # 없으면 out-of-box는 Dow 30만

# 한 번의 크론 틱: 다음 150종목 스캔, 상위 25개 저널 기록
python scripts/screen_universe.py --batch-size 150 --top 25 --workers 8

# 결과: data/scans/latest_scan.md (저평가 / 상승 / 저평가&상승 표)
```

### 크론 / 무인 실행 등록

시스템 크론(예: 30분마다 한 배치씩 순환):

```cron
*/30 9-16 * * 1-5  cd /path/to/TradingAgents_jpark && \
  python scripts/screen_universe.py --batch-size 150 --top 25 --workers 8 >> data/scans/cron.log 2>&1
```

또는 이 저장소를 **Claude Code on the web** 환경에서 운용한다면, Routine(스케줄 트리거)
으로 세션을 주기적으로 깨워 `market-scanner` → (상위 후보) `trade-decision` 를 돌리게
할 수 있습니다. 원하면 등록해 드립니다.

> 스캔은 정량 스크린이라 LLM 비용이 들지 않습니다. 비싼 멀티에이전트 분석은
> 상위 후보에만 적용해 비용을 통제하세요.

---

## 검증 (Verification)

```bash
# 1) CLI 배선 확인
python scripts/market_data.py --help
python scripts/market_data.py indicators --help

# 2) 실데이터 스모크 (네트워크 필요; 미국장 종목 하나)
python scripts/market_data.py prices AAPL --start 2026-06-20 --end 2026-07-01
python scripts/market_data.py fundamentals AAPL --date 2026-07-01

# 3) 스캐너 스코어링 단위 테스트 (네트워크 불필요, 순수 로직)
pytest -q tests/test_screener.py

# 4) 스캐너/저널/백테스트 배선 확인
python scripts/screen_universe.py --help
python scripts/journal.py summary
python scripts/backtest_screen.py --asof 2026-01-15 --indices dow30 --top 5   # 네트워크 필요

# 5) 전체 엔진 단위 테스트
pytest -q
```

Claude Code 상에서는 `trade-decision` 스킬로 단일 종목(예: AAPL) 드라이런을 돌려
세 에이전트가 실행되고 결정 메모가 나오는지 확인하세요.

> ⚠️ 일부 실행 환경은 아웃바운드 네트워크가 제한되어 Yahoo Finance 호출이 막힐 수
> 있습니다. 이 경우 CLI는 트레이스백 대신 명확한 오류 메시지를 출력합니다. 네트워크가
> 열린 환경에서 실데이터 검증을 수행하세요.

---

## 확장하기

새 분석 관점(에이전트)·지표·데이터 벤더 추가는 `trading-agent-dev` 스킬의 절차를
따르세요. 요약: ① `.claude/skills/<name>/SKILL.md` 작성 → ② `.claude/agents/<name>.md`
서브에이전트 등록 → ③ `trade-decision` 팬아웃에 연결. 데이터 벤더는
`tradingagents/dataflows/interface.py` 의 `VENDOR_METHODS` 에 등록 후
`scripts/market_data.py` 에 서브커맨드를 추가합니다.

---

## 고지

이 시스템은 **투자 판단을 돕는 의사결정 지원 도구**이며 투자 자문(financial advice)이
아닙니다. 모든 수치는 도구 호출 결과에 근거하며, 에이전트는 값을 임의로 생성하지
않습니다.

## Citation

기반 프레임워크(TradingAgents)를 인용해 주세요:

```
@misc{xiao2025tradingagentsmultiagentsllmfinancial,
      title={TradingAgents: Multi-Agents LLM Financial Trading Framework},
      author={Yijia Xiao and Edward Sun and Di Luo and Wei Wang},
      year={2025},
      eprint={2412.20138},
      archivePrefix={arXiv},
      primaryClass={q-fin.TR},
      url={https://arxiv.org/abs/2412.20138},
}
```

라이선스는 [LICENSE](LICENSE) 를 참조하세요.
