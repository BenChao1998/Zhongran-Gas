from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
from typing import Any, Mapping
from urllib.parse import urlencode

from aiohttp import ClientError, ClientSession

from .const import ACCESS_FROM, APP_INFO_PREFIX, BASE_URL, PLATFORM, REFERER, SIGNATURE_SALT

_LOGGER = logging.getLogger(__name__)

SEND_SMS_CODE = "/user/sendsms3.do"
LOGIN_BY_MOBILE = "/user/xcxMobileUserLogin"
GET_BIND_GAS_CUSTOMERS = "/crm_controller/user/getBindGasCustList"
GET_CUSTOMER_INFO = "/crm_controller/user/findCustInfoByCustCodeAndCustName"
GET_GAS_CONSUMPTION = "/crm_controller/user/getGasConsumption"
GET_PAYMENT_LIST = "/crm_controller/payfee/getPaymentList"
IMAGE_CODE_PATH = "/controller/merchant/authCode.do"
WECHAT_CHANNEL_TYPE = 0


class ZhongranApiError(Exception):
    """Base API error."""


class ZhongranAuthError(ZhongranApiError):
    """Authentication or authorization error."""


class ZhongranSignatureError(ZhongranApiError):
    """Request signing failed."""


@dataclass(frozen=True)
class ZhongranCredentials:
    """Session material used by the Zhongran mini program."""

    user_id: str
    access_token: str
    sid: str
    cust_code: str = ""
    cust_name: str = ""
    mobile: str | None = None
    union_id: str | None = None
    mini_open_id: str | None = None

    @property
    def x_mas_app_info(self) -> str:
        """Build the x-mas-app-info header value."""
        if self.sid.startswith(f"{APP_INFO_PREFIX}/"):
            return self.sid
        return f"{APP_INFO_PREFIX}/{self.sid}"


class ZhongranSigner:
    """Implement the request signature used by the Zhongran mini program."""

    def sign_v2(
        self,
        payload: Mapping[str, Any],
        preferred_field: str | None = None,
    ) -> dict[str, Any]:
        """Attach `timeStamp` and `signature` to a payload."""
        signed_payload = dict(payload)
        timestamp = signed_payload.get("timeStamp") or _timestamp_ms()
        signed_payload["timeStamp"] = timestamp

        signature_source = _resolve_signature_source(signed_payload, preferred_field)
        if signature_source is None:
            raise ZhongranSignatureError(
                "Could not determine the Zhongran signature source field for this request."
            )

        signed_payload["signature"] = _md5hex(
            f"{signature_source}{SIGNATURE_SALT}{timestamp}"
        )
        return signed_payload


class ZhongranApiClient:
    """Async client for the Zhongran mini-program APIs."""

    def __init__(
        self,
        session: ClientSession,
        credentials: ZhongranCredentials | None = None,
    ) -> None:
        self._session = session
        self._credentials = credentials
        self._signer = ZhongranSigner()

    async def async_send_sms_code(self, mobile: str, image_code: str) -> None:
        """Trigger the Zhongran SMS verification code."""
        await self._request_json(
            "POST",
            SEND_SMS_CODE,
            headers=self._public_headers("application/x-www-form-urlencoded"),
            params={
                "codeKey": mobile,
                "codeKeyValue": image_code,
                "mobile": mobile,
            },
        )

    async def async_login_by_mobile(
        self,
        mobile: str,
        sms_code: str,
        *,
        union_id: str | None = None,
        mini_open_id: str | None = None,
    ) -> ZhongranCredentials:
        """Log in with an SMS verification code and store the new session."""
        payload = await self._request_json(
            "POST",
            LOGIN_BY_MOBILE,
            headers=self._public_headers("application/x-www-form-urlencoded"),
            params={
                "channelType": WECHAT_CHANNEL_TYPE,
                "mobile": mobile,
                "code": sms_code,
                "unionId": union_id or "",
                "miniOpenId": mini_open_id or "",
            },
        )

        data = _require_dict(payload.get("data"), "Login response data")
        sid = str(payload.get("sid") or "")
        access_token = str(payload.get("masToken") or "")
        user_id = str(data.get("id") or payload.get("userId") or "")

        if not sid or not access_token or not user_id:
            raise ZhongranApiError(
                "SMS login succeeded, but Zhongran did not return a complete session."
            )

        self._credentials = ZhongranCredentials(
            user_id=user_id,
            access_token=access_token,
            sid=sid,
            mobile=mobile,
            union_id=union_id or None,
            mini_open_id=mini_open_id or None,
        )
        return self._credentials

    async def async_fetch_dashboard(self) -> dict[str, Any]:
        """Fetch the account summary used by the HA entities."""
        accounts = await self.async_get_bound_gas_customers()
        selected_account = self._select_account(accounts)

        customer_info = await self.async_get_customer_info(
            selected_account["custCode"],
            selected_account["custName"],
        )
        consumption = await self.async_get_gas_consumption(
            selected_account["custCode"],
            selected_account["custName"],
        )
        payments = await self.async_get_payment_list(selected_account["custCode"])

        latest_consumption = consumption[0] if consumption else None
        latest_payment = payments[0] if payments else None

        return {
            "accounts": accounts,
            "selected_account": selected_account,
            "customer_info": customer_info,
            "consumption": consumption,
            "latest_consumption": latest_consumption,
            "payments": payments,
            "latest_payment": latest_payment,
            "refreshed_at": datetime.now(tz=UTC).isoformat(),
        }

    async def async_get_bound_gas_customers(self) -> list[dict[str, Any]]:
        """Return the gas accounts bound to the current session."""
        credentials = self._require_credentials()
        payload = {
            "userId": credentials.user_id,
            "state": 1,
        }
        response = await self._post_form(
            GET_BIND_GAS_CUSTOMERS,
            payload,
            sign_field="userId",
        )
        return _require_list(response.get("data"), "Bound gas customer list")

    async def async_get_customer_info(
        self,
        cust_code: str,
        cust_name: str,
    ) -> dict[str, Any]:
        """Return detailed customer and meter information."""
        payload = {
            "custCode": cust_code,
            "custName": cust_name,
        }
        response = await self._post_form(
            GET_CUSTOMER_INFO,
            payload,
            sign_field="custCode",
        )
        return _require_dict(response.get("data"), "Customer info")

    async def async_get_gas_consumption(
        self,
        cust_code: str,
        cust_name: str,
        months: int = 12,
    ) -> list[dict[str, Any]]:
        """Return recent gas consumption records."""
        start_month, end_month = _month_window(months)
        payload = {
            "custCode": cust_code,
            "custName": cust_name,
            "startTime": start_month,
            "endTime": end_month,
        }
        response = await self._post_json(
            GET_GAS_CONSUMPTION,
            payload,
            sign_field="custCode",
        )
        data = _require_dict(response.get("data"), "Gas consumption payload")
        series = data.get("gasConsumptionList") or []
        if series and isinstance(series[0], list):
            return [item for item in series[0] if isinstance(item, dict)]
        return [item for item in series if isinstance(item, dict)]

    async def async_get_payment_list(
        self,
        cust_code: str,
        days: int = 365,
    ) -> list[dict[str, Any]]:
        """Return recent payment records."""
        start_day, end_day = _date_window(days)
        payload = {
            "custCode": cust_code,
            "startTime": start_day,
            "endTime": end_day,
        }
        response = await self._post_form(
            GET_PAYMENT_LIST,
            payload,
            sign_field="custCode",
        )
        return _require_list(response.get("data"), "Payment list")

    async def _post_form(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        sign_field: str | None,
    ) -> dict[str, Any]:
        signed_payload = self._signer.sign_v2(payload, sign_field)
        return await self._request_json(
            "POST",
            path,
            headers=self._auth_headers("application/x-www-form-urlencoded"),
            data=signed_payload,
        )

    async def _post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        sign_field: str | None,
    ) -> dict[str, Any]:
        signed_payload = self._signer.sign_v2(payload, sign_field)
        return await self._request_json(
            "POST",
            path,
            headers=self._auth_headers("application/json"),
            json_payload=signed_payload,
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        json_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a request and parse the JSON response."""
        url = f"{BASE_URL}{path}"
        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                params=params,
                data=data,
                json=json_payload,
                timeout=30,
            ) as response:
                text = await response.text()
        except (ClientError, TimeoutError) as err:
            raise ZhongranApiError(f"Request to {path} failed: {err}") from err

        _LOGGER.debug("Zhongran response %s: %s", path, text)

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as err:
            raise ZhongranApiError(f"Invalid JSON from {path}: {text[:240]}") from err

        if not self._response_ok(payload):
            message = str(payload.get("message") or payload.get("msg") or "Request failed")
            if _looks_like_auth_error(message):
                raise ZhongranAuthError(message)
            raise ZhongranApiError(f"{path} failed: {message}")

        return payload

    def _auth_headers(self, content_type: str) -> dict[str, str]:
        credentials = self._require_credentials()
        return {
            "accessToken": credentials.access_token,
            "x-mas-app-info": credentials.x_mas_app_info,
            "accessFrom": ACCESS_FROM,
            "userId": credentials.user_id,
            "Content-Type": content_type,
            "platform": PLATFORM,
            "Referer": REFERER,
            "Accept": "*/*",
        }

    def _public_headers(self, content_type: str) -> dict[str, str]:
        return {
            "accessFrom": ACCESS_FROM,
            "Content-Type": content_type,
            "platform": PLATFORM,
            "Referer": REFERER,
            "Accept": "*/*",
        }

    def _response_ok(self, payload: Mapping[str, Any]) -> bool:
        status = payload.get("status")
        if status is not None:
            return status in (1, "1", True, "true")

        success = payload.get("success")
        if success is not None:
            return success in (1, "1", True, "true")

        return True

    def _select_account(self, accounts: list[dict[str, Any]]) -> dict[str, Any]:
        if not accounts:
            raise ZhongranApiError("No bound gas accounts were returned by Zhongran.")

        credentials = self._require_credentials()
        for account in accounts:
            if account.get("custCode") == credentials.cust_code:
                return account

        return {
            "custCode": credentials.cust_code,
            "custName": credentials.cust_name,
            **accounts[0],
        }

    def _require_credentials(self) -> ZhongranCredentials:
        if self._credentials is None:
            raise ZhongranApiError("This request requires an authenticated Zhongran session.")
        return self._credentials


def build_image_code_url(mobile: str) -> str:
    """Return the image captcha URL used by Zhongran."""
    return f"{BASE_URL}{IMAGE_CODE_PATH}?{urlencode({'flag': mobile, 'tn': _timestamp_ms()})}"


def _date_window(days: int) -> tuple[str, str]:
    end = datetime.now()
    start = end - timedelta(days=days)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _month_window(months: int) -> tuple[str, str]:
    today = datetime.now()
    year = today.year
    month = today.month - months
    while month <= 0:
        month += 12
        year -= 1
    return f"{year:04d}{month:02d}", f"{today.year:04d}{today.month:02d}"


def _timestamp_ms() -> int:
    return int(datetime.now(tz=UTC).timestamp() * 1000)


def _resolve_signature_source(
    payload: Mapping[str, Any],
    preferred_field: str | None,
) -> str | None:
    candidate_fields = [
        preferred_field,
        "autoSrvId",
        "custCode",
        "compcode",
        "compCode",
        "userId",
        "mobile",
    ]

    for field in candidate_fields:
        if not field:
            continue
        value = payload.get(field)
        if value not in (None, ""):
            return str(value)

    return None


def _md5hex(raw: str) -> str:
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _looks_like_auth_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in (
            "login",
            "token",
            "expired",
            "auth",
            "\u672a\u767b\u5f55",
            "\u5931\u6548",
            "\u8ba4\u8bc1",
            "\u975e\u6cd5\u7528\u6237",
            "\u767b\u5f55\u8fc7\u671f",
        )
    )


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ZhongranApiError(f"{label} was not a JSON object: {value!r}")
    return value


def _require_list(value: Any, label: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ZhongranApiError(f"{label} was not a JSON list: {value!r}")
    return [item for item in value if isinstance(item, dict)]
