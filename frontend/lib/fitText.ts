/**
 * Fit text to its box by shrinking, never by clipping.
 *
 * A live auction has no control over how long a player's name is, and a name
 * that reads "P…" on the operator's console is worse than a small one. CSS
 * alone cannot do this: `truncate` crops, `line-clamp` crops, and `clamp()`
 * sizes against the viewport rather than against the text. So we measure.
 *
 * New in 2.2.0.
 */

/**
 * Largest size in [min, max] for which `fits` holds, by binary search.
 *
 * `fits` is assumed monotonic: if a size fits, every smaller size fits too.
 * That holds for text in a fixed box. Pure and separated from the DOM so the
 * search itself is unit-testable.
 */
export function largestFittingSize(
  min: number,
  max: number,
  fits: (size: number) => boolean,
  tolerance = 0.5,
  maxSteps = 24
): number {
  if (min > max) return min;
  // The common case: it already fits at full size, so do not shrink at all.
  if (fits(max)) return max;

  let lo = min;
  let hi = max;
  let best = min;
  let steps = 0;

  while (hi - lo > tolerance && steps < maxSteps) {
    const mid = (lo + hi) / 2;
    if (fits(mid)) {
      best = mid;
      lo = mid;
    } else {
      hi = mid;
    }
    steps += 1;
  }
  return best;
}

/** True when the element's content is fully inside its own box. */
export function elementFits(el: HTMLElement, slack = 1): boolean {
  return (
    el.scrollHeight <= el.clientHeight + slack &&
    el.scrollWidth <= el.clientWidth + slack
  );
}

/**
 * Size `el`'s font so its text fits its box exactly, and return the size used.
 * Call after layout; safe to call repeatedly.
 */
export function fitTextToBox(el: HTMLElement, minPx: number, maxPx: number): number {
  const previous = el.style.fontSize;
  const size = largestFittingSize(minPx, maxPx, (px) => {
    el.style.fontSize = `${px}px`;
    return elementFits(el);
  });
  el.style.fontSize = `${size}px`;
  if (size === maxPx && previous === `${maxPx}px`) return size;
  return size;
}
