[Unit]
Description=Arma Reforger Dedicated Server (test)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=@USER@
WorkingDirectory=@INSTALL_DIR@
ExecStart=@INSTALL_DIR@/ArmaReforgerServer -config @INSTALL_DIR@/configs/server.json -profile @INSTALL_DIR@/profile -maxFPS 60 -logStats 30000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
