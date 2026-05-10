APP_NAME = "WiFi Doctor"
APP_VERSION = "2.0"

MANUFACTURER_DRIVER_URLS = {
    "intel":    "https://www.intel.com/content/www/us/en/support/detect.html",
    "realtek":  "https://www.realtek.com/Download/List?cate_id=584",
    "qualcomm": "https://www.qualcomm.com/products/technology/wi-fi",
    "atheros":  "https://www.qualcomm.com/products/technology/wi-fi",
    "broadcom": "https://www.broadcom.com/products/wireless",
    "mediatek": "https://www.mediatek.com/products/connectivity-and-networking/wifi",
    "marvell":  "https://www.marvell.com/products/networking/wireless-lan.html",
}
OOKLA_URL = "https://install.speedtest.net/app/cli/ookla-speedtest-1.2.0-win64.zip"

# Severity colors
SEV_COLORS = {"high": "#e74c3c", "medium": "#e67e22", "low": "#27ae60", "info": "#2980b9"}
STATUS_COLORS = {
    "idle":       ("#bdc3c7", "#2c3e50"),
    "scanning":   ("#2980b9", "#ffffff"),
    "issue":      ("#e74c3c", "#ffffff"),
    "fixing":     ("#e67e22", "#ffffff"),
    "resolving":  ("#8e44ad", "#ffffff"),
    "good":       ("#27ae60", "#ffffff"),
    "done":       ("#16a085", "#ffffff"),
}
