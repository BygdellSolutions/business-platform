export function StatusBadge({ active }: { active: boolean }) {
  return (
    <span
      data-testid="status"
      className={`badge ${active ? "tone-success" : "tone-neutral"}`}
    >
      {active ? "Active" : "Inactive"}
    </span>
  );
}
