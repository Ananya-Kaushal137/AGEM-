"""Fact Checker demo agent — step 3. Simple consistency checks on Finance's numbers."""


def run(task: str, input: dict, context: dict) -> dict:
    revenue, profit = float(input["revenue"]), float(input["profit"])
    checks = {
        "revenue_positive": revenue > 0,
        "profit_below_revenue": profit <= revenue,
        "projection_positive": float(input["projected_value"]) > 0,
    }
    return {
        "company": input["company"],
        "revenue": revenue,
        "profit": profit,
        "projected_value": float(input["projected_value"]),
        "verified": all(checks.values()),
        "checks": checks,
    }
