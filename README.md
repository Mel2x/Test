# Power: turn my computers on and off from my phone

```
 Phone ──(Tailscale, from anywhere)──▶ NUC (hub, always on) ──(home network)──▶ Windows PC / Mac
                                         • phone web app                         • agent.py
                                         • sends Wake-on-LAN                     • shutdown / restart / sleep
```

- **hub/**: runs on the NUC. It serves the phone app, wakes computers with Wake-on-LAN, and passes commands to the agents.
- **agent/**: runs on each computer you want to control. It shuts down, restarts, or sleeps that computer when the hub asks.
- Everything is plain Python 3 with no packages to install.

---

## 1. Prepare each computer for Wake-on-LAN

You need two things from every computer: its **IP address** and its **MAC address** (the hardware address, which has nothing to do with Apple).

### Windows PC
1. **Use an Ethernet cable if you can.** Wake-on-LAN over Wi‑Fi almost never works from a full shutdown.
2. **BIOS/UEFI:** enable *Wake on LAN* (it may be called *Power On By PCI-E* or *Resume by LAN*).
3. **Device Manager → Network adapters → your Ethernet adapter → Properties:**
   - *Power Management*: tick **Allow this device to wake the computer** and **Only allow a magic packet…**
   - *Advanced*: set **Wake on Magic Packet** to Enabled.
4. **Turn off Fast Startup:** Control Panel → Power Options → *Choose what the power buttons do* → untick **Turn on fast startup**. Fast Startup often stops Wake-on-LAN from working.
5. Get the addresses: open a terminal and run `ipconfig /all`. Under your Ethernet adapter, note the **IPv4 Address** and the **Physical Address**.

### Mac
> ⚠️ Macs can be woken **from sleep** over the network, but usually **not from a full shutdown** (Apple Silicon Macs never can). For the Mac, use **Sleep** in the app instead of **Shut down**, and **Turn on** will wake it.

1. System Settings → Battery (or Energy) → Options → turn on **Wake for network access**.
2. Get the addresses: System Settings → Network → your connection → Details → TCP/IP shows the **IP address**, and Hardware shows the **MAC address**.

### Both computers
Give each computer a **fixed IP** in your router (look for *DHCP reservation* or *static lease*). Otherwise the IP can change and the hub won't find the computer.

---

## 2. Install the agent on each computer

Install Python 3 first ([python.org](https://www.python.org/downloads/); on Windows, tick **Add python.exe to PATH**). Then copy the `agent` folder onto the computer.

Create a random token for each computer:
```
python -c "import secrets; print(secrets.token_urlsafe(24))"
```
Copy `agent/agent_config.example.json` to `agent/agent_config.json` and paste the token in:
```json
{ "token": "PASTE-TOKEN-HERE", "port": 8765, "dry_run": false }
```
> Tip: set `"dry_run": true` the first time. The agent will only *print* what it would do, so you can test without your computer turning off.

### Windows: run at startup
Open **PowerShell as Administrator** inside the `agent` folder:
```powershell
# Allow the hub to reach the agent through the firewall
New-NetFirewallRule -DisplayName "Power Agent" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Private

# Start the agent at boot, before anyone logs in
$py = (Get-Command python).Source
schtasks /Create /TN "PowerAgent" /SC ONSTART /RU SYSTEM /RL HIGHEST /F /TR "`"$py`" `"$PWD\agent.py`""
schtasks /Run /TN "PowerAgent"
```
Make sure your home network is set to **Private** (Settings → Network → your connection). Otherwise the firewall rule won't apply.

### Mac: run at login
Run this in Terminal from inside the `agent` folder:
```bash
mkdir -p ~/Library/LaunchAgents
cat > ~/Library/LaunchAgents/com.power.agent.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.power.agent</string>
  <key>ProgramArguments</key><array>
    <string>$(which python3)</string><string>$PWD/agent.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/power-agent.log</string>
  <key>StandardErrorPath</key><string>/tmp/power-agent.log</string>
</dict></plist>
EOF
launchctl load ~/Library/LaunchAgents/com.power.agent.plist
```
The first time you use **Shut down** or **Restart**, macOS may ask to let Python control "System Events". Click **Allow**. If the firewall asks whether Python can accept incoming connections, also click **Allow**.

---

## 3. Set up the hub on the NUC

Install Python 3 on the NUC and copy the `hub` folder onto it.

Copy `hub/config.example.json` to `hub/config.json` and fill it in:
```json
{
  "password": "a password you'll type on your phone",
  "port": 8080,
  "computers": [
    { "id": "windows", "name": "Windows PC", "mac": "AA:BB:CC:DD:EE:01",
      "ip": "192.168.1.50", "broadcast": "192.168.1.255",
      "agent_port": 8765, "agent_token": "SAME TOKEN AS THE WINDOWS agent_config.json" },
    { "id": "mac", "name": "Mac", "mac": "AA:BB:CC:DD:EE:02",
      "ip": "192.168.1.51", "broadcast": "192.168.1.255",
      "agent_port": 8765, "agent_token": "SAME TOKEN AS THE MAC agent_config.json" }
  ]
}
```
`broadcast` is your network's IP address with the last number replaced by `255`. For example, if your IPs look like `192.168.1.x`, it's `192.168.1.255`.

Test it with `python3 hub/server.py`, then open `http://<NUC-IP>:8080` on your phone while it's on home Wi‑Fi.

Also turn off sleep on the NUC, because it has to stay awake.

### Keep the hub running

**If the NUC runs Linux (Ubuntu, etc.):**
```bash
sudo tee /etc/systemd/system/power-hub.service >/dev/null <<EOF
[Unit]
Description=Power hub
After=network-online.target
Wants=network-online.target
[Service]
ExecStart=$(which python3) $PWD/hub/server.py
Restart=always
User=$USER
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable --now power-hub
```

**If the NUC runs Windows:** in an Administrator PowerShell inside the `hub` folder:
```powershell
New-NetFirewallRule -DisplayName "Power Hub" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Private
$py = (Get-Command python).Source
schtasks /Create /TN "PowerHub" /SC ONSTART /RU SYSTEM /F /TR "`"$py`" `"$PWD\server.py`""
schtasks /Run /TN "PowerHub"
```

---

## 4. Use it from anywhere with Tailscale

[Tailscale](https://tailscale.com) is a free private network that links your devices. You don't have to open any ports on your router.

1. Install Tailscale on the **NUC** and on your **phone**, and sign in to the same account on both.
2. In the Tailscale app, find the NUC's name or its `100.x.y.z` address.
3. On your phone, open `http://<nuc-tailscale-name>:8080`. This works on Wi‑Fi or mobile data, anywhere.
4. **Add it to your home screen** (iPhone: Share → *Add to Home Screen*; Android: ⋮ → *Add to Home screen*). It will open like a normal app.

Only the NUC needs Tailscale. The Windows PC and Mac just need to be on your home network.

---

## Troubleshooting

| Problem | Try this |
|---|---|
| A computer always shows **Off** | Check that the agent is running on it, the firewall allows port 8765, and the `ip` and `agent_token` in `config.json` are correct. |
| **Turn on** does nothing | Check the BIOS and adapter settings above, use Ethernet, turn off Fast Startup (Windows), and double-check the `mac` and `broadcast` values. Some computers only wake if they were shut down normally, not after a power cut. |
| Mac won't turn on | Use **Sleep** instead of **Shut down** on the Mac (see the warning above). |
| "Wrong password" | Tap **Lock** and type the password from `hub/config.json` again. |

## Security notes
- Keep the hub on Tailscale or your home network. **Don't** open port 8080 on your router to the internet.
- Each agent only accepts commands that include its own secret token, and the hub requires a password.
- `config.json` and `agent_config.json` are in `.gitignore`, so your passwords stay off GitHub.
