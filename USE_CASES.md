# WiFi Doctor Use Cases

This document details common scenarios where WiFi Doctor can be used to resolve networking issues.

## Scenario A: Bufferbloat and Latency Spikes
**Symptoms:** Video calls stuttering, high "lag" in online games despite having high download speeds.
**Solution:**
1. Open the **Live Ping** tab and start a ping to `8.8.8.8`.
2. Observe if latency spikes coincide with network activity.
3. Use **Apply Adapter Fixes** to disable Roaming Aggressiveness, which prevents the card from constantly scanning for new APs (a common cause of lag spikes).

## Scenario B: Slow Speeds in Apartment Buildings
**Symptoms:** WiFi is slow even when standing next to the router.
**Solution:**
1. Run a **Network Scan**.
2. Check the "Channel Load" in the recommendation label.
3. If your current channel is shared with 10+ neighbors, use the **Router** tab to connect to your router and apply the "Best Channel" (e.g., a DFS channel or a less crowded 5GHz block).

## Scenario C: Driver-Related Instability
**Symptoms:** Wi-Fi option disappears from Windows entirely or "Device cannot start" in Device Manager.
**Solution:**
1. Navigate to the **Fixes** tab.
2. Check the **Driver Update** section.
3. Use "Check Windows Update" to see if a newer, WHQL-certified driver is available.
4. If unavailable, use "Open Manufacturer Page" to download the latest driver directly from Intel/Realtek/MediaTek.

## Scenario D: Corrupted Network Configuration
**Symptoms:** WiFi connects, but browser says "No Internet" and `ping 8.8.8.8` fails.
**Solution:**
1. Click **Network Stack Reset**.
2. This will flush the DNS cache and reset the Winsock catalog.
3. Reconnect to the network.
