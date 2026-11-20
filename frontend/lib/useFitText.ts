"use client";

import { useLayoutEffect, useRef } from "react";
import { fitTextToBox } from "./fitText";

/**
 * Shrink an element's text until it fits its box. Never clips.
 *
 * The ResizeObserver watches the PARENT, not the element itself: observing the
 * element would see the font-size change we just made, fire again, and loop.
 *
 * New in 2.2.0.
 */
export function useFitText<T extends HTMLElement>(
  minPx: number,
  maxPx: number,
  deps: unknown[]
) {
  const ref = useRef<T | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;

    const run = () => fitTextToBox(el, minPx, maxPx);
    run();

    const parent = el.parentElement;
    if (!parent || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(run);
    ro.observe(parent);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [minPx, maxPx, ...deps]);

  return ref;
}
