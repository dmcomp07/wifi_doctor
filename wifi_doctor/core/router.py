import base64
import hashlib
import http.cookiejar
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple, Union

from wifi_doctor.utils.shell import run_cmd

class RouterManager:
    BRAND_HINTS: Dict[str, List[str]] = {
        "tplink":   ["tp-link", "tplink", "archer", "deco", "tl-wr", "tl-wdr"],
        "asus":     ["asus", "asuswrt", "rt-ax", "rt-ac", "rt-n", "merlin"],
        "netgear":  ["netgear", "nighthawk", "orbi"],
        "dlink":    ["d-link", "dlink", "dir-"],
        "linksys":  ["linksys", "velop"],
        "xiaomi":   ["xiaomi", "miwifi"],
        "openwrt":  ["openwrt", "luci"],
    }
    MANUAL_STEPS: Dict[str, str] = {
        "tplink":  "Log in → Wireless → 2.4 GHz / 5 GHz → Channel → select → Save",
        "asus":    "Log in → Wireless → General → Control Channel → select → Apply",
        "netgear": "Log in → ADVANCED → Wireless Settings → Channel → Manual → Apply",
        "dlink":   "Log in → Setup → Wireless Settings → Wireless Channel → Save",
        "linksys": "Log in → Wireless → Basic Wireless Settings → Standard Channel → Save",
        "xiaomi":  "Log in → Basic Settings → Wireless LAN Settings → Channel → Save",
        "openwrt": "Log in → Network → Wireless → Edit → Channel → Save & Apply",
    }

    def __init__(self, ip: str = "", username: str = "admin", password: str = "") -> None:
        self.ip = ip.strip()
        self.username = username
        self.password = password
        self.brand: Optional[str] = None
        self._stok: Optional[str] = None    # TP-Link session token
        self._token: Optional[str] = None   # ASUS token
        _ctx = ssl.create_default_context()
        _ctx.check_hostname = False
        _ctx.verify_mode = ssl.CERT_NONE
        _jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(_jar),
            urllib.request.HTTPSHandler(context=_ctx),
        )

    def _req(self, path: str, data: Optional[Union[str, bytes]] = None, extra_headers: Optional[Dict[str, str]] = None, timeout: int = 8) -> Tuple[int, str]:
        payload = data.encode() if isinstance(data, str) else data
        for scheme in ("http", "https"):
            url = f"{scheme}://{self.ip}{path}"
            req = urllib.request.Request(url, data=payload)
            req.add_header("Authorization",
                           "Basic " + base64.b64encode(
                               f"{self.username}:{self.password}".encode()).decode())
            if extra_headers:
                for k, v in extra_headers.items():
                    req.add_header(k, v)
            try:
                with self._opener.open(req, timeout=timeout) as resp:
                    return resp.status, resp.read().decode("utf-8", errors="ignore")
            except urllib.error.HTTPError as e:
                try:
                    return e.code, e.read().decode("utf-8", errors="ignore")
                except Exception:
                    return e.code, ""
            except Exception:
                if scheme == "http":
                    continue   # try https
                return 0, f"Cannot reach {self.ip}"
        return 0, f"Cannot reach {self.ip}"

    def _post_json(self, path: str, obj: Any, timeout: int = 8) -> Tuple[int, str]:
        return self._req(path, data=json.dumps(obj),
                         extra_headers={"Content-Type": "application/json"},
                         timeout=timeout)

    def _post_form(self, path: str, fields: Dict[str, str], timeout: int = 8) -> Tuple[int, str]:
        return self._req(path, data=urllib.parse.urlencode(fields),
                         extra_headers={"Content-Type": "application/x-www-form-urlencoded"},
                         timeout=timeout)

    # ---------------------------------------------------------------- brand detection
    def detect_brand(self) -> Tuple[Optional[str], str]:
        status, body = self._req("/", timeout=6)
        if status == 0:
            return None, f"Cannot reach {self.ip}: {body}"
        b = body.lower()
        for brand, keywords in self.BRAND_HINTS.items():
            if any(kw in b for kw in keywords):
                self.brand = brand
                return brand, f"Detected {brand.upper()} router (HTTP {status})"
        self.brand = "generic"
        return "generic", f"Brand not recognised — generic mode (HTTP {status})"

    # ---------------------------------------------------------------- TP-Link
    def _tplink_login(self) -> Tuple[bool, str]:
        pw_hash = hashlib.md5(self.password.encode()).hexdigest().upper()
        _, body = self._post_json("/cgi-bin/luci/", {
            "method": "do",
            "login": {"username": self.username, "encrypt_type": "3", "password": pw_hash},
        })
        try:
            d = json.loads(body)
            stok = d.get("stok") or (d.get("data") or {}).get("stok")
            if stok:
                self._stok = stok
                return True, "TP-Link login OK"
        except Exception:
            pass
        # Legacy TP-Link (WR/WDR series)
        status, body = self._req(
            f"/userRpm/LoginRpm.htm?Save=Save&psd={urllib.parse.quote(self.password)}")
        if status == 200 and "userRpm" in body:
            self._stok = "legacy"
            return True, "TP-Link login OK (legacy firmware)"
        return False, "TP-Link authentication failed — check credentials"

    def _tplink_get_channels(self) -> Dict[str, Any]:
        if not self._stok or self._stok == "legacy":
            return {}
        results = {}
        for band in ("2g", "5g"):
            _, body = self._post_json(
                f"/cgi-bin/luci/;stok={self._stok}/",
                {"method": "get", "wireless": {band: None}},
            )
            try:
                cfg = json.loads(body).get("wireless", {}).get(band, {})
                results[band] = cfg
            except Exception:
                pass
        return results

    def _tplink_set_channel(self, band: str, channel: Union[int, str]) -> Tuple[bool, str]:
        if not self._stok or self._stok == "legacy":
            return False, "No active session — re-connect first"
        _, body = self._post_json(
            f"/cgi-bin/luci/;stok={self._stok}/",
            {"method": "set", "wireless": {band: {"channel": str(channel)}}},
        )
        try:
            d = json.loads(body)
            if d.get("error_code") == 0:
                return True, f"Channel {channel} set on {band}"
            return False, f"error_code {d.get('error_code')} — {body[:120]}"
        except Exception:
            return False, body[:200]

    # ---------------------------------------------------------------- ASUS
    def _asus_login(self) -> Tuple[bool, str]:
        creds_b64 = base64.b64encode(
            f"{self.username}:{self.password}".encode()).decode()
        status, body = self._post_form(
            "/login.cgi", {"login_authorization": creds_b64})
        if status == 200:
            try:
                self._token = json.loads(body).get("asus_token", "")
            except Exception:
                pass
            return True, "ASUS login OK"
        return False, f"ASUS authentication failed (HTTP {status})"

    def _asus_get_channels(self) -> Dict[str, Any]:
        _, body = self._req(
            "/nvram.cgi?nvram_get=wl0_channel&nvram_get=wl1_channel", timeout=6)
        try:
            return json.loads(body)
        except Exception:
            return {}

    def _asus_set_channel(self, iface: str, channel: Union[int, str]) -> Tuple[bool, str]:
        status, body = self._post_form(
            "/apply.cgi",
            {"action_mode": "apply", "action_script": "restart_wireless",
             f"wl{iface}_channel": str(channel)},
        )
        return status == 200, body[:200]

    # ---------------------------------------------------------------- public API
    def login(self) -> Tuple[bool, str]:
        if not self.brand:
            self.detect_brand()
        if self.brand == "tplink":
            return self._tplink_login()
        if self.brand == "asus":
            return self._asus_login()
        status, _ = self._req("/")
        ok = status not in (0, 401, 403)
        return ok, ("Connected" if ok else f"HTTP {status} — credentials may be wrong")

    def get_current_channels(self) -> Dict[str, Any]:
        if self.brand == "tplink":
            return self._tplink_get_channels()
        if self.brand == "asus":
            return self._asus_get_channels()
        return {}

    def apply_channel(self, band: str, channel: Union[int, str]) -> Tuple[bool, str]:
        if self.brand == "tplink":
            return self._tplink_set_channel(band, channel)
        if self.brand == "asus":
            iface = "1" if band == "5g" else "0"
            return self._asus_set_channel(iface, channel)
        return False, (
            f"Auto-apply not supported for '{self.brand}' routers.\n"
            f"Please set channel {channel} manually (see instructions below)."
        )

    def manual_instructions(self, ch_2g: Optional[Union[int, str]] = None, ch_5g: Optional[Union[int, str]] = None) -> str:
        step = self.MANUAL_STEPS.get(
            self.brand or "generic",
            "Open router admin panel → find Wireless settings → change Channel",
        )
        lines = [
            f"Router:  {self.brand or 'unknown'} at http://{self.ip}/",
            f"Steps:   {step}",
            "",
        ]
        if ch_2g:
            lines.append(f"  Recommended 2.4 GHz channel : {ch_2g}")
        if ch_5g:
            lines.append(f"  Recommended 5 GHz channel   : {ch_5g}")
        return "\n".join(lines)
