/**
 * Regression tests for the "draft event says Published" bug.
 *
 * The first two tests are the bug itself: before 2.1.1 a null event produced
 * "published", because the state was derived by negating the others.
 */
import { describe, expect, it } from "vitest";

import { derivePublishState, isDraftState } from "./eventStatus";

const NOW = new Date("2026-11-20T10:00:00Z");

describe("derivePublishState — the reported bug", () => {
  it("never reports 'published' for an event that has not loaded", () => {
    // This is the bug: !isDraft && !isLive was true for a null event.
    expect(derivePublishState(null, { loading: true, now: NOW })).toBe("loading");
    expect(derivePublishState(null, { loading: false, now: NOW })).toBe("unknown");
    expect(derivePublishState(undefined, { now: NOW })).toBe("unknown");
    expect(derivePublishState({}, { now: NOW })).toBe("unknown");
    expect(derivePublishState({ status: null }, { now: NOW })).toBe("unknown");
  });

  it("reports a draft event as draft", () => {
    expect(derivePublishState({ status: "draft" }, { now: NOW })).toBe("draft");
    // Loading in progress must not override a status we already have.
    expect(
      derivePublishState({ status: "draft" }, { loading: true, now: NOW })
    ).toBe("draft");
    // A scheduled date does not make a draft event published.
    expect(
      derivePublishState(
        { status: "draft", scheduled_at: "2026-01-01T00:00:00Z" },
        { now: NOW }
      )
    ).toBe("draft");
  });
});

describe("derivePublishState — the other statuses", () => {
  it("reports an unscheduled ready event as published", () => {
    expect(derivePublishState({ status: "ready" }, { now: NOW })).toBe("published");
  });

  it("reports a ready event as published until its scheduled time arrives", () => {
    expect(
      derivePublishState(
        { status: "ready", scheduled_at: "2026-11-20T18:00:00Z" },
        { now: NOW }
      )
    ).toBe("published");
  });

  it("reports a ready event as live once its scheduled time has passed", () => {
    expect(
      derivePublishState(
        { status: "ready", scheduled_at: "2026-11-20T09:00:00Z" },
        { now: NOW }
      )
    ).toBe("live");
  });

  it("reports active and paused as live", () => {
    expect(derivePublishState({ status: "active" }, { now: NOW })).toBe("live");
    expect(derivePublishState({ status: "paused" }, { now: NOW })).toBe("live");
  });

  it("reports completed as completed", () => {
    expect(derivePublishState({ status: "completed" }, { now: NOW })).toBe("completed");
  });

  it("does not guess at an unrecognised status", () => {
    expect(derivePublishState({ status: "archived" }, { now: NOW })).toBe("unknown");
    // Guards against an enum serialised as "AuctionStatus.draft" rather than
    // "draft" — that must read as unknown, not as published.
    expect(derivePublishState({ status: "AuctionStatus.draft" }, { now: NOW })).toBe(
      "unknown"
    );
  });

  it("ignores an unparseable scheduled date rather than throwing", () => {
    expect(
      derivePublishState({ status: "ready", scheduled_at: "not a date" }, { now: NOW })
    ).toBe("published");
  });
});

describe("isDraftState", () => {
  it("is true only for a loaded draft event", () => {
    expect(isDraftState({ status: "draft" })).toBe(true);
    expect(isDraftState({ status: "ready" })).toBe(false);
    expect(isDraftState(null)).toBe(false);
    expect(isDraftState(undefined)).toBe(false);
  });
});
