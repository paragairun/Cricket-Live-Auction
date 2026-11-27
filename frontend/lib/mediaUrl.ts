const DEFAULT_API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

/**
 * Uploads stored on the backend are returned as root-relative /uploads URLs.
 * Resolve those against the API host, not the Next.js frontend origin.
 * Absolute S3/CDN URLs remain unchanged.
 */
export function resolveMediaUrl(
  mediaUrl: string | null | undefined,
  apiUrl: string = DEFAULT_API_URL
): string | undefined {
  if (!mediaUrl) return undefined;

  try {
    const apiOrigin = new URL(apiUrl).origin;
    return new URL(mediaUrl, apiOrigin).toString();
  } catch {
    // Preserve the original value if a custom API URL cannot be parsed.
    return mediaUrl;
  }
}
