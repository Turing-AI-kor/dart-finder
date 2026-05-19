"""
DART Finder 진단 도구
- /diag URL로 접근하면 어디가 문제인지 한 눈에 보임
- 실행: 이 파일을 ~/dart-finder/ 에 두고 web.py가 자동 임포트
- 또는 직접 실행: python3 diagnose.py
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    import requests
    import httpx
except Exception:
    requests = httpx = None


def check_api_key():
    """API 키 존재 확인."""
    env_file = Path(".env")
    if not env_file.exists():
        return {"ok": False, "msg": ".env 파일 없음"}
    content = env_file.read_text()
    if "OPENDART_API_KEY=" not in content:
        return {"ok": False, "msg": "API 키 환경변수 누락"}
    key = content.split("OPENDART_API_KEY=")[1].split("\n")[0].strip()
    if len(key) != 40:
        return {"ok": False, "msg": f"API 키 길이 이상: {len(key)}자 (40자 정상)"}
    return {"ok": True, "msg": f"키 정상 ({key[:8]}...{key[-4:]})"}


def check_dns():
    """DNS 해석."""
    try:
        import socket
        ip = socket.gethostbyname("opendart.fss.or.kr")
        return {"ok": True, "msg": f"DNS OK → {ip}"}
    except Exception as e:
        return {"ok": False, "msg": f"DNS 실패: {e}"}


def check_curl():
    """curl로 DART API 호출."""
    key = os.getenv("OPENDART_API_KEY", "")
    if not key:
        env = Path(".env").read_text() if Path(".env").exists() else ""
        if "OPENDART_API_KEY=" in env:
            key = env.split("OPENDART_API_KEY=")[1].split("\n")[0].strip()

    if not key:
        return {"ok": False, "msg": "키 없음"}

    url = f"https://opendart.fss.or.kr/api/company.json?crtfc_key={key}&corp_code=00126380"
    try:
        start = time.time()
        r = subprocess.run(
            ["curl", "-s", "-m", "30", "-w", "%{http_code}", url],
            capture_output=True, timeout=35, text=True,
        )
        elapsed = time.time() - start
        output = r.stdout
        # 마지막 3자가 HTTP code
        status = output[-3:] if len(output) >= 3 else "???"
        body = output[:-3]
        if status == "200":
            try:
                data = json.loads(body)
                api_status = data.get("status", "?")
                api_msg = data.get("message", "")
                return {
                    "ok": api_status == "000",
                    "msg": f"curl OK ({elapsed:.1f}초) status={api_status} {api_msg}"
                }
            except Exception:
                return {"ok": False, "msg": f"curl HTTP 200 but JSON 파싱 실패: {body[:100]}"}
        else:
            return {"ok": False, "msg": f"curl HTTP {status} ({elapsed:.1f}초)"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "msg": "curl 30초 타임아웃"}
    except Exception as e:
        return {"ok": False, "msg": f"curl 에러: {e}"}


def check_python_requests():
    """Python requests로 DART API 호출."""
    if requests is None:
        return {"ok": False, "msg": "requests 라이브러리 없음"}
    key = _get_key()
    if not key:
        return {"ok": False, "msg": "키 없음"}

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Connection": "close",
    }
    try:
        start = time.time()
        r = requests.get(
            "https://opendart.fss.or.kr/api/company.json",
            params={"crtfc_key": key, "corp_code": "00126380"},
            headers=headers, timeout=30,
        )
        elapsed = time.time() - start
        if r.status_code == 200:
            data = r.json()
            return {
                "ok": data.get("status") == "000",
                "msg": f"requests OK ({elapsed:.1f}초) status={data.get('status')} {data.get('message', '')}"
            }
        return {"ok": False, "msg": f"requests HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "msg": f"requests 에러: {type(e).__name__}: {str(e)[:100]}"}


def check_external_ip():
    """외부 IP 조회."""
    try:
        r = subprocess.run(["curl", "-s", "-m", "5", "https://ifconfig.me"],
                           capture_output=True, timeout=10, text=True)
        ip = r.stdout.strip()
        return {"ok": bool(ip), "msg": f"외부 IP: {ip}"}
    except Exception as e:
        return {"ok": False, "msg": f"IP 조회 실패: {e}"}


def check_disk():
    """디스크 공간."""
    try:
        r = subprocess.run(["df", "-h", "/home"], capture_output=True, text=True)
        lines = r.stdout.strip().split("\n")
        if len(lines) >= 2:
            cols = lines[1].split()
            usage = cols[4] if len(cols) > 4 else "?"
            avail = cols[3] if len(cols) > 3 else "?"
            pct = int(usage.replace("%", "")) if "%" in usage else 0
            return {
                "ok": pct < 90,
                "msg": f"디스크 사용 {usage}, 여유 {avail}"
            }
    except Exception as e:
        return {"ok": False, "msg": f"디스크 조회 실패: {e}"}
    return {"ok": False, "msg": "?"}


def check_memory():
    """메모리."""
    try:
        r = subprocess.run(["free", "-m"], capture_output=True, text=True)
        lines = r.stdout.strip().split("\n")
        if len(lines) >= 2:
            cols = lines[1].split()
            total = int(cols[1])
            used = int(cols[2])
            avail = int(cols[6]) if len(cols) > 6 else 0
            pct = used * 100 // total
            return {
                "ok": pct < 90,
                "msg": f"메모리 {used}MB / {total}MB ({pct}%), 가용 {avail}MB"
            }
    except Exception as e:
        return {"ok": False, "msg": f"메모리 조회 실패: {e}"}
    return {"ok": False, "msg": "?"}


def check_service():
    """systemd dart.service 상태."""
    try:
        r = subprocess.run(["systemctl", "is-active", "dart"],
                           capture_output=True, text=True)
        active = r.stdout.strip() == "active"
        return {"ok": active, "msg": f"dart.service: {r.stdout.strip()}"}
    except Exception as e:
        return {"ok": False, "msg": f"systemctl 실패: {e}"}


def check_port():
    """80 또는 8080 포트 LISTEN."""
    try:
        r = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True)
        out = r.stdout
        port80 = ":80 " in out or ":80\t" in out
        port8080 = ":8080 " in out or ":8080\t" in out
        if port80:
            return {"ok": True, "msg": "포트 80 LISTEN"}
        if port8080:
            return {"ok": True, "msg": "포트 8080 LISTEN"}
        return {"ok": False, "msg": "80/8080 둘 다 LISTEN 안 함"}
    except Exception as e:
        return {"ok": False, "msg": f"ss 실패: {e}"}


def check_dart_status():
    """DART 일일 사용량 (옵션, 로그인 필요라 패스)."""
    return {"ok": True, "msg": "수동 확인: https://opendart.fss.or.kr/mng/apiUsageStatusView.do"}


def check_recent_errors():
    """최근 dart.service 로그에서 에러 패턴."""
    try:
        r = subprocess.run(
            ["journalctl", "-u", "dart", "-n", "200", "--no-pager"],
            capture_output=True, text=True
        )
        log = r.stdout
        errors = {
            "Server disconnected": log.count("Server disconnected"),
            "Connection reset": log.count("Connection reset"),
            "RemoteProtocolError": log.count("RemoteProtocolError"),
            "ReadTimeout": log.count("ReadTimeout"),
            "ConnectionError": log.count("ConnectionError"),
            "SSL": log.count("SSLError"),
            "치명적": log.count("치명적"),
        }
        nonzero = {k: v for k, v in errors.items() if v > 0}
        if nonzero:
            top = max(nonzero.items(), key=lambda x: x[1])
            return {"ok": False, "msg": f"최근 에러 패턴: {nonzero}, 가장 많음: {top[0]} ({top[1]}회)"}
        return {"ok": True, "msg": "최근 에러 패턴 없음"}
    except Exception as e:
        return {"ok": False, "msg": f"journalctl 실패: {e}"}


def _get_key():
    if Path(".env").exists():
        env = Path(".env").read_text()
        if "OPENDART_API_KEY=" in env:
            return env.split("OPENDART_API_KEY=")[1].split("\n")[0].strip()
    return os.getenv("OPENDART_API_KEY", "")


def run_all():
    """전체 진단 실행."""
    checks = [
        ("1. API 키", check_api_key),
        ("2. DNS 해석", check_dns),
        ("3. 외부 IP", check_external_ip),
        ("4. 디스크", check_disk),
        ("5. 메모리", check_memory),
        ("6. dart.service", check_service),
        ("7. 포트 LISTEN", check_port),
        ("8. curl로 DART", check_curl),
        ("9. requests로 DART", check_python_requests),
        ("10. 최근 에러 로그", check_recent_errors),
        ("11. DART 한도", check_dart_status),
    ]
    results = []
    for name, fn in checks:
        try:
            r = fn()
            r["name"] = name
            results.append(r)
        except Exception as e:
            results.append({"name": name, "ok": False, "msg": f"진단 실패: {e}"})
    return results


def print_console():
    """터미널 출력."""
    results = run_all()
    print("=" * 60)
    print("DART Finder 진단 결과")
    print("=" * 60)
    for r in results:
        icon = "✓" if r["ok"] else "✗"
        color = "\033[32m" if r["ok"] else "\033[31m"
        reset = "\033[0m"
        print(f"{color}{icon}{reset}  {r['name']:25s} {r['msg']}")
    print("=" * 60)
    ok = sum(1 for r in results if r["ok"])
    print(f"통과: {ok}/{len(results)}")
    failed = [r for r in results if not r["ok"]]
    if failed:
        print("\n실패 항목:")
        for r in failed:
            print(f"  - {r['name']}: {r['msg']}")


if __name__ == "__main__":
    print_console()
