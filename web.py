"""
DART Finder Web UI v3
- 분석 히스토리 자동 저장 (날짜별)
- 모바일 반응형 UI
- 히스토리 페이지 (이전 분석 결과 다시 보기)
"""
import asyncio
import csv
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from src.config import settings
from src.dart_client import DartClient
from src.scoring import (CompanyData, assign_tier, get_domain, guess_emails,
                         passes_filter, recommend_channel, recommend_persona,
                         score_company)

app = FastAPI()
KST = timezone(timedelta(hours=9))

OUTPUT_DIR = Path("output")
HISTORY_DIR = Path("output/history")
STATE_FILE = OUTPUT_DIR / "state.json"
RESULTS_FILE = OUTPUT_DIR / "results.json"

job_state = {
    "running": False,
    "phase": "idle",
    "current": 0,
    "total": 0,
    "log": [],
    "results": [],
    "csv_path": None,
    "error": None,
    "started_at": None,
    "params": None,
    "history_id": None,
}

state_lock = threading.Lock()


def save_state():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = {k: v for k, v in job_state.items() if k != "results"}
    try:
        with STATE_FILE.open("w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False)
    except Exception:
        pass


def save_results():
    try:
        with RESULTS_FILE.open("w", encoding="utf-8") as f:
            json.dump(job_state["results"], f, ensure_ascii=False)
    except Exception:
        pass


def save_to_history(history_id, params, results, csv_path):
    """완료된 분석을 히스토리에 영구 저장."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(KST).strftime("%Y-%m-%d_%H%M")
    entry = {
        "id": history_id,
        "timestamp": ts,
        "completed_at": time.time(),
        "params": params,
        "results_count": len(results),
        "tier_s": sum(1 for r in results if r.get("tier") == "S"),
        "tier_a": sum(1 for r in results if r.get("tier") == "A"),
        "tier_b": sum(1 for r in results if r.get("tier") == "B"),
        "csv_path": csv_path,
    }
    # 인덱스에 추가
    index_file = HISTORY_DIR / "index.json"
    history_index = []
    if index_file.exists():
        try:
            history_index = json.loads(index_file.read_text(encoding="utf-8"))
        except Exception:
            history_index = []
    history_index.insert(0, entry)
    # 최대 50개만 유지
    history_index = history_index[:50]
    with index_file.open("w", encoding="utf-8") as f:
        json.dump(history_index, f, ensure_ascii=False)
    # 상세 결과 저장
    detail_file = HISTORY_DIR / f"{history_id}.json"
    with detail_file.open("w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False)


def get_history_list():
    """히스토리 목록 조회."""
    index_file = HISTORY_DIR / "index.json"
    if not index_file.exists():
        return []
    try:
        return json.loads(index_file.read_text(encoding="utf-8"))
    except Exception:
        return []


def get_history_detail(history_id):
    """특정 히스토리 결과 조회."""
    detail_file = HISTORY_DIR / f"{history_id}.json"
    if not detail_file.exists():
        return None
    try:
        return json.loads(detail_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_state():
    if STATE_FILE.exists():
        try:
            with STATE_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                job_state[k] = v
            if job_state.get("running"):
                job_state["running"] = False
                job_state["phase"] = "interrupted"
        except Exception:
            pass
    if RESULTS_FILE.exists():
        try:
            with RESULTS_FILE.open("r", encoding="utf-8") as f:
                job_state["results"] = json.load(f)
        except Exception:
            pass


def add_log(text, level="normal"):
    with state_lock:
        job_state["log"].append({"text": text, "level": level, "ts": time.time()})
        if len(job_state["log"]) > 200:
            job_state["log"] = job_state["log"][-200:]
        save_state()


def set_progress(current, total, phase=None):
    with state_lock:
        job_state["current"] = current
        job_state["total"] = total
        if phase:
            job_state["phase"] = phase
        save_state()


# ──────────────────────────────────────────────────────────────
#  HTML
# ──────────────────────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
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
  * { margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html, body { overflow-x: hidden; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Pretendard', sans-serif;
    min-height: 100vh;
  }
  .header {
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 12px;
    background: var(--surface);
    position: sticky;
    top: 0;
    z-index: 100;
  }
  .logo {
    font-family: 'JetBrains Mono', monospace;
    font-size: 16px;
    font-weight: 700;
    color: var(--accent);
  }
  .logo span { color: var(--muted); }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); transition: all 0.3s; flex-shrink: 0; }
  .status-dot.running { background: var(--accent); box-shadow: 0 0 8px var(--accent); animation: pulse 1s infinite; }
  .status-dot.done { background: var(--accent2); }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
  .status-text {
    margin-left: auto;
    font-size: 11px;
    color: var(--muted);
    font-family: 'JetBrains Mono', monospace;
  }

  /* 탭 네비게이션 */
  .nav-tabs {
    display: flex;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    position: sticky;
    top: 57px;
    z-index: 99;
  }
  .nav-tab {
    flex: 1;
    padding: 12px;
    text-align: center;
    background: transparent;
    border: none;
    color: var(--muted);
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    border-bottom: 2px solid transparent;
    font-family: 'Pretendard', sans-serif;
  }
  .nav-tab.active { color: var(--accent); border-bottom-color: var(--accent); }

  /* 페이지 컨테이너 */
  .page { display: none; padding: 16px; }
  .page.active { display: block; }

  /* 사이드바 카드 */
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 12px;
  }

  .section-title {
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px;
    font-weight: 600;
    color: var(--muted);
    letter-spacing: 1.5px;
    text-transform: uppercase;
    margin-bottom: 12px;
  }

  /* 필터 그리드 */
  .filter-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
  }
  .filter-full { grid-column: 1 / -1; }

  .form-group { margin-bottom: 0; }

  label {
    display: block;
    font-size: 11px;
    color: var(--muted);
    margin-bottom: 4px;
    font-weight: 500;
  }

  input, select {
    width: 100%;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 10px 12px;
    border-radius: 8px;
    font-size: 14px;
    font-family: 'JetBrains Mono', monospace;
    appearance: none;
  }

  input:focus, select:focus {
    outline: none;
    border-color: var(--accent);
  }

  .btn {
    width: 100%;
    padding: 14px;
    border: none;
    border-radius: 10px;
    font-size: 15px;
    font-weight: 700;
    cursor: pointer;
    font-family: 'Pretendard', sans-serif;
    margin-top: 12px;
  }

  .btn-primary {
    background: var(--accent);
    color: #000;
  }
  .btn-primary:disabled { opacity: 0.4; }

  .btn-secondary {
    background: transparent;
    color: var(--muted);
    border: 1px solid var(--border);
    margin-top: 8px;
  }

  /* 진행 상황 */
  .progress-bar {
    height: 6px;
    background: var(--border);
    border-radius: 3px;
    overflow: hidden;
    margin-bottom: 8px;
  }
  .progress-fill {
    height: 100%;
    background: var(--accent);
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
    padding: 10px;
    max-height: 120px;
    overflow-y: auto;
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px;
    line-height: 1.6;
  }
  .log-line { color: var(--muted); word-break: break-all; }
  .log-line.accent { color: var(--accent); }
  .log-line.error { color: var(--warn); }

  /* 결과 통계 */
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 8px;
    margin-bottom: 12px;
  }
  .stat-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px;
    text-align: center;
  }
  .stat-val {
    font-family: 'JetBrains Mono', monospace;
    font-size: 18px;
    font-weight: 700;
    color: var(--accent);
  }
  .stat-label {
    font-size: 10px;
    color: var(--muted);
    margin-top: 2px;
  }

  /* Tier 배지 */
  .tier-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px;
    font-weight: 700;
  }
  .tier-S { background: rgba(255,215,0,0.15); color: var(--tier-s); }
  .tier-A { background: rgba(0,255,136,0.15); color: var(--tier-a); }
  .tier-B { background: rgba(0,136,255,0.15); color: var(--tier-b); }
  .tier-C { background: rgba(100,116,139,0.15); color: var(--tier-c); }

  /* 필터 탭 */
  .filter-tabs {
    display: flex;
    gap: 4px;
    margin-bottom: 12px;
    overflow-x: auto;
  }
  .tab-btn {
    padding: 6px 14px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--muted);
    font-family: 'JetBrains Mono', monospace;
    white-space: nowrap;
  }
  .tab-btn.active {
    border-color: var(--accent);
    color: var(--accent);
    background: rgba(0,255,136,0.08);
  }

  /* 결과 카드 (모바일) */
  .result-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px;
    margin-bottom: 8px;
  }
  .result-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
  }
  .result-rank {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    color: var(--muted);
  }
  .result-name {
    font-size: 15px;
    font-weight: 700;
    color: var(--text);
    margin-bottom: 4px;
  }
  .result-row {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    margin: 4px 0;
  }
  .result-key {
    color: var(--muted);
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
  }
  .result-val {
    color: var(--text);
    font-family: 'JetBrains Mono', monospace;
  }
  .result-score {
    font-family: 'JetBrains Mono', monospace;
    font-size: 14px;
    font-weight: 700;
    color: var(--accent);
  }

  /* 다운로드 버튼 */
  .dl-bar {
    position: sticky;
    bottom: 0;
    background: var(--bg);
    padding: 12px 0;
    margin: 0 -16px -16px;
    padding: 12px 16px;
    border-top: 1px solid var(--border);
  }
  .dl-btn {
    display: block;
    width: 100%;
    padding: 12px;
    background: var(--accent);
    color: #000;
    border-radius: 8px;
    text-align: center;
    font-weight: 700;
    text-decoration: none;
  }
  .dl-btn.hidden { display: none; }

  /* 빈 상태 */
  .empty-state {
    text-align: center;
    padding: 60px 20px;
    color: var(--muted);
  }
  .empty-icon { font-size: 48px; opacity: 0.3; margin-bottom: 12px; }
  .empty-text { font-family: 'JetBrains Mono', monospace; font-size: 12px; }

  /* 히스토리 카드 */
  .history-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px;
    margin-bottom: 10px;
    cursor: pointer;
    transition: border-color 0.2s;
  }
  .history-card:active { border-color: var(--accent); }
  .history-time {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    color: var(--accent);
    margin-bottom: 6px;
  }
  .history-meta {
    display: flex;
    gap: 12px;
    font-size: 12px;
    color: var(--muted);
    margin-top: 8px;
    flex-wrap: wrap;
  }
  .history-meta span {
    font-family: 'JetBrains Mono', monospace;
  }

  /* 데스크탑 */
  @media (min-width: 768px) {
    .filter-grid { grid-template-columns: repeat(3, 1fr); }
    .stats-grid { grid-template-columns: repeat(4, 1fr); }
    .page { max-width: 1200px; margin: 0 auto; padding: 24px; }
  }
</style>
</head>
<body>

<div class="header">
  <div class="status-dot" id="statusDot"></div>
  <div class="logo">DART<span>/</span>finder</div>
  <div class="status-text" id="statusText">대기 중</div>
</div>

<div class="nav-tabs">
  <button class="nav-tab active" onclick="showPage('analyze', event)">분석</button>
  <button class="nav-tab" onclick="showPage('results', event)">결과</button>
  <button class="nav-tab" onclick="showPage('history', event)">히스토리</button>
</div>

<!-- 분석 페이지 -->
<div class="page active" id="page-analyze">
  <div class="card">
    <div class="section-title">필터 설정</div>
    <div class="filter-grid">
      <div class="form-group filter-full">
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
        <label>최소 매출 (억)</label>
        <input type="number" id="revenueMin" value="100" min="0">
      </div>
      <div class="form-group">
        <label>최대 매출 (억)</label>
        <input type="number" id="revenueMax" value="5000" min="0">
      </div>
      <div class="form-group">
        <label>최소 직원</label>
        <input type="number" id="empMin" value="30" min="1">
      </div>
      <div class="form-group">
        <label>최대 직원</label>
        <input type="number" id="empMax" value="999999" min="1">
      </div>
      <div class="form-group">
        <label>추출 수</label>
        <input type="number" id="top" value="5000" min="1">
      </div>
      <div class="form-group">
        <label>샘플 크기</label>
        <input type="number" id="sampleSize" value="50000" min="0">
      </div>
      <div class="form-group filter-full">
        <label>최소 점수 (0~100)</label>
        <input type="number" id="minScore" value="20" min="0" max="100">
      </div>
    </div>
    <button class="btn btn-primary" id="runBtn" onclick="startSearch()">▶ 분석 시작</button>
    <button class="btn btn-secondary" id="stopBtn" onclick="stopSearch()" style="display:none">■ 중단</button>
  </div>

  <div class="card">
    <div class="section-title">진행 상황</div>
    <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
    <div class="progress-text" id="progressText">0 / 0</div>
  </div>

  <div class="card">
    <div class="section-title">로그</div>
    <div class="log-box" id="logBox"></div>
  </div>
</div>

<!-- 결과 페이지 -->
<div class="page" id="page-results">
  <div class="stats-grid">
    <div class="stat-card"><div class="stat-val" id="statTotal">0</div><div class="stat-label">전체</div></div>
    <div class="stat-card"><div class="stat-val" style="color:var(--tier-s)" id="statS">0</div><div class="stat-label">Tier S</div></div>
    <div class="stat-card"><div class="stat-val" style="color:var(--tier-a)" id="statA">0</div><div class="stat-label">Tier A</div></div>
    <div class="stat-card"><div class="stat-val" style="color:var(--tier-b)" id="statB">0</div><div class="stat-label">Tier B</div></div>
  </div>

  <div class="filter-tabs">
    <button class="tab-btn active" onclick="filterTier('all', event)">전체</button>
    <button class="tab-btn" onclick="filterTier('S', event)">S</button>
    <button class="tab-btn" onclick="filterTier('A', event)">A</button>
    <button class="tab-btn" onclick="filterTier('B', event)">B</button>
  </div>

  <div id="resultsList"></div>

  <div class="dl-bar">
    <a class="dl-btn hidden" id="dlBtn" href="/download">⬇ CSV 다운로드</a>
  </div>
</div>

<!-- 히스토리 페이지 -->
<div class="page" id="page-history">
  <div id="historyList">
    <div class="empty-state">
      <div class="empty-icon">⌛</div>
      <div class="empty-text">아직 분석 기록이 없습니다</div>
    </div>
  </div>
</div>

<script>
let allResults = [];
let currentTier = 'all';
let pollTimer = null;
let lastResultsCount = -1;
let viewingHistoryId = null;

function showPage(name, ev) {
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  ev.target.classList.add('active');
  document.getElementById('page-' + name).classList.add('active');

  if (name === 'history') loadHistory();
  if (name === 'results' && viewingHistoryId) {
    viewingHistoryId = null;
    pollStatus();
  }
}

async function pollStatus() {
  try {
    const r = await fetch('/status');
    const s = await r.json();

    if (s.running) {
      document.getElementById('statusDot').className = 'status-dot running';
      document.getElementById('statusText').textContent = `${s.phase}`;
      document.getElementById('runBtn').disabled = true;
      document.getElementById('stopBtn').style.display = 'block';
    } else if (s.phase === 'done') {
      document.getElementById('statusDot').className = 'status-dot done';
      document.getElementById('statusText').textContent = `완료 - ${s.results_count}개`;
      document.getElementById('runBtn').disabled = false;
      document.getElementById('stopBtn').style.display = 'none';
      if (s.csv_path) document.getElementById('dlBtn').classList.remove('hidden');
    } else if (s.phase === 'error' || s.phase === 'stopped' || s.phase === 'interrupted') {
      document.getElementById('statusDot').className = 'status-dot';
      document.getElementById('statusText').textContent = s.phase === 'error' ? '에러' : (s.phase === 'stopped' ? '중단됨' : '중단됨');
      document.getElementById('runBtn').disabled = false;
      document.getElementById('stopBtn').style.display = 'none';
    } else {
      document.getElementById('statusDot').className = 'status-dot';
      document.getElementById('statusText').textContent = '대기';
      document.getElementById('runBtn').disabled = false;
      document.getElementById('stopBtn').style.display = 'none';
    }

    updateProgress(s.current, s.total);
    renderLog(s.log);

    if (s.results_count !== lastResultsCount && !viewingHistoryId) {
      lastResultsCount = s.results_count;
      if (s.results_count > 0) {
        const rr = await fetch('/results');
        allResults = await rr.json();
        renderResults(filterResults(allResults));
        updateStats(allResults);
      }
    }
  } catch (e) {
    console.error('poll error', e);
  }
}

function startPolling() {
  pollStatus();
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    if (!viewingHistoryId) pollStatus();
  }, 2000);
}

async function startSearch() {
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
  allResults = [];
  lastResultsCount = -1;
  renderResults([]);

  try {
    const r = await fetch('/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(params)
    });
    const result = await r.json();
    if (result.error) {
      alert(result.error);
      document.getElementById('runBtn').disabled = false;
    }
  } catch (e) {
    alert('시작 실패: ' + e.message);
    document.getElementById('runBtn').disabled = false;
  }
}

async function stopSearch() {
  if (!confirm('분석을 중단하시겠습니까?')) return;
  await fetch('/stop', {method: 'POST'});
}

function renderLog(logs) {
  const box = document.getElementById('logBox');
  box.innerHTML = '';
  (logs || []).forEach(l => {
    const line = document.createElement('div');
    line.className = `log-line ${l.level || 'normal'}`;
    line.textContent = `> ${l.text}`;
    box.appendChild(line);
  });
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

function filterTier(tier, ev) {
  currentTier = tier;
  document.querySelectorAll('.tab-btn').forEach(t => t.classList.remove('active'));
  ev.target.classList.add('active');
  renderResults(filterResults(allResults));
}

function filterResults(results) {
  if (currentTier === 'all') return results;
  return results.filter(r => r.tier === currentTier);
}

function renderResults(results) {
  const list = document.getElementById('resultsList');
  if (results.length === 0) {
    list.innerHTML = `<div class="empty-state"><div class="empty-icon">◎</div><div class="empty-text">아직 결과가 없습니다</div></div>`;
    return;
  }

  list.innerHTML = results.slice(0, 200).map(r => `
    <div class="result-card">
      <div class="result-header">
        <div>
          <div class="result-rank">#${r.rank} <span class="tier-badge tier-${r.tier}">${r.tier}</span></div>
        </div>
        <div class="result-score">${r.score}점</div>
      </div>
      <div class="result-name">${r.corp_name}</div>
      <div class="result-row"><span class="result-key">매출</span><span class="result-val">${r.revenue}억 ${r.growth ? `(↑${r.growth}%)` : ''}</span></div>
      <div class="result-row"><span class="result-key">마진</span><span class="result-val">${r.margin || '-'}%</span></div>
      <div class="result-row"><span class="result-key">직원</span><span class="result-val">${r.employees}명</span></div>
      <div class="result-row"><span class="result-key">컨택</span><span class="result-val" style="text-align:right;max-width:60%;overflow:hidden;text-overflow:ellipsis">${r.persona || '-'}</span></div>
      <div class="result-row"><span class="result-key">채널</span><span class="result-val" style="color:var(--accent2);font-size:11px">${r.channel}</span></div>
      ${r.domain ? `<div class="result-row"><span class="result-key">도메인</span><span class="result-val" style="color:var(--accent2);font-size:11px">${r.domain}</span></div>` : ''}
      ${r.emails ? `<div class="result-row"><span class="result-key">이메일</span><span class="result-val" style="font-size:10px;color:var(--muted);text-align:right;max-width:60%;overflow:hidden;text-overflow:ellipsis">${r.emails.split('|')[0]}</span></div>` : ''}
    </div>
  `).join('');

  if (results.length > 200) {
    list.innerHTML += `<div style="text-align:center;padding:16px;color:var(--muted);font-size:12px">상위 200개만 표시 · 전체는 CSV 다운로드</div>`;
  }
}

async function loadHistory() {
  try {
    const r = await fetch('/history');
    const list = await r.json();

    const div = document.getElementById('historyList');
    if (list.length === 0) {
      div.innerHTML = `<div class="empty-state"><div class="empty-icon">⌛</div><div class="empty-text">아직 분석 기록이 없습니다</div></div>`;
      return;
    }

    div.innerHTML = list.map(h => `
      <div class="history-card" onclick="loadHistoryDetail('${h.id}')">
        <div class="history-time">${h.timestamp}</div>
        <div style="font-size:14px;font-weight:700;margin-bottom:4px">${h.results_count}개 추출</div>
        <div class="history-meta">
          <span style="color:var(--tier-s)">S ${h.tier_s}</span>
          <span style="color:var(--tier-a)">A ${h.tier_a}</span>
          <span style="color:var(--tier-b)">B ${h.tier_b}</span>
        </div>
        <div class="history-meta">
          <span>매출 ${(h.params.revenue_min/1e8).toFixed(0)}~${(h.params.revenue_max/1e8).toFixed(0)}억</span>
          <span>샘플 ${h.params.sample_size?.toLocaleString() || '?'}</span>
        </div>
      </div>
    `).join('');
  } catch (e) {
    console.error(e);
  }
}

async function loadHistoryDetail(id) {
  try {
    const r = await fetch('/history/' + id);
    const data = await r.json();
    allResults = data.results;
    viewingHistoryId = id;
    updateStats(allResults);
    renderResults(filterResults(allResults));

    // 결과 탭으로 이동
    document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-tab')[1].classList.add('active');
    document.getElementById('page-results').classList.add('active');

    // CSV 링크 변경
    document.getElementById('dlBtn').href = '/history/' + id + '/csv';
    document.getElementById('dlBtn').classList.remove('hidden');
  } catch (e) {
    alert('로드 실패: ' + e.message);
  }
}

window.addEventListener('load', startPolling);
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML


@app.get("/status")
async def get_status():
    return JSONResponse({
        "running": job_state["running"],
        "phase": job_state["phase"],
        "current": job_state["current"],
        "total": job_state["total"],
        "log": job_state["log"][-50:],
        "results_count": len(job_state["results"]),
        "csv_path": job_state["csv_path"],
        "error": job_state["error"],
    })


@app.get("/results")
async def get_results():
    return JSONResponse(job_state["results"])


@app.post("/start")
async def start_job(params: dict):
    if job_state["running"]:
        return JSONResponse({"error": "이미 실행 중입니다"}, status_code=400)
    try:
        settings.validate()
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    with state_lock:
        job_state["running"] = True
        job_state["phase"] = "starting"
        job_state["current"] = 0
        job_state["total"] = 0
        job_state["log"] = []
        job_state["results"] = []
        job_state["csv_path"] = None
        job_state["error"] = None
        job_state["started_at"] = time.time()
        job_state["params"] = params
        job_state["history_id"] = uuid.uuid4().hex[:12]
        save_state()
        save_results()

    thread = threading.Thread(target=run_analysis, args=(params,), daemon=True)
    thread.start()
    return JSONResponse({"ok": True})


@app.post("/stop")
async def stop_job():
    with state_lock:
        job_state["running"] = False
        job_state["phase"] = "stopped"
        save_state()
    return JSONResponse({"ok": True})


@app.get("/download")
async def download():
    files = list(OUTPUT_DIR.glob("targets_*.csv")) if OUTPUT_DIR.exists() else []
    if not files:
        return HTMLResponse("결과 파일 없음", status_code=404)
    latest = max(files, key=lambda f: f.stat().st_mtime)
    return FileResponse(latest, filename=latest.name, media_type="text/csv")


@app.get("/history")
async def history_list():
    return JSONResponse(get_history_list())


@app.get("/history/{history_id}")
async def history_detail(history_id: str):
    results = get_history_detail(history_id)
    if not results:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse({"id": history_id, "results": results})


@app.get("/history/{history_id}/csv")
async def history_csv(history_id: str):
    index = get_history_list()
    entry = next((e for e in index if e["id"] == history_id), None)
    if not entry or not entry.get("csv_path"):
        return HTMLResponse("CSV 없음", status_code=404)
    csv_path = Path(entry["csv_path"])
    if not csv_path.exists():
        return HTMLResponse("파일 없음", status_code=404)
    return FileResponse(csv_path, filename=csv_path.name, media_type="text/csv")


# ──────────────────────────────────────────────────────────────
#  분석 로직
# ──────────────────────────────────────────────────────────────
def run_analysis(params):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_async_analyze(params))
    except Exception as e:
        add_log(f"치명적 에러: {e}", "error")
        with state_lock:
            job_state["running"] = False
            job_state["phase"] = "error"
            job_state["error"] = str(e)
            save_state()


async def _async_analyze(params):
    add_log("DART API 연결 중...", "accent")
    try:
        with DartClient() as dart:
            all_corps = dart.fetch_all_corp_codes()
    except Exception as e:
        add_log(f"DART API 에러: {e}", "error")
        with state_lock:
            job_state["running"] = False
            job_state["phase"] = "error"
            job_state["error"] = str(e)
            save_state()
        return

    add_log(f"전체 {len(all_corps):,}개 회사 로드 완료")

    industry = params.get("industry", "all")
    prefixes = None if industry == "all" else settings.INDUSTRY_KSIC_PREFIX.get(industry)
    sample_size = params.get("sample_size", 50000)

    sample = sorted(all_corps.items(), key=lambda x: x[1].get("modify_date", ""), reverse=True)
    if sample_size > 0:
        sample = sample[:sample_size]

    add_log(f"분석 대상: {len(sample):,}개")
    set_progress(0, len(sample), "수집중")

    sem = asyncio.Semaphore(20)
    results_raw = []
    done_count = [0]

    async def _async_get(client, path, p):
        try:
            r = await client.get(path, params={"crtfc_key": settings.OPENDART_API_KEY, **p}, timeout=120.0)
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

    async def fetch_one(client, corp_code, base, year):
        async with sem:
            try:
                info, emp, exc = await asyncio.gather(
                    _async_get(client, "/company.json", {"corp_code": corp_code}),
                    _async_get(client, "/empSttus.json", {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"}),
                    _async_get(client, "/exctvSttus.json", {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"}),
                    return_exceptions=True,
                )
                fin = await _async_get_fin(client, corp_code, year)
            except Exception:
                done_count[0] += 1
                return None

        if isinstance(info, Exception) or not info or info.get("status") != "000":
            done_count[0] += 1
            return None

        fin = fin if isinstance(fin, dict) else {}
        emp_list = emp.get("list", []) if isinstance(emp, dict) and emp.get("status") == "000" else []
        exc_list = exc.get("list", []) if isinstance(exc, dict) and exc.get("status") == "000" else []
        total_emp = sum(
            int((e.get("sumtrmcnt") or "0").replace(",", "") or 0)
            for e in emp_list
            if (e.get("sumtrmcnt") or "0").replace(",", "").isdigit()
        )
        done_count[0] += 1
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

    year = datetime.now(KST).year - 1

    async def update_progress():
        while True:
            await asyncio.sleep(2)
            with state_lock:
                if not job_state["running"]:
                    return
            set_progress(done_count[0], len(sample), "수집중")

    progress_task = asyncio.create_task(update_progress())

    async with httpx.AsyncClient(base_url=settings.DART_BASE_URL, timeout=120.0) as client:
        for i in range(0, len(sample), 100):
            with state_lock:
                if not job_state["running"]:
                    add_log("사용자가 중단했습니다", "error")
                    progress_task.cancel()
                    return
            batch = sample[i:i+100]
            batch_results = await asyncio.gather(
                *[fetch_one(client, cc, b, year) for cc, b in batch],
                return_exceptions=True,
            )
            for r in batch_results:
                if isinstance(r, CompanyData):
                    results_raw.append(r)
            if i > 0 and i % 1000 == 0:
                add_log(f"수집 진행: {done_count[0]:,} / {len(sample):,} (성공 {len(results_raw):,})")

    progress_task.cancel()
    set_progress(len(sample), len(sample), "필터링")
    add_log(f"수집 완료: {len(results_raw):,}개")

    revenue_min = params.get("revenue_min", 10_000_000_000)
    revenue_max = params.get("revenue_max", 500_000_000_000)
    emp_min = params.get("employees_min", 30)
    emp_max = params.get("employees_max", 999999)
    min_score = params.get("min_score", 20)
    top = params.get("top", 5000)

    candidates = [
        c for c in results_raw
        if (prefixes is None or any(c.industry.startswith(p) for p in prefixes))
        and passes_filter(c, revenue_min=revenue_min, revenue_max=revenue_max,
                          employees_min=emp_min, employees_max=emp_max)
    ]
    add_log(f"필터 통과: {len(candidates)}개")

    scored = []
    for c in candidates:
        total, bd = score_company(c)
        if total >= min_score:
            scored.append((total, bd, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    scored = scored[:top]

    if not scored:
        add_log("결과 없음", "error")
        with state_lock:
            job_state["running"] = False
            job_state["phase"] = "error"
            job_state["error"] = "결과 없음"
            save_state()
        return

    OUTPUT_DIR.mkdir(exist_ok=True)
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

    result_data = []
    for rank, (total, bd, c) in enumerate(scored, 1):
        tier = assign_tier(rank)
        pen = sum(bd.get(k,0) for k in ["pen_age","pen_industry","pen_decision","pen_recruit","pen_penalty"])
        money = sum(bd.get(k,0) for k in ["money_margin","money_growth","money_per_capita","money_scale"])
        result_data.append({
            "rank": rank, "tier": tier, "channel": recommend_channel(tier),
            "corp_name": c.corp_name, "score": total, "pen": pen, "money": money,
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

    with state_lock:
        job_state["results"] = result_data
        job_state["csv_path"] = csv_path
        job_state["running"] = False
        job_state["phase"] = "done"
        save_state()
        save_results()

        # 히스토리에 저장
        history_id = job_state.get("history_id") or uuid.uuid4().hex[:12]
        save_to_history(history_id, params, result_data, csv_path)

    add_log(f"완료! {len(scored)}개 추출", "accent")


if __name__ == "__main__":
    load_state()
    port = int(os.getenv("PORT", 8080))
    print(f"DART Finder Web UI v3: http://0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
