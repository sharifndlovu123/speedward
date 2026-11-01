import json


def to_json(report) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
