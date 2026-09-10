{
  "bindAddress": "0.0.0.0",
  "bindPort": 2001,
  "publicPort": 2001,
  "a2s": {
    "address": "0.0.0.0",
    "port": 17777
  },
  "game": {
    "name": "@SERVER_NAME@",
    "password": "",
    "passwordAdmin": "@ADMIN_PASSWORD@",
    "scenarioId": "@SCENARIO@",
    "maxPlayers": @MAX_PLAYERS@,
    "visible": true,
    "crossPlatform": true,
    "gameProperties": {
      "serverMaxViewDistance": 2500,
      "networkViewDistance": 1000,
      "battlEye": true,
      "fastValidation": true
    },
    "mods": []
  },
  "operating": {
    "lobbyPlayerSynchronise": true
  }
}
