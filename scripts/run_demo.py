from datetime import date

from agents.orchestrator import OrchestratorAgent


def main():
    store_id = "store_1"
    start = date(2024, 12, 9)   # based on the availability sheet window
    end = date(2024, 12, 22)

    orchestrator = OrchestratorAgent(store_id=store_id, start_date=start, end_date=end)
    result = orchestrator.run()

    print("\n=== ROSTER SUMMARY (PLACEHOLDER) ===")
    print(f"Store: {result.roster.store_id}")
    print(f"Date range: {result.roster.start_date} to {result.roster.end_date}")
    print(f"Total assignments: {len(result.roster.assignments)}")
    print(f"Violations: {len(result.violations)}")
    print("Metrics:", result.metrics)

    # Show a few sample violations, if any
    if result.violations:
        print("\nSample violations:")
        for v in result.violations[:5]:
            print(f"- [{v.severity.value.upper()}] {v.code}: {v.message}")
    
    print("\n=== RUN SUMMARY (EXPLANATION AGENT) ===")
    for line in [l for l in result.logs if "[Summary]" in l]:
        print(line.replace("[Summary] ", ""))
    
    # We deliberately skip printing generic "last few logs" to keep the demo
    # output focused and non-redundant. If debugging is needed later,
    # the logs list is still available in OrchestratorResult.
    # print("\nLast few logs:")
    # for line in result.logs[-5:]:
    #     print("  ", line)


if __name__ == "__main__":
    main()
