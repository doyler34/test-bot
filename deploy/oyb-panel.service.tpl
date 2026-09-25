[Unit]
Description=OYB Control web panel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=@USER@
WorkingDirectory=@REPO_DIR@
ExecStart=@PYTHON@ -m panel serve
Environment=PYTHONUNBUFFERED=1
UMask=0077
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
