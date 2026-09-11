"""Apply the requested shorter Server 1 rules while preserving local settings."""
import json
from pathlib import Path
import os
import shutil
from dotenv import load_dotenv


def main():
    load_dotenv()
    path = Path(os.getenv("SERVERS_CONFIG", "servers.local.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    example = json.loads((Path(__file__).resolve().parents[1] / "servers.example.json").read_text(encoding="utf-8"))
    rules = next(s["rules"] for s in example if s["id"] == "server-1")
    server = next(s for s in data if s["id"] == "server-1")
    if server["rules"] == rules:
        print("Short Server 1 rules already applied.")
        return
    backup = path.with_name(path.name + ".before-short-rules")
    if not backup.exists():
        shutil.copy2(path, backup)
    server["rules"] = rules
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    print("Short Server 1 rules applied; other settings preserved.")


if __name__ == "__main__":
    main()
