---
name: trading-agent-dev
description: >-
  Develop, extend, and maintain the trading-agent skills and sub-agents in this
  repo (트레이딩 에이전트 스킬 개발) — add a new analyst lens, wire a new data source
  into the market-data CLI, or adjust the orchestration. Use when the user wants
  to build/modify a trading skill or agent, add an indicator/data vendor, or asks
  "새 분석 에이전트 추가", "스킬 확장", "데이터 소스 붙이기".
---

# Trading-Agent Skill Development (트레이딩 에이전트 스킬 개발)

This is the **meta-skill**: how the trading system is built, and how to extend it
consistently. Use it whenever you change how the agents analyze or decide.

## Architecture at a glance

```
.claude/skills/                 ← what each agent does (this layer)
  fundamental-analysis/         기본적 분석 (종목 지표)
  technical-analysis/           기술적 분석 (차트)
  news-analysis/                뉴스 분석 (트렌드·맥락)
  trade-decision/               오케스트레이션 → 매수·매도 결정
  trading-agent-dev/            (this) 스킬 개발
.claude/agents/                 ← parallelizable sub-agents (Task tool targets)
  fundamental-analyst.md, technical-analyst.md, news-analyst.md, trade-orchestrator.md
scripts/market_data.py          ← the ONE data seam (wraps route_to_vendor)
tradingagents/                  ← data + LLM engine (dataflows, llm_clients, config)
```

**Design rule:** every skill/agent gets real data *only* through
`scripts/market_data.py`. No skill talks to a vendor SDK directly. This keeps
vendor routing, keys, and fallbacks in one place (`tradingagents/dataflows/`).

## Common tasks

### Add a new analyst lens (e.g. a sentiment or options-flow agent)
1. Create `.claude/skills/<name>/SKILL.md` — copy the shape of an existing
   analysis skill: `name`, a trigger-rich `description`, a **Data source** block
   using the CLI, a **Method**, and an **Output** section ending in a one-line
   lean. Keep the "never fabricate; report `NO_DATA_AVAILABLE`" rule.
2. Create `.claude/agents/<name>.md` (frontmatter `name`, `description`,
   `tools: Read, Bash`, `model: sonnet`) so `trade-decision` can fan out to it.
3. Wire it into `trade-decision/SKILL.md`: add it to the parallel fan-out and the
   per-lens summary table.

### Add a data source / indicator to the CLI
1. The vendor functions live in `tradingagents/dataflows/` and are registered in
   `VENDOR_METHODS` / `TOOLS_CATEGORIES` in `tradingagents/dataflows/interface.py`.
   Add or extend a vendor there so `route_to_vendor("<method>", ...)` resolves.
2. Expose it as a subcommand in `scripts/market_data.py` (add a `cmd_*` handler +
   an `add_parser` block, following the existing pattern).
3. Reference the new subcommand from whichever skill should use it.
4. Verify: `python scripts/market_data.py <subcommand> --help`, then a live call.

### Reference the upstream framework
This repo is built on **TauricResearch/TradingAgents**. Its LangGraph agent
prompts (`tradingagents/agents/analysts/*.py`, `.../trader/trader.py`) and its
researcher/risk-debate logic are the source material for these skills. When
extending a lens, mirror the corresponding upstream agent's prompt and tool set.

## Conventions
- Skills describe *what/how to analyze*; the engine (`tradingagents/`) does data
  and LLM plumbing. Don't blur the two.
- Descriptions must carry trigger phrases in **both English and Korean** so the
  right skill activates.
- Keep outputs evidence-cited and end analyst skills with an explicit lean;
  only `trade-decision` issues the final BUY/HOLD/SELL.
- After any change, run the verification in `README.md` (CLI smoke + a
  single-ticker `trade-decision` dry run).
