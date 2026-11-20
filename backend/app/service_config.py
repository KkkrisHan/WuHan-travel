r"""Local-only service credential persistence and runtime updates.

On Windows settings live in HKCU\Environment and are also refreshed into this
process immediately. Other platforms use a user config file outside the repo.
These stores are not encrypted credential vaults.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any
from uuid import uuid4
from dotenv import dotenv_values

from .config import settings

APP_KEY = "Software\\WuhanTravel\\Services"
DISABLED_KEY = "Software\\WuhanTravel\\DisabledServices"


def _registry_read(key: str) -> dict[str, Any]:
    if sys.platform != "win32":
        return {}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            value, _ = winreg.QueryValueEx(handle, "Config")
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _user_file() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming")
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "WuhanTravel" / "services.json"


def _frontend_env_value(name: str) -> str:
    """Read legacy Vite-only credentials so existing local installs are reused."""
    env_file = Path(__file__).resolve().parents[2] / "frontend" / ".env"
    try:
        return str(dotenv_values(env_file).get(name) or "")
    except OSError:
        return ""


def _legacy_js_key() -> str:
    return os.environ.get("VITE_AMAP_JS_KEY") or _frontend_env_value("VITE_AMAP_JS_KEY")


def _legacy_security_code() -> str:
    return (
        os.environ.get("AMAP_JS_SECURITY_CODE")
        or settings.amap_js_security_code
        or os.environ.get("VITE_AMAP_SECURITY_CODE")
        or _frontend_env_value("VITE_AMAP_SECURITY_CODE")
    )


def read_saved() -> dict[str, Any]:
    if sys.platform == "win32":
        return _registry_read(APP_KEY)
    try:
        data = json.loads(_user_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _write_saved(data: dict[str, Any]) -> None:
    encoded = json.dumps(data, ensure_ascii=False)
    if sys.platform == "win32":
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, APP_KEY, 0, winreg.KEY_SET_VALUE) as handle:
            winreg.SetValueEx(handle, "Config", 0, winreg.REG_SZ, encoded)
    else:
        path = _user_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(encoded, encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass


def _disabled() -> set[str]:
    if sys.platform == "win32":
        return set(_registry_read(DISABLED_KEY).get("services", []))
    return set(read_saved().get("_disabled", []))


def _write_disabled(values: set[str]) -> None:
    if sys.platform == "win32":
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, DISABLED_KEY, 0, winreg.KEY_SET_VALUE) as handle:
            winreg.SetValueEx(handle, "Config", 0, winreg.REG_SZ, json.dumps({"services": sorted(values)}))
    else:
        data = read_saved()
        data["_disabled"] = sorted(values)
        _write_saved(data)


def effective_config() -> dict[str, Any]:
    saved = read_saved()
    disabled = _disabled()
    provider = str(saved.get("provider") or settings.ai_provider or "openai").strip().lower()
    if provider not in {"deepseek", "openai"}:
        provider = "openai"
    result = {
        "amapJsKey": "" if "amap" in disabled else saved.get("amapJsKey") or _legacy_js_key(),
        "amapJsSecurityCode": "" if "amap" in disabled else saved.get("amapJsSecurityCode") or _legacy_security_code(),
        "amapWebServiceKey": "" if "amap" in disabled else saved.get("amapWebServiceKey") or settings.amap_web_service_key,
        "provider": provider,
        "deepseekApiKey": "" if "ai" in disabled else settings.deepseek_api_key,
        "openaiApiKey": "" if "ai" in disabled else settings.openai_api_key,
        "deepseekModel": settings.deepseek_model,
        "openaiModel": settings.openai_model,
    }
    if "ai" not in disabled:
        result["deepseekApiKey"] = saved.get("deepseekApiKey") or settings.deepseek_api_key
        result["openaiApiKey"] = saved.get("openaiApiKey") or settings.openai_api_key
        result["deepseekModel"] = saved.get("deepseekModel") or settings.deepseek_model
        result["openaiModel"] = saved.get("openaiModel") or settings.openai_model
    return result


def apply_runtime() -> None:
    config = effective_config()
    settings.amap_js_security_code = config["amapJsSecurityCode"]
    settings.amap_web_service_key = config["amapWebServiceKey"]
    settings.ai_provider = config["provider"]
    settings.deepseek_api_key = config["deepseekApiKey"]
    settings.openai_api_key = config["openaiApiKey"]
    settings.deepseek_model = config["deepseekModel"]
    settings.openai_model = config["openaiModel"]
    # Only the two reviewed official API origins are used for UI-managed credentials.
    settings.deepseek_base_url = "https://api.deepseek.com"
    settings.openai_base_url = "https://api.openai.com/v1"


def save_amap(js_key: str, security_code: str, web_key: str) -> None:
    data = read_saved()
    retained_js_key = js_key or data.get("amapJsKey") or _legacy_js_key()
    retained_security_code = security_code or data.get("amapJsSecurityCode") or _legacy_security_code()
    retained_web_key = web_key or data.get("amapWebServiceKey") or settings.amap_web_service_key
    data.update({"amapJsKey": retained_js_key, "amapJsSecurityCode": retained_security_code, "amapWebServiceKey": retained_web_key, "amapRevision": uuid4().hex})
    _write_saved(data)
    _write_disabled(_disabled() - {"amap"})
    _persist_env({
        "VITE_AMAP_JS_KEY": retained_js_key, "AMAP_JS_SECURITY_CODE": retained_security_code,
        "AMAP_WEB_SERVICE_KEY": retained_web_key,
    })
    apply_runtime()


def save_ai(provider: str, api_key: str, model: str) -> None:
    data = read_saved()
    data.update({"provider": provider, "deepseekModel": settings.deepseek_model, "openaiModel": settings.openai_model})
    data["deepseekApiKey" if provider == "deepseek" else "openaiApiKey"] = api_key
    data["deepseekModel" if provider == "deepseek" else "openaiModel"] = model
    _write_saved(data)
    _write_disabled(_disabled() - {"ai"})
    env_values = {
        "AI_PROVIDER": provider, "DEEPSEEK_MODEL": data["deepseekModel"],
        "OPENAI_MODEL": data["openaiModel"],
    }
    env_values["DEEPSEEK_API_KEY" if provider == "deepseek" else "OPENAI_API_KEY"] = api_key
    _persist_env(env_values)
    apply_runtime()


def remove_service(name: str) -> None:
    disabled = _disabled()
    disabled.add(name)
    _write_disabled(disabled)
    data = read_saved()
    if name == "amap":
        for field in ("amapJsKey", "amapJsSecurityCode", "amapWebServiceKey", "amapRevision"):
            data.pop(field, None)
    else:
        for field in ("provider", "deepseekApiKey", "openaiApiKey", "deepseekModel", "openaiModel"):
            data.pop(field, None)
    _write_saved(data)
    apply_runtime()


def _persist_env(values: dict[str, str]) -> None:
    # Keep explicit per-user environment variables for restart compatibility.
    for key, value in values.items():
        os.environ[key] = value
    if sys.platform != "win32":
        return
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as handle:
        for key, value in values.items():
            winreg.SetValueEx(handle, key, 0, winreg.REG_SZ, value)
    try:
        import ctypes
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None)
    except Exception:
        pass
