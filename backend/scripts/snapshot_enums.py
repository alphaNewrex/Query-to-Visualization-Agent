"""Refresh catalog/data/enums.json from GET /studies/enums.

Run from backend/:  uv run python scripts/snapshot_enums.py

One request. The file maps each enum type to its tokens and the registry's own display names
(`legacyValue`), which `ctviz.catalog.vocab` turns into labels. It is committed, so the service never
asks the registry for labels at run time.
"""

import json
import sys
from pathlib import Path
from typing import Final

import httpx2

from ctviz.settings import Settings

OUTPUT: Final = Path(__file__).parent.parent / "src" / "ctviz" / "catalog" / "data" / "enums.json"
USER_AGENT: Final = "ctviz-snapshot-enums/0.1 (ClinicalTrials.gov query-to-visualization take-home)"


def compact(enums: object) -> dict[str, dict[str, str]]:
    """`[{type, values: [{value, legacyValue}]}]` as `{type: {value: legacyValue}}`."""
    if not isinstance(enums, list):
        raise ValueError("The enums endpoint did not return a list.")
    return {
        entry["type"]: {item["value"]: item.get("legacyValue", item["value"]) for item in entry["values"]}
        for entry in enums
    }


def main() -> int:
    # model_construct reads no environment variable and no env file, so the real key is never loaded.
    base_url = Settings.model_construct().ctgov_base_url
    response = httpx2.get(f"{base_url}/studies/enums", headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    snapshot = compact(response.json())
    OUTPUT.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT} ({len(snapshot)} enum types)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
