import csv
import json
import os
import platform
import re
import socket
import time
import urllib.request
import zipfile
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from wifi_doctor.utils.shell import run_cmd, powershell
from wifi_doctor.core.models import Finding
from wifi_doctor.core.constants import MANUFACTURER_DRIVER_URLS, OOKLA_URL
from wifi_doctor.utils.logger import logger

class WifiDoctor:
    def __init__(self) -> None:
        self.snapshot: Dict[str, Any] = {}
        self.last_test: Dict[str, Any] = {}
        logger.debug("WifiDoctor initialized")

    # ------------------------------------------------------------------ collect
    def get_wifi_adapters(self) -> List[Dict[str, str]]:
        """Returns a list of all Wi-Fi adapters using Get-NetAdapter."""
        logger.info("Fetching Wi-Fi adapters…")
        ps = "Get-NetAdapter | Where-Object {$_.ConnectorType -eq '802.11'} | Select-Object Name, InterfaceDescription, Status, LinkSpeed | ConvertTo-Json -Compress"
        rc, out, err = powershell(ps)
        if rc == 0 and out:
            try:
                data = json.loads(out)
                if isinstance(data, dict):
                    data = [data]
                logger.debug(f"Found {len(data)} adapter(s)")
                return data
            except Exception as e:
                logger.error(f"Error parsing adapter list: {e}")
        else:
            logger.warning(f"Get-NetAdapter failed: {err}")
        return []

    def collect(self, adapter_name: Optional[str] = None) -> Dict[str, Any]:
        logger.info(f"Collecting data for adapter: {adapter_name or 'Auto'}")
        data: Dict[str, Any] = {
            "platform": platform.platform(),
            "hostname": socket.gethostname(),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "wifi_interfaces": [],
            "profiles": [],
            "ipconfig": "",
            "ping_gateway": "",
            "ping_gateway_v6": "",
            "driver_info": {},
            "advanced_props": {},
        }

        # Get adapter list if not provided
        adapters = self.get_wifi_adapters()
        if not adapter_name and adapters:
            adapter_name = adapters[0]["Name"]

        if not adapter_name:
            logger.warning("No Wi-Fi adapter detected during collect")
            return data

        # Use netsh for Wi-Fi specific details (still needed for signal/channel)
        rc, out, err = run_cmd(["netsh", "wlan", "show", "interfaces", f"name={adapter_name}"])
        data["wlan_interfaces_raw"] = out or err
        data["wifi_interfaces"] = self.parse_interfaces(out)

        # Profiles
        rc, out, err = run_cmd(["netsh", "wlan", "show", "profiles"])
        data["profiles_raw"] = out or err
        data["profiles"] = self.parse_profiles(out)

        # IP Config (Robust version using PowerShell)
        ps_ip = f"Get-NetIPConfiguration -InterfaceAlias '{adapter_name}' | Select-Object IPv4Address, IPv4DefaultGateway, IPv6DefaultGateway | ConvertTo-Json -Compress"
        rc_ip, out_ip, err_ip = powershell(ps_ip)
        if rc_ip == 0 and out_ip:
            try:
                ip_info = json.loads(out_ip)
                if isinstance(ip_info, list): ip_info = ip_info[0]
                
                gw_v4 = ip_info.get("IPv4DefaultGateway", {}).get("NextHop")
                if gw_v4:
                    data["default_gateway"] = gw_v4
                    logger.info(f"Pinging IPv4 gateway: {gw_v4}")
                    data["ping_gateway"] = self.ping_target(gw_v4, count=8)
                
                gw_v6 = ip_info.get("IPv6DefaultGateway")
                if isinstance(gw_v6, list): gw_v6 = gw_v6[0]
                if isinstance(gw_v6, dict): gw_v6 = gw_v6.get("NextHop")
                
                if gw_v6:
                    data["default_gateway_v6"] = gw_v6
                    logger.info(f"Pinging IPv6 gateway: {gw_v6}")
                    data["ping_gateway_v6"] = self.ping_target(gw_v6, count=8)
                    
            except Exception as e:
                logger.error(f"Error parsing IP config: {e}")

        data["driver_info"] = self.get_driver_info(adapter_name)
        data["advanced_props"] = self.get_advanced_props(adapter_name)

        self.snapshot = data
        return data

    # ------------------------------------------------------------------ parse
    def parse_interfaces(self, text: str) -> List[Dict[str, str]]:
        interfaces, current = [], {}
        for line in text.splitlines():
            if ":" in line:
                k_raw, v = line.split(":", 1)
                k = k_raw.strip()
                v = v.strip()
                # Use regex to identify keys by common patterns or position if possible, 
                # but for now, we'll keep the lower() mapping and maybe add multilingual support later.
                # However, many users have English Windows for tech tasks.
                current[k.lower()] = v
                if k.lower() == "name" and current:
                    # This logic was slightly flawed for single interface output from netsh name=X
                    pass 
        
        if current:
            interfaces.append(current)
            
        cleaned = []
        for i in interfaces:
            # Try to be more robust with keys by searching for value patterns if keys don't match
            def find_val(patterns):
                for k, v in i.items():
                    if any(p in k for p in patterns): return v
                return ""

            cleaned.append({
                "name": find_val(["name", "nombre", "nom", "name"]),
                "description": find_val(["desc", "beschreibung"]),
                "state": find_val(["state", "status", "estado", "état"]),
                "ssid": find_val(["ssid"]),
                "radio type": find_val(["radio", "funktyp"]),
                "band": self.derive_band(find_val(["radio"]), find_val(["channel", "kanal"])),
                "channel": find_val(["channel", "kanal"]),
                "receive rate (mbps)": find_val(["receive", "empfang"]),
                "transmit rate (mbps)": find_val(["transmit", "übertragung"]),
                "signal": find_val(["signal"]),
                "profile": find_val(["profile", "profil"]),
                "authentication": find_val(["auth"]),
                "cipher": find_val(["cipher", "verschlüsselung"]),
                "bssid": find_val(["bssid"]),
            })
        return cleaned

    def derive_band(self, radio: str, channel: str) -> str:
        try:
            ch_match = re.findall(r"\d+", str(channel))
            if ch_match:
                ch = int(ch_match[0])
                if 1 <= ch <= 14:
                    return "2.4 GHz"
                if 36 <= ch <= 177:
                    return "5 GHz / 6 GHz"
        except Exception:
            pass
        r = radio.lower()
        if "ax" in r or "ac" in r:
            return "Likely 5/6 GHz"
        if "a" in r and "b" not in r:
            return "Likely 5 GHz"
        return "2.4/5 GHz capable"

    def parse_profiles(self, text: str) -> List[str]:
        return re.findall(r"All User Profile\s*:\s*(.*)", text)

    def extract_default_gateway(self, text: str) -> Optional[str]:
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            if "Default Gateway" in line:
                candidate = line.split(":", 1)[-1].strip()
                m = re.search(r"(\d+\.\d+\.\d+\.\d+)", candidate)
                if m:
                    return m.group(1)
                for j in range(idx + 1, min(idx + 3, len(lines))):
                    m = re.search(r"(\d+\.\d+\.\d+\.\d+)", lines[j])
                    if m:
                        return m.group(1)
        return None

    # ------------------------------------------------------------------ driver / props
    def get_driver_info(self, adapter_name: str) -> Dict[str, Any]:
        ps = (
            f'Get-NetAdapter -Name "{adapter_name}" | '
            "Select-Object Name,InterfaceDescription,Status,LinkSpeed,"
            "DriverVersion,DriverDate | ConvertTo-Json -Compress"
        )
        rc, out, err = powershell(ps)
        if rc == 0 and out:
            try:
                return json.loads(out)
            except Exception:
                return {"raw": out}
        return {"error": err}

    def get_advanced_props(self, adapter_name: str) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
        ps = (
            f'Get-NetAdapterAdvancedProperty -Name "{adapter_name}" | '
            "Select-Object DisplayName,DisplayValue,RegistryKeyword,RegistryValue | "
            "ConvertTo-Json -Compress"
        )
        rc, out, err = powershell(ps)
        if rc == 0 and out:
            try:
                return json.loads(out)
            except Exception:
                return {"raw": out}
        return {"error": err}

    # ------------------------------------------------------------------ analyze
    def analyze(self, data: Dict[str, Any]) -> List[Finding]:
        findings = []
        if not data.get("wifi_interfaces"):
            findings.append(Finding(
                "No active Wi-Fi interface found", "high",
                "The app could not detect a connected wireless interface.",
                "Connect to Wi-Fi and rerun diagnostics.",
            ))
            return findings

        iface = data["wifi_interfaces"][0]
        signal = iface.get("signal", "")
        sig_match = re.findall(r"\d+", signal)
        sig_num = int(sig_match[0]) if sig_match else None

        if sig_num is not None:
            if sig_num < 40:
                findings.append(Finding(
                    "Very weak signal", "high",
                    f"Signal is {signal}. This will cause frequent drops.",
                    "Move much closer to the router or use a wired connection.",
                ))
            elif sig_num < 60:
                findings.append(Finding(
                    "Weak signal quality", "medium",
                    f"Signal is {signal}.",
                    "Move closer to the router or reduce obstructions.",
                ))

        if "5" in iface.get("band", "") and sig_num is not None and sig_num < 70:
            findings.append(Finding(
                "Marginal 5 GHz range", "high",
                "5 GHz has shorter range and weaker wall penetration than 2.4 GHz.",
                "Test near the router or switch to 2.4 GHz for better coverage.",
            ))

        props = data.get("advanced_props", [])
        if isinstance(props, dict):
            props = [props] # Normalize to list if single object
        
        prop_map = {
            str(p.get("DisplayName", "")).lower(): str(p.get("DisplayValue", ""))
            for p in props if isinstance(p, dict)
        }

        roam = next((v for k, v in prop_map.items() if "roaming" in k), None)
        if roam and roam.lower() not in ["lowest", "low", "1", "2"]:
            findings.append(Finding(
                "Roaming aggressiveness too high", "medium",
                f"Current roaming setting: '{roam}'.",
                "Set Roaming Aggressiveness to Low or Lowest for a single-router setup.",
            ))

        pref_band = next((v for k, v in prop_map.items() if "preferred band" in k), None)
        if pref_band and "5" not in pref_band:
            findings.append(Finding(
                "Preferred band not optimized", "medium",
                f"Preferred band shows '{pref_band}'.",
                "Set Preferred Band to 'Prefer 5 GHz' in adapter properties.",
            ))

        power_sav = next((v for k, v in prop_map.items() if "power" in k and "sav" in k), None)
        if power_sav and power_sav not in ["0", "Disabled"]:
            findings.append(Finding(
                "Wi-Fi power saving enabled", "medium",
                f"Power saving mode is active ('{power_sav}'). This causes random drops.",
                "Disable power saving in adapter Advanced Properties and Power Management.",
            ))

        ping = data.get("ping_gateway", {})
        if isinstance(ping, dict):
            loss = ping.get("packet_loss_percent")
            avg = ping.get("avg_ms")
            if loss is not None and loss > 10:
                findings.append(Finding(
                    f"Gateway packet loss: {loss}%", "high",
                    f"Losing {loss}% of packets to the gateway ({ping.get('target', '')}).",
                    "Check Wi-Fi signal, change channel, or reboot the router.",
                ))
            if avg is not None and avg > 50:
                findings.append(Finding(
                    f"High gateway latency: {avg} ms", "medium",
                    "Gateway response time is high, indicating a congested or noisy channel.",
                    "Try a less congested channel or move closer to the router.",
                ))

        drv = data.get("driver_info", {})
        drv_date_str = drv.get("DriverDate", "") if isinstance(drv, dict) else ""
        drv_ver = drv.get("DriverVersion", "") if isinstance(drv, dict) else ""
        m_ts = re.search(r"/Date\((\d+)\)/", str(drv_date_str))
        if m_ts:
            age_days = (time.time() - int(m_ts.group(1)) / 1000) / 86400
            date_label = time.strftime("%Y-%m-%d", time.localtime(int(m_ts.group(1)) / 1000))
            detail = f"Version {drv_ver}, dated {date_label} ({int(age_days / 365)} yr {int((age_days % 365) / 30)} mo old)."
            if age_days > 730:
                findings.append(Finding(
                    "Outdated Wi-Fi driver", "medium",
                    detail,
                    "Use 'Driver Update' in the Fixes tab or visit the manufacturer's site.",
                ))
            else:
                findings.append(Finding(
                    "Driver is reasonably current", "low",
                    detail,
                    "Update if instability persists after other fixes.",
                ))
        else:
            findings.append(Finding(
                "Driver version unknown", "low",
                "Could not read driver date. Outdated drivers are a common cause of instability.",
                "Use 'Driver Update' in the Fixes tab or visit the manufacturer's site.",
            ))

        return findings

    # ------------------------------------------------------------------ fixes
    def _get_backup_path(self) -> str:
        base = os.path.join(os.path.expanduser("~"), ".wifi_doctor_tools")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "adapter_backup.json")

    def _save_backup(self, adapter_name: str, key: str, value: str) -> None:
        path = self._get_backup_path()
        backup = {}
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    backup = json.load(f)
            except Exception:
                pass
        
        adapter_backup = backup.setdefault(adapter_name, {})
        # Only backup if not already backed up to preserve "original" state
        if key not in adapter_backup:
            adapter_backup[key] = value
            with open(path, "w") as f:
                json.dump(backup, f, indent=2)

    def apply_fixes(self, adapter_name: Optional[str] = None, progress_cb: Optional[Callable[[str], None]] = None) -> List[Dict[str, Any]]:
        results = []

        def step(label: str, fn: Callable[[], Tuple[bool, str]]) -> None:
            if progress_cb:
                progress_cb(label)
            ok, msg = fn()
            results.append({"step": label, "ok": ok, "msg": msg})

        step("Set High Performance power plan", self._set_high_perf_plan)
        step("Disable adapter power management (wake)", lambda: self._disable_adapter_wake(adapter_name))
        step("Set adapter power saving off", lambda: self._set_adapter_power_saving(adapter_name, off=True))
        step("Set roaming aggressiveness Low", lambda: self._set_adv_prop(adapter_name, "RoamAggressiveness", "2", backup=True))
        step("Set preferred band to 5 GHz", lambda: self._set_adv_prop(adapter_name, "PreferredBand", "3", backup=True))
        step("Flush DNS", self._flush_dns)
        return results

    def rollback_fixes(self, adapter_name: str, progress_cb: Optional[Callable[[str], None]] = None) -> List[Dict[str, Any]]:
        results = []
        path = self._get_backup_path()
        if not os.path.exists(path):
            return [{"step": "Rollback", "ok": False, "msg": "No backup found"}]

        try:
            with open(path, "r") as f:
                backup = json.load(f)
        except Exception as e:
            return [{"step": "Rollback", "ok": False, "msg": f"Error reading backup: {e}"}]

        adapter_backup = backup.get(adapter_name, {})
        if not adapter_backup:
            return [{"step": "Rollback", "ok": False, "msg": f"No backup for {adapter_name}"}]

        for key, value in adapter_backup.items():
            if progress_cb:
                progress_cb(f"Restoring {key} to {value}…")
            ok, msg = self._set_adv_prop(adapter_name, key, value, backup=False)
            results.append({"step": f"Restore {key}", "ok": ok, "msg": msg})

        # Power management and power plan are harder to "undo" without state tracking
        # but we can at least try to re-enable power saving if we assumed it was on.
        # For now, we focus on registry properties.

        return results

    def _set_high_perf_plan(self) -> Tuple[bool, str]:
        rc, out, err = powershell("powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c")
        if rc != 0:
            rc, out, err = powershell("powercfg /setactive SCHEME_MIN")
        return rc == 0, out or err

    def _disable_adapter_wake(self, adapter_name: Optional[str]) -> Tuple[bool, str]:
        if not adapter_name:
            return False, "No adapter name"
        ps = f'Disable-NetAdapterPowerManagement -Name "{adapter_name}" -WakeOnMagicPacket -WakeOnPattern -ErrorAction SilentlyContinue'
        powershell(ps)
        return True, "Done"

    def _set_adapter_power_saving(self, adapter_name: Optional[str], off: bool = True) -> Tuple[bool, str]:
        if not adapter_name:
            return False, "No adapter name"
        val = "0" if off else "1"
        
        # Backup before setting
        if off:
            # We assume selective suspend was enabled (1) if not known
            # Real robust way is to query first
            ps_query = f'Get-NetAdapterAdvancedProperty -Name "{adapter_name}" -RegistryKeyword "SelectiveSuspend" | Select-Object -ExpandProperty DisplayValue'
            _, out, _ = powershell(ps_query)
            if out: self._save_backup(adapter_name, "SelectiveSuspend", out)

        ps = f'Set-NetAdapterAdvancedProperty -Name "{adapter_name}" -RegistryKeyword "PowerSavingMode" -RegistryValue {val} -ErrorAction SilentlyContinue'
        powershell(ps)
        ps2 = f'Set-NetAdapterAdvancedProperty -Name "{adapter_name}" -RegistryKeyword "SelectiveSuspend" -RegistryValue {val} -ErrorAction SilentlyContinue'
        powershell(ps2)
        return True, "Done"

    def _set_adv_prop(self, adapter_name: Optional[str], keyword: str, value: str, backup: bool = False) -> Tuple[bool, str]:
        if not adapter_name:
            return False, "No adapter name"
        
        if backup:
            ps_query = f'Get-NetAdapterAdvancedProperty -Name "{adapter_name}" -RegistryKeyword "{keyword}" | Select-Object -ExpandProperty RegistryValue'
            rc, out, _ = powershell(ps_query)
            if rc == 0 and out:
                # out is the raw registry value
                self._save_backup(adapter_name, keyword, out.strip())

        ps = f'Set-NetAdapterAdvancedProperty -Name "{adapter_name}" -RegistryKeyword "{keyword}" -RegistryValue {value} -ErrorAction SilentlyContinue'
        _, out, _ = powershell(ps)
        return True, out or "Done"

    def _flush_dns(self) -> Tuple[bool, str]:
        rc, out, err = run_cmd(["ipconfig", "/flushdns"])
        return rc == 0, out or err

    def network_reset_commands(self, progress_cb: Optional[Callable[[str], None]] = None) -> List[Dict[str, Any]]:
        cmds: List[Tuple[List[str], str]] = [
            (["netsh", "winsock", "reset"], "Winsock reset"),
            (["netsh", "int", "ip", "reset"], "IP stack reset"),
            (["ipconfig", "/flushdns"], "Flush DNS"),
            (["ipconfig", "/release"], "Release IP"),
            (["ipconfig", "/renew"], "Renew IP"),
        ]
        results = []
        for c, label in cmds:
            if progress_cb:
                progress_cb(label)
            results.append({"cmd": " ".join(c), "result": run_cmd(c, timeout=90)})
        return results

    # ------------------------------------------------------------------ ping
    def ping_target(self, target: str, count: int = 20) -> Dict[str, Any]:
        rc, out, err = run_cmd(["ping", "-n", str(count), target], timeout=max(30, count * 2))
        txt = out or err
        loss, avg = None, None
        m1 = re.search(r"Lost = \d+ \((\d+)% loss\)", txt)
        if m1:
            loss = int(m1.group(1))
        m2 = re.search(r"Average = (\d+)ms", txt)
        if m2:
            avg = int(m2.group(1))
        return {"raw": txt, "packet_loss_percent": loss, "avg_ms": avg, "target": target}

    # ------------------------------------------------------------------ congestion scan
    def scan_networks(self) -> List[Dict[str, Any]]:
        rc, out, err = run_cmd(["netsh", "wlan", "show", "networks", "mode=bssid"], timeout=15)
        text = out or err
        networks: List[Dict[str, Any]] = []
        current: Dict[str, Any] = {}
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("SSID") and "BSSID" not in line:
                if current:
                    networks.append(current)
                current = {}
                parts = line.split(":", 1)
                if len(parts) == 2:
                    current["ssid"] = parts[1].strip()
            elif ":" in line:
                k, v = line.split(":", 1)
                k = k.strip().lower()
                v = v.strip()
                if k == "channel":
                    current["channel"] = v
                elif k == "signal":
                    current["signal"] = v
                elif k == "radio type":
                    current["radio"] = v
                elif k == "authentication":
                    current["auth"] = v
                elif k.startswith("bssid"):
                    current.setdefault("bssids", []).append(v)
        if current:
            networks.append(current)
        return networks

    def recommend_channel(self, networks: List[Dict[str, Any]], current_channel: Optional[str] = None) -> Tuple[int, Dict[int, int]]:
        channel_load: Dict[int, int] = {}
        for net in networks:
            ch = net.get("channel", "")
            try:
                ch_int = int(str(ch))
                channel_load[ch_int] = channel_load.get(ch_int, 0) + 1
            except ValueError:
                pass

        is_5ghz = False
        if current_channel:
            try:
                is_5ghz = int(str(current_channel)) > 14
            except ValueError:
                pass

        if is_5ghz:
            candidates = [36, 40, 44, 48, 149, 153, 157, 161]
        else:
            candidates = [1, 6, 11]

        best = min(candidates, key=lambda c: channel_load.get(c, 0))
        return best, channel_load

    # ------------------------------------------------------------------ speedtest
    def ensure_speedtest_cli(self) -> str:
        base = os.path.join(os.path.expanduser("~"), ".wifi_doctor_tools")
        os.makedirs(base, exist_ok=True)
        exe = os.path.join(base, "speedtest.exe")
        if os.path.exists(exe):
            return exe
        zip_path = os.path.join(base, "ookla-speedtest.zip")
        urllib.request.urlretrieve(OOKLA_URL, zip_path)
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                if name.lower().endswith("speedtest.exe"):
                    zf.extract(name, base)
                    src = os.path.join(base, name)
                    if src != exe:
                        os.replace(src, exe)
                    return exe
        raise RuntimeError("speedtest.exe not found in downloaded archive")

    def _python_speedtest(self) -> Dict[str, Any]:
        """Fallback: measure download speed and ping using plain HTTP (no CLI needed)."""
        result: Dict[str, Any] = {"server": "Cloudflare (fallback)", "isp": None, "upload_mbps": None, "fallback": True}
        ping = self.ping_target("1.1.1.1", count=5)
        result["ping_ms"] = ping.get("avg_ms")
        result["packet_loss"] = ping.get("packet_loss_percent", 0)
        try:
            url = "https://speed.cloudflare.com/__down?bytes=25000000"
            start = time.time()
            with urllib.request.urlopen(url, timeout=40) as resp:
                data = resp.read()
            elapsed = time.time() - start
            result["download_mbps"] = round(len(data) * 8 / (elapsed * 1_000_000), 2)
        except Exception as e:
            result["download_mbps"] = None
            result["error"] = f"Fallback download failed: {e}"
        return result

    def run_speedtest(self) -> Dict[str, Any]:
        try:
            exe = self.ensure_speedtest_cli()
        except Exception:
            return self._python_speedtest()

        rc, out, err = run_cmd([exe, "--accept-license", "--accept-gdpr", "-f", "json"], timeout=180)
        if rc != 0:
            raw = err or out
            try:
                msg = json.loads(raw).get("message") or json.loads(raw).get("error") or raw
            except Exception:
                msg = raw
            if any(w in str(msg).lower() for w in ("socket", "connect", "network", "reach", "interface")):
                result = self._python_speedtest()
                result["cli_error"] = msg
                return result
            return {"error": msg, "raw": raw}
        try:
            data = json.loads(out)
            return {
                "ping_ms": data.get("ping", {}).get("latency"),
                "download_mbps": round(data.get("download", {}).get("bandwidth", 0) * 8 / 1_000_000, 2),
                "upload_mbps": round(data.get("upload", {}).get("bandwidth", 0) * 8 / 1_000_000, 2),
                "server": data.get("server", {}).get("name"),
                "isp": data.get("isp"),
                "packet_loss": data.get("packetLoss"),
                "raw": data,
            }
        except Exception:
            return {"error": "Could not parse speedtest output", "raw": out}

    # ------------------------------------------------------------------ post-fix suite
    def run_post_fix_test_suite(self, progress_cb: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        results: Dict[str, Any] = {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}
        if progress_cb:
            progress_cb("Collecting Wi-Fi snapshot…")
        data = self.collect()
        results["snapshot"] = data
        gw = data.get("default_gateway")
        if gw:
            if progress_cb:
                progress_cb(f"Pinging gateway {gw} (30 packets)…")
            results["gateway_ping"] = self.ping_target(gw, count=30)
        if progress_cb:
            progress_cb("Pinging 8.8.8.8 (20 packets)…")
        results["internet_ping"] = self.ping_target("8.8.8.8", count=20)
        if progress_cb:
            progress_cb("Running speed test (may take 30–60 s)…")
        results["speedtest"] = self.run_speedtest()
        self.last_test = results
        return results

    def forget_profile(self, profile: str) -> Tuple[int, str, str]:
        return run_cmd(["netsh", "wlan", "delete", "profile", f"name={profile}"])

    # ---------------------------------------------------------------- driver update
    def get_manufacturer_driver_info(self, adapter_description: Optional[str]) -> Tuple[str, Optional[str]]:
        desc = (adapter_description or "").lower()
        for brand, url in MANUFACTURER_DRIVER_URLS.items():
            if brand in desc:
                return brand, url
        return "unknown", None

    def check_driver_updates(self) -> Dict[str, Any]:
        ps = r"""
try {
    $Session = New-Object -ComObject Microsoft.Update.Session
    $Searcher = $Session.CreateUpdateSearcher()
    $Result = $Searcher.Search("IsInstalled=0 and Type='Driver'")
    $WifiUpdates = @($Result.Updates | Where-Object {
        $_.Title -match 'Wi-?Fi|Wireless|WLAN|802\.11|Network Adapter'
    })
    if ($WifiUpdates.Count -eq 0) {
        Write-Output '{"status":"none","updates":[]}'
    } else {
        $list = $WifiUpdates | Select-Object Title,@{n='KB';e={$_.KBArticleIDs -join ','}} |
                ConvertTo-Json -Compress
        Write-Output "{""status"":""found"",""updates"":$list}"
    }
} catch {
    Write-Output "{""status"":""error"",""error"":""$($_.Exception.Message)""}"
}
"""
        _, out, err = powershell(ps, timeout=120)
        for line in out.strip().splitlines():
            try:
                return json.loads(line)
            except Exception:
                pass
        return {"status": "error", "error": err or out}

    def install_driver_via_windows_update(self, progress_cb: Optional[Callable[[str], None]] = None) -> List[Dict[str, Any]]:
        if progress_cb:
            progress_cb("Searching Windows Update for Wi-Fi driver…")
        ps = r"""
try {
    $Session  = New-Object -ComObject Microsoft.Update.Session
    $Searcher = $Session.CreateUpdateSearcher()
    $Result   = $Searcher.Search("IsInstalled=0 and Type='Driver'")
    $Coll = New-Object -ComObject Microsoft.Update.UpdateColl
    $Result.Updates | Where-Object {
        $_.Title -match 'Wi-?Fi|Wireless|WLAN|802\.11|Network Adapter'
    } | ForEach-Object { $Coll.Add($_) | Out-Null }
    if ($Coll.Count -eq 0) {
        Write-Output '{"status":"none","message":"No Wi-Fi driver updates found."}'
        exit 0
    }
    Write-Output "{""status"":""downloading"",""count"":$($Coll.Count)}"
    $DL = $Session.CreateUpdateDownloader(); $DL.Updates = $Coll; $DL.Download() | Out-Null
    $IN = $Session.CreateUpdateInstaller();  $IN.Updates = $Coll
    $IR = $IN.Install()
    Write-Output "{""status"":""done"",""reboot"":$($IR.RebootRequired.ToString().ToLower()),""resultCode"":$($IR.ResultCode)}"
} catch {
    Write-Output "{""status"":""error"",""error"":""$($_.Exception.Message)""}"
}
"""
        if progress_cb:
            progress_cb("Downloading & installing (may take a few minutes)…")
        _, out, err = powershell(ps, timeout=600)
        results = []
        for line in out.strip().splitlines():
            try:
                results.append(json.loads(line))
            except Exception:
                pass
        return results or [{"status": "error", "error": err or out}]

    # ---------------------------------------------------------------- reports
    def save_csv_report(self, path: str) -> None:
        snap = self.snapshot or self.collect()
        iface = snap.get("wifi_interfaces", [{}])[0] if snap.get("wifi_interfaces") else {}
        rows = [
            ["timestamp", snap.get("timestamp", "")],
            ["hostname", snap.get("hostname", "")],
            ["ssid", iface.get("ssid", "")],
            ["band", iface.get("band", "")],
            ["channel", iface.get("channel", "")],
            ["signal", iface.get("signal", "")],
            ["radio_type", iface.get("radio type", "")],
            ["rx_mbps", iface.get("receive rate (mbps)", "")],
            ["tx_mbps", iface.get("transmit rate (mbps)", "")],
        ]
        lt = self.last_test
        if lt:
            gp = lt.get("gateway_ping", {})
            ip = lt.get("internet_ping", {})
            st = lt.get("speedtest", {})
            rows += [
                ["gateway_ping_avg_ms", gp.get("avg_ms", "")],
                ["gateway_ping_loss_percent", gp.get("packet_loss_percent", "")],
                ["internet_ping_avg_ms", ip.get("avg_ms", "")],
                ["internet_ping_loss_percent", ip.get("packet_loss_percent", "")],
                ["speedtest_ping_ms", st.get("ping_ms", "")],
                ["download_mbps", st.get("download_mbps", "")],
                ["upload_mbps", st.get("upload_mbps", "")],
                ["isp", st.get("isp", "")],
            ]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["metric", "value"])
            writer.writerows(rows)

    def save_json_report(self, path: str) -> None:
        report = {
            "snapshot": self.snapshot,
            "last_test": self.last_test,
        }
        # Remove raw verbose keys to keep the file readable
        def strip_raw(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {k: strip_raw(v) for k, v in obj.items() if k not in ("raw",)}
            if isinstance(obj, list):
                return [strip_raw(i) for i in obj]
            return obj
        with open(path, "w", encoding="utf-8") as f:
            json.dump(strip_raw(report), f, indent=2, default=str)

    def recommend_changes(self, data: Dict[str, Any]) -> List[str]:
        return [
            "Forget and reconnect the 5 GHz SSID.",
            "Disable adapter power saving.",
            "Set wireless power plan to Maximum Performance.",
            "Set Preferred Band to 5 GHz when available.",
            "Set Roaming Aggressiveness to Low or Lowest.",
            "If 5 GHz stays unstable, test 802.11ac temporarily instead of 802.11ax.",
            "If you control the router, test a different non-DFS 5 GHz channel.",
        ]
