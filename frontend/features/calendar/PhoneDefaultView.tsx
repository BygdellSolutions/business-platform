"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";

/**
 * A week of columns does not fit a phone, so a phone that opens the calendar without choosing a view gets the day. The server
 * cannot know the screen width; this replaces the address once, and a view chosen by the person (in the address) is left alone.
 */
export function PhoneDefaultView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const chosen = params.has("view");

  useEffect(() => {
    if (chosen || !window.matchMedia("(max-width: 767px)").matches) return;
    const next = new URLSearchParams(window.location.search);
    next.set("view", "day");
    router.replace(`${pathname}?${next}`);
  }, [chosen, pathname, router]);

  return null;
}
