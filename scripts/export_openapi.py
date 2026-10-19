import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.main import create_app

destination = Path(__file__).resolve().parents[1] / "contracts" / "openapi.json"
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(create_app().openapi(), indent=2) + "\n")
print(destination)
