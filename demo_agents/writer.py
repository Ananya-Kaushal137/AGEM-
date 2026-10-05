"""Writer demo agent — step 4. Turns the checked figures into the final report."""


def run(task: str, input: dict, context: dict) -> dict:
    margin = 100 * float(input["profit"]) / float(input["revenue"])
    status = "verified" if input.get("verified") else "NOT verified"
    report = (
        f"Investment report: {input['company']}\n"
        f"Revenue: ${input['revenue']:.2f}B, profit: ${input['profit']:.2f}B (margin {margin:.1f}%).\n"
        f"Projected value of the investment: {input['projected_value']:.2f}.\n"
        f"Figures {status} by the Fact Checker."
    )
    return {"report": report}
