"""Finance demo agent — step 2. Deliberately lacks `calculate_compound_interest`.

Without the tool's result in `context.tool_results` it raises MissingToolError, which
the wrapper turns into {"status": "FAILED", "error": "MISSING_CAPABILITY", ...}. AGEM
then builds the tool and resumes this step with the result.
"""

from python_wrapper import MissingToolError

TOOL = "calculate_compound_interest"


def run(task: str, input: dict, context: dict) -> dict:
    tool_results = context.get("tool_results", {})
    if TOOL not in tool_results:
        raise MissingToolError(TOOL)
    return {
        "company": input["company"],
        "revenue": float(input["revenue"]),
        "profit": float(input["profit"]),
        "projected_value": float(tool_results[TOOL]),
    }
