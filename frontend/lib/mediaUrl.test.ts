import { describe, expect, it } from "vitest";
import { resolveMediaUrl } from "./mediaUrl";

describe("resolveMediaUrl", () => {
  it("resolves local upload paths against the backend origin", () => {
    expect(
      resolveMediaUrl("/uploads/team-logos/team.png", "http://localhost:8000/api")
    ).toBe("http://localhost:8000/uploads/team-logos/team.png");
  });

  it("preserves absolute S3 or CDN image URLs", () => {
    const url = "https://bucket.s3.ap-south-1.amazonaws.com/team-logos/team.png";
    expect(resolveMediaUrl(url, "http://localhost:8000/api")).toBe(url);
  });

  it("returns undefined when no image URL is configured", () => {
    expect(resolveMediaUrl(null, "http://localhost:8000/api")).toBeUndefined();
  });
});
