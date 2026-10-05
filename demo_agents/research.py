"""Research demo agent — step 1. Returns fixed company figures (no web, so the demo is repeatable)."""

COMPANIES = {
    "Tesla": {"revenue": 96.77, "profit": 14.97, "summary": "Electric vehicles and energy storage."},
}


def run(task: str, input: dict, context: dict) -> dict:
    company = input.get("company", "Tesla")
    data = COMPANIES.get(company, {"revenue": 10.0, "profit": 1.0, "summary": "No public data; placeholder figures."})
    # revenue/profit in USD billions; numbers, because Finance and the Fact Checker map them.
    return {"company": company, "summary": data["summary"], "revenue": data["revenue"], "profit": data["profit"]}
