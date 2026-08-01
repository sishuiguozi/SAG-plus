export interface SourceGraphLoadBudget {
  documentLimit: number;
  eventLimit: number;
  entityLimit: number;
}

function stagedLimit(finalValue: number, ratio: number, minimum: number) {
  return Math.min(finalValue, Math.max(minimum, Math.ceil(finalValue * ratio)));
}

/** Build a small first paint, an intermediate fill, and the final view budget. */
export function progressiveSourceGraphBudgets(
  finalBudget: SourceGraphLoadBudget,
): SourceGraphLoadBudget[] {
  if (
    finalBudget.documentLimit <= 64 &&
    finalBudget.eventLimit <= 64 &&
    finalBudget.entityLimit <= 40
  ) {
    return [finalBudget];
  }

  const candidates: SourceGraphLoadBudget[] = [
    {
      documentLimit: stagedLimit(finalBudget.documentLimit, 0.35, 40),
      eventLimit: stagedLimit(finalBudget.eventLimit, 0.35, 40),
      entityLimit: stagedLimit(finalBudget.entityLimit, 0.35, 24),
    },
    {
      documentLimit: stagedLimit(finalBudget.documentLimit, 0.7, 64),
      eventLimit: stagedLimit(finalBudget.eventLimit, 0.7, 64),
      entityLimit: stagedLimit(finalBudget.entityLimit, 0.7, 40),
    },
    finalBudget,
  ];

  const seen = new Set<string>();
  return candidates.filter((budget) => {
    const key = `${budget.documentLimit}:${budget.eventLimit}:${budget.entityLimit}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
