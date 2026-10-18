import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AttentionStrip } from "@/features/dashboard/AttentionStrip";
import { SummaryCard } from "@/features/dashboard/SummaryCard";
import { TodayList } from "@/features/dashboard/TodayList";
import type { Appointment } from "@/lib/api/types";

const booking = (over: Partial<Appointment>): Appointment =>
  ({ id: "a1", title: "Massage", starts_at: "2026-10-18T07:30:00Z", ends_at: "2026-10-18T08:30:00Z", status: "scheduled", customer_name: "Anna", practitioner_name: "Fredrik", subjects: [{ label: "Kalle" }], ...over }) as Appointment;

describe("AttentionStrip", () => {
  it("lists only what is above zero, each as a link", () => {
    render(<AttentionStrip items={[{ label: "past due date", count: 2, href: "/x" }, { label: "out of stock", count: 0, href: "/y" }]} />);
    expect(screen.getByRole("link", { name: /2 past due date/ })).toHaveAttribute("href", "/x");
    expect(screen.queryByText(/out of stock/)).toBeNull();
  });

  it("says all clear when nothing is above zero", () => {
    render(<AttentionStrip items={[{ label: "past due date", count: 0, href: "/x" }]} />);
    expect(screen.getByTestId("attention-clear")).toBeInTheDocument();
    expect(screen.queryByTestId("attention")).toBeNull();
  });
});

describe("TodayList", () => {
  it("shows the booking's hours in the organization's zone and leaves cancelled bookings out", () => {
    render(<TodayList orgId="o" zone="Europe/Stockholm" appointments={[booking({}), booking({ id: "a2", title: "Gone", status: "cancelled" })]} />);
    expect(screen.getAllByTestId("today-row")).toHaveLength(1);
    expect(screen.getByText("09:30–10:30")).toBeInTheDocument();
    expect(screen.getByText(/Anna · Kalle · Fredrik/)).toBeInTheDocument();
  });

  it("says so when nothing is booked", () => {
    render(<TodayList orgId="o" zone={null} appointments={[]} />);
    expect(screen.getByTestId("today-empty")).toBeInTheDocument();
  });
});

describe("SummaryCard note", () => {
  it("shows the note on an attention card with work, and only as a tooltip otherwise", () => {
    render(
      <>
        <SummaryCard title="A" count={1} note="Do it" tone="attention" testId="a" />
        <SummaryCard title="B" count={1} note="Quiet" testId="b" />
      </>,
    );
    expect(screen.getByText("Do it")).toBeVisible();
    expect(screen.queryByText("Quiet")).toBeNull();
    expect(screen.getByTestId("b")).toHaveAttribute("title", "Quiet");
  });
});
