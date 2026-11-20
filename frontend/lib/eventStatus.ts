/**
 * Event publish state, derived from the event itself.
 *
 * New in 2.1.1 (2026-11-20).
 *
 * The organizer page used to work this out inline as three sibling JSX
 * conditions, where the middle one was `!isDraft && !isLive`. With `event`
 * still null — which is the case on the first render every time the page is
 * opened, and permanently if the fetch fails — `isDraft` and `isLive` are both
 * false, so that middle branch won: a draft event announced itself as
 * "Published (waiting for start)".
 *
 * The lesson is not "add a loading flag" but "do not derive a state by
 * negating the other states". `unknown` and `loading` are now first-class
 * results, so an absent event renders nothing rather than the wrong thing.
 */
export type PublishState =
  | "loading"
  | "draft"
  | "published"
  | "live"
  | "completed"
  | "unknown";

export interface EventStatusInput {
  /** The event's status string, or null/undefined when not loaded. */
  status?: string | null;
  /** ISO timestamp, or null when the event is not scheduled. */
  scheduled_at?: string | null;
}

export function derivePublishState(
  event: EventStatusInput | null | undefined,
  opts: { loading?: boolean; now?: Date } = {}
): PublishState {
  const { loading = false, now = new Date() } = opts;

  // No event yet. Never fall through to a real state.
  if (!event || !event.status) return loading ? "loading" : "unknown";

  switch (event.status) {
    case "draft":
      return "draft";
    case "completed":
      return "completed";
    case "active":
    case "paused":
      return "live";
    case "ready": {
      // A ready event whose scheduled time has arrived is treated as live, so
      // the operator is not told to wait for a start that is already due.
      const scheduled = event.scheduled_at ? new Date(event.scheduled_at) : null;
      if (scheduled && !Number.isNaN(scheduled.getTime()) && scheduled <= now) {
        return "live";
      }
      return "published";
    }
    default:
      // An unrecognised status (a newer backend, a typo in seeded data) must
      // not be guessed at.
      return "unknown";
  }
}

/** Whether the event is open for setup edits. */
export function isDraftState(event: EventStatusInput | null | undefined): boolean {
  return event?.status === "draft";
}
