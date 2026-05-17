from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "zhongran_online"
PLATFORMS: list[Platform] = [Platform.SENSOR]

CONF_ACCESS_TOKEN = "access_token"
CONF_CUST_CODE = "cust_code"
CONF_CUST_NAME = "cust_name"
CONF_MINI_OPEN_ID = "mini_open_id"
CONF_MOBILE = "mobile"
CONF_SCAN_INTERVAL_HOURS = "scan_interval_hours"
CONF_SID = "sid"
CONF_UNION_ID = "union_id"
CONF_USER_ID = "user_id"

ACCESS_FROM = "yphpaymp"
APP_INFO_PREFIX = "aaahg10001"
BASE_URL = "https://zrds.95007.com"
DEFAULT_SCAN_INTERVAL_HOURS = 6
DEFAULT_SCAN_INTERVAL = timedelta(hours=DEFAULT_SCAN_INTERVAL_HOURS)
PLATFORM = "mp-weixin"
REFERER = "https://servicewechat.com/wx2082cbdc25b3b8e6/107/page-frame.html"
SIGNATURE_SALT = "yph1234567890"
