[Unit]
Description=OYB Discord community bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=@USER@
WorkingDirectory=@REPO_DIR@
ExecStart=@PYTHON@ main.py
Environment=PYTHONUNBUFFERED=1
UMask=0077
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
