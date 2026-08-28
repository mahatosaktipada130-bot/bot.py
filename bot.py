from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import random
import re
import secrets
import shutil
import signal
import string
import subprocess
import sys
import importlib
import tarfile
import tempfile
import threading
import time
import traceback
import zipfile
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple


_REQUIRED_PKGS = [
    ("telebot",             "pyTelegramBotAPI"),
    ("requests",            "requests"),
    ("cryptography.fernet", "cryptography"),
    ("flask",               "flask"),
    ("apscheduler",         "APScheduler"),
    ("github",              "PyGithub"),
    ("psutil",              "psutil"),
    ("PIL",                 "Pillow"),
]


def _auto_install_missing() -> None:
    import importlib
    missing: List[str] = []
    for mod, pip_name in _REQUIRED_PKGS:
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(pip_name)
    if not missing:
        return
    print(f"[setup] installing missing packages: {', '.join(missing)}")
    strategies = [
        [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", *missing],
        [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet",
         "--break-system-packages", *missing],
        [sys.executable, "-m", "pip", "install", "--user", "--upgrade", "--quiet", *missing],
        [sys.executable, "-m", "pip", "install", "--user", "--upgrade", "--quiet",
         "--break-system-packages", *missing],
    ]
    last_err: Optional[Exception] = None
    for cmd in strategies:
        try:
            subprocess.run(cmd, check=True)
            print("[setup] install ok — continuing boot")
            return
        except Exception as e:
            last_err = e
            continue
    sys.exit(f"[x] auto-install failed after {len(strategies)} attempts: {last_err}. "
             f"Run manually: pip install {' '.join(missing)}")


_auto_install_missing()

import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException
import requests
from cryptography.fernet import Fernet, InvalidToken
from flask import Flask, jsonify

class Btn(types.InlineKeyboardButton):
    """InlineKeyboardButton with optional style support (Bot API 9.4+)."""
    def __init__(self, *args, style: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        if style:
            self.style = style  # type: ignore[attr-defined]

    def to_dict(self):
        d = super().to_dict()
        if getattr(self, "style", ""):
            d["style"] = self.style
        return d

_SEC_PATTERNS = {
    "🔴 Data Theft": [
        (r'os\.walk\s*\(\s*["\'][/\\](?:root|home|etc|var|proc)["\']',
                                                  "Root/system directory walk — server files chura raha hai"),
        (r'send_document\s*\(.*open\s*\(\s*["\'][/\\](?:root|etc|proc|sys)',
                                                  "System file bahar bhej raha hai"),
        (r'zipfile\.ZipFile.*["\']w["\'].*\bos\.walk\b.*["\'][/\\](?:root|etc|home)',
                                                  "System files ZIP mein pack karke bhej raha hai"),
        (r'glob\.glob\s*\(["\'][/\\]\*',          "Root glob scan — server files dhundh raha hai"),
        (r'shutil\.copy.*["\'][/\\]root',         "/root se copy kar raha hai"),
        (r'ROOT_DIR\s*=\s*["\'][/\\]["\']',       "Root directory target kar raha hai"),
    ],
    "🔴 Backdoor": [
        (r'subprocess\s*\.\s*(?:Popen|call|run)\s*\([^\n]*shell\s*=\s*True[^\n]*(?:input|stdin)',
                                                  "Shell injection with user input"),
        (r'marshal\.loads\s*\(',                  "Marshalled bytecode — obfuscated execution"),
    ],
    "🔴 Exposed Credentials": [],
    "🟡 Obfuscation": [
        (r'base64\.b64decode\s*\(.*\)\s*[\)\s]*\bexec\b',
                                                  "Base64 decode + execute — hidden code"),
        (r'(?:\\x[0-9a-fA-F]{2}){6,}',           "Long hex string — obfuscated code"),
        (r'zlib\.decompress\s*\(.*\)\s*[\)\s]*\bexec\b',
                                                  "Compressed + executed hidden code"),
    ],
    "🟡 Suspicious Network": [
        (r'devil-api\.com|elementfx\.io',         "Known malicious API endpoint"),
        (r'open\s*\(\s*["\'][/\\](?:root|etc|proc|sys).*(?:requests|urllib).*(?:post|put)',
                                                  "System file HTTP POST — data exfiltration"),
        (r'pastebin\.com/raw',                    "Pastebin raw fetch — remote code load"),
    ],
    "🟠 Resource Abuse": [
        (r'multiprocessing\.Pool\s*\(\s*(?:None|\d{3,})',
                                                  "Massive process pool — resource abuse"),
        (r'fork\s*\(\s*\).*fork\s*\(',            "Fork bomb pattern"),
    ],
}

_SEC_TOKEN_RE  = re.compile(r'\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b')


def _sec_static_scan(code: str) -> dict:
    results: Dict[str, List[str]] = {}
    for category, pattern_list in _SEC_PATTERNS.items():
        hits = []
        for pattern, description in pattern_list:
            if re.search(pattern, code, re.IGNORECASE | re.MULTILINE):
                hits.append(description)
        if hits:
            results[category] = hits
    tokens = _SEC_TOKEN_RE.findall(code)
    if tokens:
        results.setdefault("🔴 Exposed Credentials", [])
        results["🔴 Exposed Credentials"].append(f"Bot Token mila: {tokens[0][:15]}...")
    return results


def _sec_ast_scan(code: str) -> List[str]:
    import ast as _ast
    findings: List[str] = []
    try:
        tree = _ast.parse(code)
    except SyntaxError as e:
        findings.append(f"Code parse nahi hua: {e} - encoded/obfuscated ho sakta hai")
        return findings
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Call):
            func = node.func
            if isinstance(func, _ast.Attribute):
                if (func.attr == 'walk' and isinstance(func.value, _ast.Name)
                        and func.value.id == 'os' and node.args):
                    arg = node.args[0]
                    if isinstance(arg, _ast.Constant) and isinstance(arg.value, str):
                        if arg.value in ['/root', '/etc', '/home', '/proc']:
                            findings.append(f"os.walk('{arg.value}') - sensitive directory scan")
            if isinstance(func, _ast.Name) and func.id in ('eval', 'exec'):
                if node.args:
                    arg0 = node.args[0]
                    if isinstance(arg0, _ast.Call):
                        findings.append(f"Dangerous: {func.id}() — dynamic code execution")
                    elif isinstance(arg0, _ast.Attribute):
                        findings.append(f"Dangerous: {func.id}() — attribute-based input")
            if isinstance(func, _ast.Name) and func.id == '__import__':
                if node.args and isinstance(node.args[0], _ast.Constant):
                    if node.args[0].value == 'os':
                        findings.append("Dynamic __import__('os') — code injection")
    return findings


def _sec_calculate_risk(static_findings: dict, ast_findings: List[str]) -> int:
    weights = {
        "🔴 Data Theft":          40,
        "🔴 Backdoor":            40,
        "🔴 Exposed Credentials": 10,
        "🟡 Suspicious Network":  12,
        "🟡 Obfuscation":         10,
        "🟠 Resource Abuse":       8,
    }
    score = sum(weights.get(cat, 5) * min(len(hits), 3)
                for cat, hits in static_findings.items()
                if hits)
    unique_ast = list(dict.fromkeys(ast_findings))
    score += min(len(unique_ast) * 5, 20)
    return min(score, 100)


def _sec_get_verdict(risk_score: int, static_findings: dict) -> Tuple[str, str]:
    has_blocking = any(
        static_findings.get(c)
        for c in ("🔴 Data Theft", "🔴 Backdoor")
    )
    has_credentials = bool(static_findings.get("🔴 Exposed Credentials"))

    if has_blocking and risk_score >= 70:
        return "DANGEROUS", "REJECT"
    if risk_score >= 85:
        return "DANGEROUS", "REJECT"
    if has_credentials and not has_blocking and risk_score < 40:
        return "SUSPICIOUS", "MANUAL_REVIEW"
    if has_blocking and risk_score >= 35:
        return "SUSPICIOUS", "MANUAL_REVIEW"
    if risk_score >= 55:
        return "SUSPICIOUS", "MANUAL_REVIEW"
    return "SAFE", "APPROVE"


def _sec_scan_code(code: str, filename: str = "file.py") -> dict:
    sf = _sec_static_scan(code)
    af = _sec_ast_scan(code)
    risk = _sec_calculate_risk(sf, af)
    verdict, recommendation = _sec_get_verdict(risk, sf)
    all_threats: List[str] = [f"{c}: {h}" for c, hits in sf.items() for h in hits] + af
    if verdict == "DANGEROUS":
        summary = f"⚠️ File DANGEROUS hai! {len(all_threats)} threats mili hain."
    elif verdict == "SUSPICIOUS":
        summary = "🔍 File suspicious hai. Admin se manual review karwao."
    else:
        summary = "✅ File safe lagti hai. Koi major threat nahi mila."
    return {"verdict": verdict, "risk_score": risk, "findings": sf,
            "ast_findings": af, "all_threats": all_threats,
            "recommendation": recommendation, "summary": summary, "filename": filename}


def _sec_scan_archive(file_path: str) -> dict:
    tmp = tempfile.mkdtemp()
    try:
        if file_path.endswith('.zip'):
            with zipfile.ZipFile(file_path, 'r') as z:
                for name in z.namelist():
                    if name.startswith('/') or '..' in name:
                        return {"verdict": "DANGEROUS", "risk_score": 99,
                                "findings": {"🔴 Zip Slip Attack": ["Dangerous file paths in ZIP!"]},
                                "ast_findings": [], "recommendation": "REJECT",
                                "summary": "ZIP Slip attack detected!", "all_threats": []}
                z.extractall(tmp)
        elif file_path.endswith(('.tar.gz', '.tgz', '.tar')):
            with tarfile.open(file_path, 'r:*') as t:
                t.extractall(tmp)
        py_files = list(Path(tmp).rglob("*.py"))
        if not py_files:
            return {"verdict": "SUSPICIOUS", "risk_score": 20,
                    "findings": {"🟡 Warning": ["Koi .py file nahi mili archive mein"]},
                    "ast_findings": [], "recommendation": "MANUAL_REVIEW",
                    "summary": "Archive mein Python files nahi hain.", "all_threats": []}
        worst = None
        for py_file in py_files[:10]:
            try:
                result = _sec_scan_code(py_file.read_text(errors='ignore'), py_file.name)
                if worst is None or result['risk_score'] > worst['risk_score']:
                    worst = result
            except Exception:
                continue
        return worst or {"verdict": "SAFE", "risk_score": 0, "recommendation": "APPROVE",
                         "summary": "Safe lagti hai", "all_threats": []}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _scan_file(file_path: str) -> dict:
    filename = os.path.basename(file_path)
    try:
        if filename.lower().endswith(('.zip', '.tar.gz', '.tgz', '.tar')):
            return _sec_scan_archive(file_path)
        elif filename.lower().endswith(('.py', '.pyc', '.pyo', '.js')):
            with open(file_path, 'r', errors='ignore') as _f:
                return _sec_scan_code(_f.read(), filename)
        else:
            return {"verdict": "SUSPICIOUS", "risk_score": 30,
                    "findings": {"🟡 Warning": [f"Unknown file type: {filename}"]},
                    "ast_findings": [], "recommendation": "MANUAL_REVIEW",
                    "summary": f"File type '{filename}' allow nahi hai.",
                    "all_threats": [], "filename": filename}
    except Exception as _e:
        return {"verdict": "ERROR", "risk_score": 50, "findings": {},
                "ast_findings": [], "recommendation": "MANUAL_REVIEW",
                "summary": f"Scan error: {_e}", "all_threats": [], "filename": filename}

_SCANNER_OK = True

try:
    import os as _os, sys as _sys
    _here = _os.path.dirname(_os.path.abspath(__file__))
    if _here and _here not in _sys.path:
        _sys.path.insert(0, _here)
    from security_scanner_free import scan_file as _scan_file  # noqa: F811
    _SCANNER_OK = True
except Exception as _ssf_err:
    pass

import urllib.request as _urllib_req
import json as _json

_AI_SCAN_PROMPT = """You are a security expert reviewing uploaded bot code.
Analyze the code below for malicious behavior. Look for:
1. Data theft — reading/sending server files, credentials, databases
2. Backdoors — eval/exec with remote payloads, hidden commands
3. Spyware — logging user data secretly and sending it out
4. Credential theft — stealing tokens, passwords, API keys
5. Resource abuse — fork bombs, crypto mining

Reply ONLY with a JSON object (no markdown, no extra text):
{
  "verdict": "SAFE" | "SUSPICIOUS" | "DANGEROUS",
  "risk_score": <0-100>,
  "reason": "<one sentence summary in simple language>",
  "threats": ["<threat1>", "<threat2>"]
}

IMPORTANT: Normal Telegram bots that use telebot, infinity_polling, CommandHandler,
send_message, send_document for their OWN users are SAFE. Do NOT flag standard
Telegram bot patterns as malicious.

CODE TO ANALYZE:
"""

def _ai_scan_code(code: str, filename: str = "file.py") -> Optional[Dict[str, Any]]:
    base_url = os.environ.get("AI_INTEGRATIONS_OPENROUTER_BASE_URL", "").rstrip("/")
    api_key  = os.environ.get("AI_INTEGRATIONS_OPENROUTER_API_KEY", "no-key")
    if not base_url:
        return None

    code_snippet = code[:6000]
    payload = _json.dumps({
        "model": "google/gemma-4-31b-it:free",
        "max_tokens": 512,
        "temperature": 0.1,
        "messages": [
            {"role": "user", "content": f"{_AI_SCAN_PROMPT}{code_snippet}"}
        ]
    }).encode("utf-8")

    req = _urllib_req.Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST"
    )
    try:
        with _urllib_req.urlopen(req, timeout=30) as resp:
            body = _json.loads(resp.read())
        content = body["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        result = _json.loads(content)
        return {
            "ai_verdict":    result.get("verdict", "SAFE"),
            "ai_risk_score": int(result.get("risk_score", 0)),
            "ai_reason":     result.get("reason", ""),
            "ai_threats":    result.get("threats", []),
        }
    except Exception as _ai_err:
        return None


def _combined_scan(file_path: str) -> dict:
    pattern_result = _scan_file(file_path)
    filename = os.path.basename(file_path)

    ai_result = None
    if filename.lower().endswith(('.py', '.js', '.ts')):
        try:
            with open(file_path, 'r', errors='ignore') as _f:
                ai_result = _ai_scan_code(_f.read(), filename)
        except Exception:
            pass

    if ai_result is None:
        return pattern_result

    ai_risk  = ai_result["ai_risk_score"]
    pat_risk = pattern_result.get("risk_score", 0)
    merged_risk = int(ai_risk * 0.6 + pat_risk * 0.4)

    ai_v  = ai_result["ai_verdict"]
    pat_v = pattern_result.get("verdict", "SAFE")

    if ai_v == "DANGEROUS":
        verdict = "DANGEROUS"; recommendation = "REJECT"
    elif ai_v == "SUSPICIOUS" or pat_v == "DANGEROUS":
        verdict = "SUSPICIOUS"; recommendation = "MANUAL_REVIEW"
    elif pat_v == "SUSPICIOUS":
        verdict = "SUSPICIOUS"; recommendation = "MANUAL_REVIEW"
    else:
        verdict = "SAFE"; recommendation = "APPROVE"

    all_threats = list(pattern_result.get("all_threats", []))
    for t in ai_result.get("ai_threats", []):
        entry = f"🤖 AI: {t}"
        if entry not in all_threats:
            all_threats.append(entry)

    ai_label = f"🤖 AI ({ai_v} {ai_risk}/100): {ai_result['ai_reason']}"
    if verdict == "DANGEROUS":
        summary = f"⚠️ File DANGEROUS hai! {ai_label}"
    elif verdict == "SUSPICIOUS":
        summary = f"🔍 File suspicious hai. {ai_label}"
    else:
        summary = f"✅ File safe hai. {ai_label}"

    return {
        **pattern_result,
        "verdict":        verdict,
        "risk_score":     merged_risk,
        "recommendation": recommendation,
        "summary":        summary,
        "all_threats":    all_threats,
        "ai_result":      ai_result,
    }

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter  # type: ignore
    _PIL_OK = True
except Exception:
    Image = ImageDraw = ImageFont = ImageFilter = None  # type: ignore
    _PIL_OK = False

try:
    import psutil
except ImportError:
    psutil = None

BASE_DIR = Path(__file__).resolve().parent

DIRS: Dict[str, Path] = {
    "uploads":  BASE_DIR / "storage" / "uploads",
    "encfiles": BASE_DIR / "storage" / "encfiles",
    "data":     BASE_DIR / "storage" / "data",
    "logs":     BASE_DIR / "storage" / "logs",
    "backups":  BASE_DIR / "storage" / "backups",
    "sandbox":  BASE_DIR / "sandbox",
    "tickets":  BASE_DIR / "storage" / "tickets",
    "bot_data": BASE_DIR / "storage" / "bot_data",
    "photos":   BASE_DIR / "storage" / "photos",
}
for _p in DIRS.values():
    _p.mkdir(parents=True, exist_ok=True)

DB_FILE       = DIRS["data"] / "panel_db.json"
SETTINGS_FILE = DIRS["data"] / "panel_settings.json"
AUDIT_FILE    = DIRS["data"] / "audit.log"
KEYRING_FILE  = DIRS["data"] / "keyring.json"

# HARDCODED BOT TOKEN & OWNER ID (COMPLETELY FREE UNLOCKED)
BOT_TOKEN_HARDCODED = "8782806153:AAETtoRmSFRJUu2YZW3w_LS1fLIGLqxI2uU"
TOKEN = BOT_TOKEN_HARDCODED
OWNER_ID = 8782806153

ANNOUNCE_CHANNEL = os.environ.get("ANNOUNCE_CHANNEL", "").strip()
try:
    KEEPALIVE_PORT = int(os.environ.get("PORT", 10460))
except (TypeError, ValueError):
    KEEPALIVE_PORT = 10000

BRAND       = "ѕιмяαη нoѕтιηg ＲΒOT"
BRAND_VER   = "v2.1 [UNLOCKED FREE]"
BRAND_TAG   = f"{BRAND} {BRAND_VER}"
SUPPORT_USR = "@nur7871"
UPDATE_CH   = "https://t.me/+MXtA9ufCgok3Yjc1"
FOOTER      = f"\n\n<blockquote>{BRAND_TAG}</blockquote>"

G = {
    "ok": "✓", "no": "\u2718", "warn": "\u26A0", "arrow": "\u2192", "bullet": "\u2022",
    "tri": "\u25B8", "diamond": "\u25C6", "star": "\u2605", "spark": "\u2726", "back": "↲",
    "fwd": "\u25B6", "plus": "\u2295", "minus": "\u2296", "rec": "\u25C9", "rec_off": "\u25CB",
    "div": "\u2501" * 16, "div_eq": "\u2550" * 16, "div_dash": "\u2508" * 16,
    "block_on": "\u25A0", "block_off": "\u25A1", "border_top": "\u2550" * 16,
    "border_mid": "\u2501" * 16, "border_bot": "\u2550" * 16, "play": "‣", "stop": "\u25A0",
    "pause": "\u2759\u2759", "refresh": "\u21BB", "running": "\u25B6", "stopped": "■",
    "restarting": "\u21BB", "stop_bot": "■", "lock": "\u25A3", "unlock": "\u25A2",
    "secure": "\u25C8", "key": "\u2756", "shield": "\u25C7", "ban": "\u2694", "trash": "\u2716",
    "eye": "\u25C9", "user": "\u25C8", "users": "\u25CE", "crown": "\u2654", "wallet": "\u25C6",
    "premium": "⌬", "lifetime": "\u2736", "gift": "\u2726", "ticket": "\u273F", "trophy": "\u2605",
    "graph": "\u25AA", "stats": "\u25AA", "chart_up": "\u25B2", "plan": "\u25A4",
    "broadcast": "⚑", "chat": "\u25AB", "folder": "\u25B8", "upload": "\u25B4",
    "download": "\u25BE", "cloud": "\u2601", "settings": "⚙", "cog": "\u2699",
    "bolt": "\u26A1", "clock": "\u23F1",
}

# ─── TOTALLY FREE UNLOCKED PLANS ───
PLAN_LIMITS: Dict[str, Dict[str, Any]] = {
    "free":       {"name": "Free Unlimited", "max_bots": 999, "ram": 16384, "auto_restart": True, "price": 0, "days": 36500},
    "starter":    {"name": "Free Starter",   "max_bots": 999, "ram": 16384, "auto_restart": True, "price": 0, "days": 36500},
    "basic":      {"name": "Free Basic",     "max_bots": 999, "ram": 16384, "auto_restart": True, "price": 0, "days": 36500},
    "pro":        {"name": "Free Pro",       "max_bots": 999, "ram": 16384, "auto_restart": True, "price": 0, "days": 36500},
    "enterprise": {"name": "Free VIP",       "max_bots": 999, "ram": 16384, "auto_restart": True, "price": 0, "days": 36500},
    "lifetime":   {"name": "Free Lifetime",  "max_bots": 999, "ram": 16384, "auto_restart": True, "price": 0, "days": 36500},
}

PAYMENT_METHODS: Dict[str, Dict[str, Any]] = {
    "free": {"name": "Free Access", "number": "No payment needed", "type": "FREE", "tag": "[FREE]"}
}

def main_menu_kb(admin: bool = False) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("  Mʏ Bᴏᴛꜱ", callback_data="menu_bots", style="primary"),
        Btn("  Uᴘʟᴏᴀ德 Bᴏᴛ", callback_data="menu_upload", style="success"),
    )
    kb.add(
        Btn("  Pʟᴀɴꜱ", callback_data="menu_plans", style="primary"),
        Btn("  Pʀᴏꜰɪʟᴇ", callback_data="menu_profile", style="primary"),
    )
    kb.add(
        Btn("  Wᴀʟʟᴇᴛ", callback_data="menu_wallet", style="primary"),
        Btn("  Rᴇꜰᴇʀʀᴀʟ", callback_data="menu_referral", style="primary"),
    )
    kb.add(
        Btn("  Hᴇʟᴘ", callback_data="menu_help", style="primary"),
        Btn("  Sᴜᴘᴘᴏʀᴛ", callback_data="menu_support", style="primary"),
    )
    if admin:
        kb.add(
            Btn("  Aᴅᴍɪɴ Pᴀɴᴇʟ", callback_data="menu_admin", style="danger")
        )
    return kb