"""
DART Finder Web UI
실행: python web.py
접속: http://서버IP:8080
"""
import asyncio
import csv
import json
import os
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

# dart_finder 모듈
from src.config import settings
from src.dart_client import DartClient
from src.scoring import CompanyData, assign_tier, get_domain, guess_emails, passes_filter, recommend_channel, recommend_persona, score_company

app = FastAPI()
KST = timezone(timedelta(hours=9))

# 실행 상태
state = {
    "running": False,
    "progress": 0,
    "total": 0,
    "log": [],
    "results": [],
    "done": False,
    "error": None,
}

HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DART Target Finder</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Pretendard:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0a0a0f;
    --surface: #111118;
    --border: #1e1e2e;
    --accent: #00ff88;
    --accent2: #0088ff;
    --warn: #ff6b35;
    --text: #e2e8f0;
    --muted: #64748b;
    --tier-s: #ffd700;
    --tier-a: #00ff88;
    --tier-b: #0088ff;
    --tier-c: #64748b;
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Pretendard', sans-serif;
    min-height: 100vh;
  }

  .header {
    padding: 24px 32px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 16px;
    background: var(--surface);
  }

  .logo {
    font-family: 'JetBrains Mono', monospace;
    font-size: 18px;
    font-weight: 700;
    color: var(--accent);
    letter-spacing: -0.5px;
  }

  .logo span { color: var(--muted); }

  .status-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--muted);
    transition: all 0.3s;
  }
  .status-dot.running { background: var(--accent); box-shadow: 0 0 8px var(--accent); animation: pulse 1s infinite; }
  .status-dot.done { background: var(--accent2); }

  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

  .main { display: grid; grid-template-columns: 320px 1fr; gap: 0; height: calc(100vh - 73px); }

  .sidebar {
    background: var(--surface);
    border-right: 1px solid var(--border);
    padding: 24px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 24px;
  }

  .section-title {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    font-weight: 600;
    color: var(--muted);
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 12px;
  }

  .form-group { margin-bottom: 16px; }

  label {
    display: block;
    font-size: 12px;
    color: var(--muted);
    margin-bottom: 6px;
    font-weight: 500;
  }

  input, select {
    width: 100%;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 13px;
    font-family: 'JetBrains Mono', monospace;
    transition: border-color 0.2s;
  }

  input:focus, select:focus {
    outline: none;
    border-color: var(--accent);
  }

  .btn {
    width: 100%;
    padding: 12px;
    border: none;
    border-radius: 8px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
    font-family: 'Pretendard', sans-serif;
  }

  .btn-primary {
    background: var(--accent);
    color: #000;
  }

  .btn-primary:hover { opacity: 0.9; transform: translateY(-1px); }
  .btn-primary:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }

  .btn-secondary {
    background: transparent;
    color: var(--muted);
    border: 1px solid var(--border);
    margin-top: 8px;
  }

  .btn-secondary:hover { border-color: var(--muted); color: var(--text); }

  .progress-wrap { margin-top: 16px; }

  .progress-bar {
    height: 4px;
    background: var(--border);
    border-radius: 2px;
    overflow: hidden;
    margin-bottom: 8px;
  }

  .progress-fill {
    height: 100%;
    background: var(--accent);
    border-radius: 2px;
    transition: width 0.5s;
    width: 0%;
  }

  .progress-text {
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    color: var(--muted);
  }

  .log-box {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px;
    height: 160px;
    overflow-y: auto;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    line-height: 1.8;
  }

  .log-line { color: var(--muted); }
  .log-line.accent { color: var(--accent); }
  .log-line.error { color: var(--warn); }

  .content {
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  .toolbar {
    padding: 16px 24px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    background: var(--surface);
  }

  .stats { display: flex; gap: 24px; }

  .stat {
    text-align: center;
  }

  .stat-val {
    font-family: 'JetBrains Mono', monospace;
    font-size: 20px;
    font-weight: 700;
    color: var(--accent);
  }

  .stat-label {
    font-size: 11px;
    color: var(--muted);
    margin-top: 2px;
  }

  .tier-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    font-weight: 700;
  }

  .tier-S { background: rgba(255,215,0,0.15); color: var(--tier-s); }
  .tier-A { background: rgba(0,255,136,0.15); color: var(--tier-a); }
  .tier-B { background: rgba(0,136,255,0.15); color: var(--tier-b); }
  .tier-C { background: rgba(100,116,139,0.15); color: var(--tier-c); }

  .table-wrap {
    overflow: auto;
    flex: 1;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }

  th {
    padding: 10px 16px;
    text-align: left;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
    background: var(--surface);
    position: sticky;
    top: 0;
    white-space: nowrap;
    font-weight: 600;
    letter-spacing: 1px;
    text-transform: uppercase;
  }

  td {
    padding: 10px 16px;
    border-bottom: 1px solid rgba(30,30,46,0.5);
    white-space: nowrap;
    max-width: 200px;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  tr:hover td { background: rgba(255,255,255,0.02); }

  .score-bar {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .score-mini {
    height: 4px;
    border-radius: 2px;
    background: var(--accent);
    min-width: 4px;
  }

  .rank-num {
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    color: var(--muted);
    width: 32px;
  }

  .company-name {
    font-weight: 600;
    color: var(--text);
  }

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--muted);
    gap: 12px;
  }

  .empty-icon {
    font-size: 48px;
    opacity: 0.3;
  }

  .empty-text {
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
  }

  .dl-btn {
    padding: 8px 16px;
    background: transparent;
    border: 1px solid var(--accent);
    color: var(--accent);
    border-radius: 6px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
    text-decoration: none;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }

  .dl-btn:hover { background: rgba(0,255,136,0.1); }
  .dl-btn.hidden { display: none; }

  .filter-tabs {
    display: flex;
    gap: 4px;
  }

  .tab {
    padding: 6px 12px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--muted);
    transition: all 0.2s;
    font-family: 'JetBrains Mono', monospace;
  }

  .tab.active { border-color: var(--accent); color: var(--accent); background: rgba(0,255,136,0.08); }
  .tab:hover:not(.active) { border-color: var(--muted); color: var(--text); }
</style>
</head>
<body>

<div class="header">
  <div class="status-dot" id="statusDot"></div>
  <div class="logo">DART<span>/</span>finder</div>
  <div style="margin-left:auto; font-size:12px; color:var(--muted); font-family:'JetBrains Mono',monospace;" id="statusText">대기 중</div>
</div>

<div class="main">
  <div class="sidebar">
    <div>
      <div class="section-title">필터 설정</div>

      <div class="form-group">
        <label>업종</label>
        <select id="industry">
          <option value="all">전체 업종</option>
          <option value="it_software">IT/소프트웨어</option>
          <option value="service">서비스</option>
          <option value="manufacturing">제조업</option>
          <option value="retail">유통/도소매</option>
          <option value="finance">금융</option>
          <option value="construction">건설</option>
        </select>
      </div>

      <div class="form-group">
        <label>최소 매출 (억원)</label>
        <input type="number" id="revenueMin" value="100" min="0">
      </div>

      <div class="form-group">
        <label>최대 매출 (억원)</label>
        <input type="number" id="revenueMax" value="5000" min="0">
      </div>

      <div class="form-group">
        <label>최소 직원수</label>
        <input type="number" id="empMin" value="30" min="1">
      </div>

      <div class="form-group">
        <label>최대 직원수</label>
        <input type="number" id="empMax" value="800" min="1">
      </div>

      <div class="form-group">
        <label>추출 수 (최대)</label>
        <input type="number" id="top" value="5000" min="1">
      </div>

      <div class="form-group">
        <label>샘플 크기 (0=전체)</label>
        <input type="number" id="sampleSize" value="5000" min="0">
      </div>

      <div class="form-group">
        <label>최소 점수</label>
        <input type="number" id="minScore" value="25" min="0" max="100">
      </div>

      <button class="btn btn-primary" id="runBtn" onclick="startSearch()">▶ 분석 시작</button>
      <button class="btn btn-secondary" id="stopBtn" onclick="stopSearch()" style="display:none">■ 중단</button>
    </div>

    <div>
      <div class="section-title">진행 상황</div>
      <div class="progress-wrap">
        <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
        <div class="progress-text" id="progressText">0 / 0</div>
      </div>
    </div>

    <div style="flex:1">
      <div class="section-title">로그</div>
      <div class="log-box" id="logBox"></div>
    </div>
  </div>

  <div class="content">
    <div class="toolbar">
      <div class="stats">
        <div class="stat">
          <div class="stat-val" id="statTotal">0</div>
          <div class="stat-label">전체</div>
        </div>
        <div class="stat">
          <div class="stat-val" style="color:var(--tier-s)" id="statS">0</div>
          <div class="stat-label">Tier S</div>
        </div>
        <div class="stat">
          <div class="stat-val" style="color:var(--tier-a)" id="statA">0</div>
          <div class="stat-label">Tier A</div>
        </div>
        <div class="stat">
          <div class="stat-val" style="color:var(--tier-b)" id="statB">0</div>
          <div class="stat-label">Tier B</div>
        </div>
      </div>
      <div style="display:flex; gap:8px; align-items:center">
        <div class="filter-tabs">
          <button class="tab active" onclick="filterTier('all')">전체</button>
          <button class="tab" onclick="filterTier('S')">S</button>
          <button class="tab" onclick="filterTier('A')">A</button>
          <button class="tab" onclick="filterTier('B')">B</button>
        </div>
        <a class="dl-btn hidden" id="dlBtn" href="/download" download>⬇ CSV</a>
      </div>
    </div>

    <div class="table-wrap" id="tableWrap">
      <div class="empty-state" id="emptyState">
        <div class="empty-icon">◎</div>
        <div class="empty-text">분석을 시작하면 결과가 여기에 표시됩니다</div>
      </div>
      <table id="resultTable" style="display:none">
        <thead>
          <tr>
            <th>#</th>
            <th>Tier</th>
            <th>회사명</th>
            <th>점수</th>
            <th>뚫림</th>
            <th>매출력</th>
            <th>매출(억)</th>
            <th>마진%</th>
            <th>성장%</th>
            <th>직원</th>
            <th>설립</th>
            <th>추천컨택</th>
            <th>영업채널</th>
            <th>도메인</th>
            <th>이메일후보</th>
            <th>주소</th>
          </tr>
        </thead>
        <tbody id="tableBody"></tbody>
      </table>
    </div>
  </div>
</div>

<script>
let ws = null;
let allResults = [];
let currentTier = 'all';
let csvPath = null;

function startSearch() {
  const params = {
    industry: document.getElementById('industry').value,
    revenue_min: parseInt(document.getElementById('revenueMin').value) * 100000000,
    revenue_max: parseInt(document.getElementById('revenueMax').value) * 100000000,
    employees_min: parseInt(document.getElementById('empMin').value),
    employees_max: parseInt(document.getElementById('empMax').value),
    top: parseInt(document.getElementById('top').value),
    sample_size: parseInt(document.getElementById('sampleSize').value),
    min_score: parseInt(document.getElementById('minScore').value),
  };

  document.getElementById('runBtn').disabled = true;
  document.getElementById('stopBtn').style.display = 'block';
  document.getElementById('statusDot').className = 'status-dot running';
  document.getElementById('statusText').textContent = '분석 중...';
  document.getElementById('logBox').innerHTML = '';
  allResults = [];
  renderTable([]);

  ws = new WebSocket(`ws://${location.host}/ws`);

  ws.onopen = () => ws.send(JSON.stringify(params));

  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);

    if (msg.type === 'log') {
      addLog(msg.text, msg.level || 'normal');
    } else if (msg.type === 'progress') {
      updateProgress(msg.current, msg.total);
    } else if (msg.type === 'result') {
      allResults = msg.data;
      renderTable(filterResults(allResults));
      updateStats(allResults);
    } else if (msg.type === 'done') {
      csvPath = msg.csv;
      document.getElementById('runBtn').disabled = false;
      document.getElementById('stopBtn').style.display = 'none';
      document.getElementById('statusDot').className = 'status-dot done';
      document.getElementById('statusText').textContent = `완료 — ${allResults.length}개`;
      if (csvPath) document.getElementById('dlBtn').classList.remove('hidden');
      addLog(`완료! ${allResults.length}개 추출`, 'accent');
    } else if (msg.type === 'error') {
      addLog(`에러: ${msg.text}`, 'error');
      document.getElementById('runBtn').disabled = false;
      document.getElementById('stopBtn').style.display = 'none';
      document.getElementById('statusDot').className = 'status-dot';
      document.getElementById('statusText').textContent = '에러 발생';
    }
  };

  ws.onclose = () => {
    document.getElementById('runBtn').disabled = false;
    document.getElementById('stopBtn').style.display = 'none';
  };
}

function stopSearch() {
  if (ws) ws.close();
  document.getElementById('statusDot').className = 'status-dot';
  document.getElementById('statusText').textContent = '중단됨';
  document.getElementById('runBtn').disabled = false;
  document.getElementById('stopBtn').style.display = 'none';
}

function addLog(text, level = 'normal') {
  const box = document.getElementById('logBox');
  const line = document.createElement('div');
  line.className = `log-line ${level}`;
  line.textContent = `> ${text}`;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

function updateProgress(current, total) {
  const pct = total > 0 ? (current / total * 100) : 0;
  document.getElementById('progressFill').style.width = pct + '%';
  document.getElementById('progressText').textContent = `${current.toLocaleString()} / ${total.toLocaleString()}`;
}

function updateStats(results) {
  document.getElementById('statTotal').textContent = results.length.toLocaleString();
  document.getElementById('statS').textContent = results.filter(r => r.tier === 'S').length;
  document.getElementById('statA').textContent = results.filter(r => r.tier === 'A').length;
  document.getElementById('statB').textContent = results.filter(r => r.tier === 'B').length;
}

function filterTier(tier) {
  currentTier = tier;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  renderTable(filterResults(allResults));
}

function filterResults(results) {
  if (currentTier === 'all') return results;
  return results.filter(r => r.tier === currentTier);
}

function renderTable(results) {
  const empty = document.getElementById('emptyState');
  const table = document.getElementById('resultTable');
  const tbody = document.getElementById('tableBody');

  if (results.length === 0) {
    empty.style.display = 'flex';
    table.style.display = 'none';
    return;
  }

  empty.style.display = 'none';
  table.style.display = 'table';

  tbody.innerHTML = results.map((r, i) => `
    <tr>
      <td class="rank-num">${r.rank}</td>
      <td><span class="tier-badge tier-${r.tier}">${r.tier}</span></td>
      <td class="company-name" title="${r.corp_name}">${r.corp_name}</td>
      <td>
        <div class="score-bar">
          <div class="score-mini" style="width:${Math.max(4, r.score * 1.8)}px"></div>
          <span style="font-family:'JetBrains Mono',monospace;font-size:12px">${r.score}</span>
        </div>
      </td>
      <td style="color:var(--accent);font-family:'JetBrains Mono',monospace;font-size:12px">${r.pen}</td>
      <td style="color:var(--accent2);font-family:'JetBrains Mono',monospace;font-size:12px">${r.money}</td>
      <td style="font-family:'JetBrains Mono',monospace;font-size:12px">${r.revenue}</td>
      <td style="font-family:'JetBrains Mono',monospace;font-size:12px">${r.margin || '-'}</td>
      <td style="font-family:'JetBrains Mono',monospace;font-size:12px">${r.growth || '-'}</td>
      <td style="font-family:'JetBrains Mono',monospace;font-size:12px">${r.employees}</td>
      <td style="font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--muted)">${r.establish_date ? r.establish_date.substring(0,4) : '-'}</td>
      <td title="${r.persona}" style="max-width:150px;overflow:hidden;text-overflow:ellipsis">${r.persona}</td>
      <td><span style="font-size:11px;color:var(--muted)">${r.channel}</span></td>
      <td style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--accent2)">${r.domain || '-'}</td>
      <td style="font-size:11px;color:var(--muted)" title="${r.emails}">${r.emails.split('|')[0] || '-'}</td>
      <td style="font-size:11px;color:var(--muted)" title="${r.address}">${r.address.substring(0, 20)}...</td>
    </tr>
  `).join('');
}
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML


@app.get("/download")
async def download():
    files = list(Path("output").glob("*.csv")) if Path("output").exists() else []
    if not files:
        return HTMLResponse("결과 파일 없음", status_code=404)
    latest = max(files, key=lambda f: f.stat().st_mtime)
    return FileResponse(latest, filename=latest.name, media_type="text/csv")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    async def send(msg: dict):
        try:
            await websocket.send_text(json.dumps(msg, ensure_ascii=False))
        except Exception:
            pass

    try:
        raw = await websocket.receive_text()
        params = json.loads(raw)
    except Exception:
        await send({"type": "error", "text": "파라미터 파싱 실패"})
        return

    await send({"type": "log", "text": "DART API 연결 중...", "level": "accent"})

    try:
        settings.validate()
    except RuntimeError as e:
        await send({"type": "error", "text": str(e)})
        return

    # 회사 코드 수집
    try:
        with DartClient() as dart:
            all_corps = dart.fetch_all_corp_codes()
    except Exception as e:
        await send({"type": "error", "text": f"DART API 에러: {e}"})
        return

    await send({"type": "log", "text": f"전체 {len(all_corps):,}개 회사 로드 완료"})

    industry = params.get("industry", "all")
    prefixes = None if industry == "all" else settings.INDUSTRY_KSIC_PREFIX.get(industry)
    sample_size = params.get("sample_size", 5000)

    sample = sorted(all_corps.items(), key=lambda x: x[1].get("modify_date", ""), reverse=True)
    if sample_size > 0:
        sample = sample[:sample_size]

    await send({"type": "log", "text": f"분석 대상: {len(sample):,}개"})
    await send({"type": "progress", "current": 0, "total": len(sample)})

    # 비동기 수집
    sem = asyncio.Semaphore(params.get("concurrency", 20))
    results_raw = []
    done_count = 0

    async def fetch_one(client, corp_code, base, year):
        nonlocal done_count
        async with sem:
            try:
                import httpx as _httpx
                info, emp_data, exc_data = await asyncio.gather(
                    _async_get(client, "/company.json", {"corp_code": corp_code}),
                    _async_get(client, "/empSttus.json", {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"}),
                    _async_get(client, "/exctvSttus.json", {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"}),
                    return_exceptions=True,
                )
                fin = await _async_get_fin(client, corp_code, year)
            except Exception:
                done_count += 1
                return None

        if isinstance(info, Exception) or not info or info.get("status") != "000":
            done_count += 1
            return None

        fin = fin if isinstance(fin, dict) else {}
        emp_list = emp_data.get("list", []) if isinstance(emp_data, dict) and emp_data.get("status") == "000" else []
        exc_list = exc_data.get("list", []) if isinstance(exc_data, dict) and exc_data.get("status") == "000" else []

        total_emp = sum(
            int((e.get("sumtrmcnt") or "0").replace(",", "") or 0)
            for e in emp_list
            if (e.get("sumtrmcnt") or "0").replace(",", "").isdigit()
        )

        done_count += 1
        return CompanyData(
            corp_name=base["corp_name"], corp_code=corp_code,
            stock_code=base["stock_code"], ceo_name=info.get("ceo_nm", ""),
            industry=(info.get("induty_code") or "").strip(),
            address=info.get("adres", ""), homepage=info.get("hm_url", ""),
            revenue=fin.get("revenue"), revenue_prev=fin.get("revenue_prev"),
            growth_rate=fin.get("growth_rate"),
            operating_profit=fin.get("operating_profit"),
            net_income=fin.get("net_income"),
            employees=total_emp, executives=exc_list,
            establish_date=info.get("est_dt", ""),
        )

    async def _async_get(client, path, params_dict):
        try:
            r = await client.get(path, params={"crtfc_key": settings.OPENDART_API_KEY, **params_dict}, timeout=30.0)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    async def _async_get_fin(client, corp_code, year):
        for fs in ["CFS", "OFS"]:
            d = await _async_get(client, "/fnlttSinglAcntAll.json", {
                "corp_code": corp_code, "bsns_year": str(year),
                "reprt_code": "11011", "fs_div": fs,
            })
            if d and d.get("status") == "000":
                return DartClient.parse_financial(d.get("list", []))
        return {}

    year = datetime.now(KST).year - 1
    import httpx

    async with httpx.AsyncClient(base_url=settings.DART_BASE_URL, timeout=30.0) as client:
        progress_task = asyncio.create_task(_progress_sender(websocket, lambda: done_count, len(sample)))

        for i in range(0, len(sample), 100):
            batch = sample[i:i+100]
            batch_results = await asyncio.gather(
                *[fetch_one(client, cc, b, year) for cc, b in batch],
                return_exceptions=True,
            )
            for r in batch_results:
                if isinstance(r, CompanyData):
                    results_raw.append(r)

        progress_task.cancel()

    await send({"type": "log", "text": f"수집 완료: {len(results_raw):,}개"})
    await send({"type": "progress", "current": len(sample), "total": len(sample)})

    # 필터 + 점수
    revenue_min = params.get("revenue_min", 10_000_000_000)
    revenue_max = params.get("revenue_max", 500_000_000_000)
    emp_min = params.get("employees_min", 30)
    emp_max = params.get("employees_max", 800)
    min_score = params.get("min_score", 25)
    top = params.get("top", 5000)

    candidates = [
        c for c in results_raw
        if (prefixes is None or any(c.industry.startswith(p) for p in prefixes))
        and passes_filter(c, revenue_min=revenue_min, revenue_max=revenue_max,
                          employees_min=emp_min, employees_max=emp_max)
    ]

    await send({"type": "log", "text": f"필터 통과: {len(candidates)}개"})

    scored = []
    for c in candidates:
        total, bd = score_company(c)
        if total >= min_score:
            scored.append((total, bd, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    scored = scored[:top]

    # CSV 저장
    Path("output").mkdir(exist_ok=True)
    ts = datetime.now(KST).strftime("%Y-%m-%d_%H%M")
    csv_path = f"output/targets_{industry}_{ts}.csv"

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["순위","Tier","영업채널","회사명","종목코드","점수","점수_잘뚫림","점수_매출",
                    "매출(억)","매출성장률(%)","영업이익(억)","영업이익률(%)","1인당매출(억)",
                    "직원수","임원수","설립일","추천컨택","대표자","도메인","이메일후보","주소","홈페이지","업종코드"])
        for rank, (total, bd, c) in enumerate(scored, 1):
            tier = assign_tier(rank)
            pen = sum(bd.get(k,0) for k in ["pen_age","pen_industry","pen_decision","pen_recruit","pen_penalty"])
            money = sum(bd.get(k,0) for k in ["money_margin","money_growth","money_per_capita","money_scale"])
            margin = f"{c.operating_profit/c.revenue*100:.1f}" if c.revenue and c.operating_profit and c.revenue > 0 else ""
            growth = f"{c.growth_rate*100:.1f}" if c.growth_rate else ""
            per_cap = f"{c.revenue/c.employees/1e8:.1f}" if c.revenue and c.employees else ""
            w.writerow([rank, tier, recommend_channel(tier), c.corp_name, c.stock_code, total,
                        pen, money, f"{(c.revenue or 0)/1e8:.0f}", growth,
                        f"{(c.operating_profit or 0)/1e8:.0f}", margin, per_cap,
                        c.employees, len(c.executives), c.establish_date,
                        recommend_persona(c), c.ceo_name,
                        get_domain(c.homepage) or "", " | ".join(guess_emails(c)),
                        c.address, c.homepage, c.industry])

    # 결과 전송
    result_data = []
    for rank, (total, bd, c) in enumerate(scored, 1):
        tier = assign_tier(rank)
        pen = sum(bd.get(k,0) for k in ["pen_age","pen_industry","pen_decision","pen_recruit","pen_penalty"])
        money = sum(bd.get(k,0) for k in ["money_margin","money_growth","money_per_capita","money_scale"])
        result_data.append({
            "rank": rank,
            "tier": tier,
            "channel": recommend_channel(tier),
            "corp_name": c.corp_name,
            "score": total,
            "pen": pen,
            "money": money,
            "revenue": f"{(c.revenue or 0)/1e8:.0f}",
            "margin": f"{c.operating_profit/c.revenue*100:.1f}" if c.revenue and c.operating_profit and c.revenue > 0 else "",
            "growth": f"{c.growth_rate*100:.1f}" if c.growth_rate else "",
            "employees": c.employees or 0,
            "establish_date": c.establish_date or "",
            "persona": recommend_persona(c),
            "domain": get_domain(c.homepage) or "",
            "emails": " | ".join(guess_emails(c)),
            "address": c.address or "",
        })

    await send({"type": "result", "data": result_data})
    await send({"type": "done", "csv": csv_path})


async def _progress_sender(ws, get_count, total):
    while True:
        try:
            await ws.send_text(json.dumps({"type": "progress", "current": get_count(), "total": total}))
            await asyncio.sleep(2)
        except Exception:
            break


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    print(f"DART Finder Web UI: http://0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
