# SmartSearch AI — Cloudflare Tunnel Deployment Guide

Expose your local or hosted SmartSearch AI stack to the public internet securely over HTTPS so anyone using your Chrome extension can connect from anywhere in the world.

---

## 1. Why Cloudflare Tunnel?

Cloudflare Tunnel (`cloudflared`) connects your local Docker container directly to Cloudflare's global Anycast edge network using an encrypted outbound-only connection.

```text
Any User in the World (Chrome Extension)
                │
                ▼ (Public HTTPS)
Cloudflare Edge Network (DDoS Shield, Free SSL, Global CDN)
                │
                ▼ (Encrypted Outbound Tunnel)
SmartSearch Tunnel Container (`cloudflared`)
                │
                ▼ (Local Bridge)
Nginx Reverse Proxy (:80)
                │
                ▼
FastAPI Backend (:8000)
                │
SQLite Prefix Index (3.48 GB) + Trained PyTorch LSTM
```

### Major Advantages:
- **No Port Forwarding Required:** You do not need to open ports on your home or office router.
- **Hidden IP Address:** Your home/ISP public IP address is never revealed to clients or attackers.
- **Automatic Free SSL/TLS:** Cloudflare automatically manages HTTPS certificates.
- **DDoS & Bot Mitigation:** Built-in Cloudflare L3/L4/L7 rate limiting and threat filtering.
- **100% Free:** Cloudflare Tunnels are included in the free tier of Cloudflare.

---

## 2. Option A: Free Quick Tunnel (Instant — No Setup Needed)

If you do **not** own a custom domain name or do not want to configure a Cloudflare account right now, you can generate a free, instant public HTTPS endpoint on `*.trycloudflare.com`.

### Step 1: Launch the Tunnel

From your PowerShell terminal in the `project/` directory:

```powershell
.\start_tunnel.ps1
```

*(Or simply double-click `project/start_tunnel.bat` in Windows Explorer).*

### What happens:
1. Docker checks that `smartsearch-api` and `nginx` are running.
2. The `cloudflared` container starts on the internal Docker network.
3. Cloudflare registers an ephemeral tunnel and prints your unique URL:

```text
==========================================================
  SUCCESS! YOUR PUBLIC CLOUDFLARE URL IS LIVE:
  https://example-random-words.trycloudflare.com
==========================================================
```

### Step 2: Configure the Chrome Extension

1. Click the **SmartSearch AI** icon in Chrome to open the popup.
2. In the **API Base URL** input field, paste your generated URL:
   ```text
   https://example-random-words.trycloudflare.com
   ```
3. Click **Save Settings**.
4. The status banner will immediately show **FastAPI Online (CPU)**!

Anyone with the SmartSearch AI Chrome extension can now use your server from any network or device.

---

## 3. Option B: Permanent Named Tunnel (Custom Domain)

If you own a domain (e.g., `yourdomain.com`) managed on Cloudflare, you can create a permanent, unchanging URL (e.g., `https://api.yourdomain.com`).

### Step 1: Create a Tunnel in Cloudflare Zero Trust

1. Log into your [Cloudflare Dashboard](https://dash.cloudflare.com/).
2. On the left sidebar, click **Zero Trust**.
3. Go to **Networks** → **Tunnels** → click **Add a tunnel**.
4. Choose **Cloudflare (cloudflared)** as the connector type and click **Next**.
5. Give your tunnel a name (e.g., `smartsearch-production`) and click **Save tunnel**.
6. Under **Choose your environment**, select **Docker**.
7. You will see a command containing a long token:
   ```text
   docker run cloudflare/cloudflared:latest tunnel --no-autoupdate run --token eyJh...
   ```
   **Copy only the token string** starting with `eyJh...`.

### Step 2: Configure Public Hostname in Cloudflare

1. In the same Cloudflare Tunnel setup wizard, click the **Public Hostnames** tab.
2. Click **Add a public hostname**:
   - **Subdomain:** `smartsearch` (or `api`)
   - **Domain:** select your domain (e.g., `yourdomain.com`)
   - **Path:** leave blank
   - **Service Type:** `HTTP`
   - **URL:** `nginx:80`
3. Click **Save hostname**.

### Step 3: Add the Token to SmartSearch AI

Create or update your `.env` file in the `project/` directory:

```env
CLOUDFLARE_TUNNEL_TOKEN=eyJh...your_token_here...
CLOUDFLARE_TUNNEL_COMMAND=run
```

### Step 4: Start the Permanent Tunnel

```powershell
docker compose --profile tunnel up -d
```

Your backend is now permanently reachable at:
```text
https://smartsearch.yourdomain.com
```

---

## 4. Extension Manifest V3 Compatibility

The SmartSearch AI extension [`extension/manifest.json`](../extension/manifest.json) is already configured with:

```json
"host_permissions": [
  "http://localhost/*",
  "http://127.0.0.1:8000/*",
  "http://localhost:8000/*",
  "https://*/*"
]
```

Because `"https://*/*"` is included, the extension has browser permission to communicate with any HTTPS endpoint, including `*.trycloudflare.com` and your custom Cloudflare domain without browser security warnings.

---

## 5. Verification Commands

To check that your Cloudflare tunnel is healthy:

```powershell
# Check tunnel container status
docker ps --filter "name=smartsearch-tunnel"

# View live tunnel connection logs
docker logs --tail 20 smartsearch-tunnel

# Test autocomplete via your public Cloudflare URL
curl.exe "https://<your-tunnel-url>/api/v1/suggest?q=deep%20lea&limit=3"

# Test website trust check via your public Cloudflare URL
curl.exe "https://<your-tunnel-url>/api/v1/site-check?url=https://github.com"
```

---

## 6. Stopping the Tunnel

To pause public access while keeping your local development containers running:

```powershell
docker compose --profile tunnel stop cloudflared
```
