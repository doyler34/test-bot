[Unit]
Description=Reforger Discord session timer bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=@USER@
WorkingDirectory=@REPO_DIR@
ExecStart=@REPO_DIR@/.venv/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
