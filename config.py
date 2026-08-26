"""Configuration loading with environment overrides and safety validation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from errors import ConfigError


def _bool(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ConfigError(f"{name} must be true or false")


def _resolve(base: Path, value: object) -> Path:
    path = Path(str(value)).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _env_or(raw: dict[str, object], env: str, key: str, default: object) -> object:
    value = os.getenv(env)
    return value if value is not None else raw.get(key, default)


def _safe_sis_url(value: object, name: str) -> str:
    url = str(value).strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "sisn.hkust-gz.edu.cn":
        raise ConfigError(f"{name} must use https://sisn.hkust-gz.edu.cn")
    if parsed.username or parsed.password:
        raise ConfigError(f"{name} must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ConfigError(f"{name} must not contain a query string or fragment")
    return url


@dataclass(frozen=True)
class Settings:
    target_datetime: str
    timezone: str
    sis_url: str
    shopping_cart_url: str
    browser_channel: str
    user_data_dir: Path
    enroll_button_selector: str
    select_all_checkbox_selector: str
    select_all_click_target_selector: str
    expected_button_texts: tuple[str, ...]
    enable_pre_refresh: bool
    refresh_before_seconds: float
    manual_login_timeout_seconds: float
    keep_browser_open_after_run: bool
    require_arm_phrase_confirmation: bool
    click_confirmation_button: bool
    confirmation_button_selector: str
    screenshot_dir: Path
    log_file: Path
    lock_file: Path


def load_settings(path: Path) -> Settings:
    """Load YAML plus non-sensitive `.env` overrides."""

    path = path.resolve()
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot read config: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a mapping")

    base = path.parent
    texts_raw = raw.get("expected_button_texts", ["Enrol", "Enroll"])
    if not isinstance(texts_raw, list) or not texts_raw:
        raise ConfigError("expected_button_texts must be a non-empty list")

    settings = Settings(
        target_datetime=str(
            _env_or(raw, "TARGET_DATETIME", "target_datetime", "")
        ).strip(),
        timezone=str(_env_or(raw, "TIMEZONE", "timezone", "Asia/Shanghai")).strip(),
        sis_url=_safe_sis_url(
            _env_or(raw, "SIS_URL", "sis_url", "https://sisn.hkust-gz.edu.cn/"),
            "sis_url",
        ),
        shopping_cart_url=_safe_sis_url(
            _env_or(
                raw,
                "SHOPPING_CART_URL",
                "shopping_cart_url",
                "https://sisn.hkust-gz.edu.cn/student/student-my-cart",
            ),
            "shopping_cart_url",
        ),
        browser_channel=str(
            _env_or(raw, "BROWSER_CHANNEL", "browser_channel", "msedge")
        ).strip(),
        user_data_dir=_resolve(
            base,
            _env_or(
                raw,
                "USER_DATA_DIR",
                "user_data_dir",
                ".private/browser-profile",
            ),
        ),
        enroll_button_selector=str(
            _env_or(
                raw,
                "ENROLL_BUTTON_SELECTOR",
                "enroll_button_selector",
                "",
            )
        ).strip(),
        select_all_checkbox_selector=str(
            _env_or(
                raw,
                "SELECT_ALL_CHECKBOX_SELECTOR",
                "select_all_checkbox_selector",
                "",
            )
        ).strip(),
        select_all_click_target_selector=str(
            _env_or(
                raw,
                "SELECT_ALL_CLICK_TARGET_SELECTOR",
                "select_all_click_target_selector",
                "",
            )
        ).strip(),
        expected_button_texts=tuple(str(item).strip() for item in texts_raw),
        enable_pre_refresh=_bool(
            _env_or(raw, "ENABLE_PRE_REFRESH", "enable_pre_refresh", True),
            "enable_pre_refresh",
        ),
        refresh_before_seconds=float(
            _env_or(raw, "REFRESH_BEFORE_SECONDS", "refresh_before_seconds", 2)
        ),
        manual_login_timeout_seconds=float(
            _env_or(
                raw,
                "MANUAL_LOGIN_TIMEOUT_SECONDS",
                "manual_login_timeout_seconds",
                1800,
            )
        ),
        keep_browser_open_after_run=_bool(
            _env_or(
                raw,
                "KEEP_BROWSER_OPEN_AFTER_RUN",
                "keep_browser_open_after_run",
                True,
            ),
            "keep_browser_open_after_run",
        ),
        require_arm_phrase_confirmation=_bool(
            _env_or(
                raw,
                "REQUIRE_ARM_PHRASE_CONFIRMATION",
                "require_arm_phrase_confirmation",
                False,
            ),
            "require_arm_phrase_confirmation",
        ),
        click_confirmation_button=_bool(
            _env_or(
                raw,
                "CLICK_CONFIRMATION_BUTTON",
                "click_confirmation_button",
                False,
            ),
            "click_confirmation_button",
        ),
        confirmation_button_selector=str(
            _env_or(
                raw,
                "CONFIRMATION_BUTTON_SELECTOR",
                "confirmation_button_selector",
                "",
            )
        ).strip(),
        screenshot_dir=_resolve(
            base,
            _env_or(
                raw,
                "SCREENSHOT_DIR",
                "screenshot_dir",
                "var/screenshots",
            ),
        ),
        log_file=_resolve(
            base,
            _env_or(raw, "LOG_FILE", "log_file", "var/enroll-click.log"),
        ),
        lock_file=_resolve(
            base,
            _env_or(raw, "LOCK_FILE", "lock_file", ".private/enroll-click.lock"),
        ),
    )
    _validate(settings)
    return settings


def _validate(settings: Settings) -> None:
    if not settings.target_datetime:
        raise ConfigError("target_datetime is required")
    if settings.browser_channel.casefold() != "msedge":
        raise ConfigError("browser_channel must be msedge for this verified build")
    if settings.enroll_button_selector != "button.btn-cart-enrol":
        raise ConfigError(
            "enroll_button_selector must remain the verified button.btn-cart-enrol"
        )
    expected_select_all = (
        'div.mine-select input.el-checkbox__original[type="checkbox"]'
        '[value="Select All"]'
    )
    if settings.select_all_checkbox_selector != expected_select_all:
        raise ConfigError(
            "select_all_checkbox_selector must remain the verified Select All locator"
        )
    expected_select_all_click = (
        'div.mine-select label.el-checkbox:has('
        'input.el-checkbox__original[type="checkbox"][value="Select All"])'
    )
    if settings.select_all_click_target_selector != expected_select_all_click:
        raise ConfigError(
            "select_all_click_target_selector must remain the verified Select All "
            "label locator"
        )
    if not 1 <= settings.refresh_before_seconds <= 3:
        raise ConfigError("refresh_before_seconds must be between 1 and 3")
    if settings.manual_login_timeout_seconds < 60:
        raise ConfigError("manual_login_timeout_seconds must be at least 60")
    if settings.click_confirmation_button:
        raise ConfigError(
            "Second-step confirmation clicking is not implemented because its DOM "
            "has not been supplied and verified"
        )
