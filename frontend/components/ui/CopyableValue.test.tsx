import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CopyableValue } from "@/components/ui/CopyableValue";

describe("CopyableValue", () => {
  it("shows the whole value and copies it", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    render(<CopyableValue label="Organization ID" value="00000000-0000-4000-8000-0000000000a1" testId="organization-id" />);

    expect(screen.getByTestId("organization-id")).toHaveTextContent("00000000-0000-4000-8000-0000000000a1");
    await user.click(screen.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("00000000-0000-4000-8000-0000000000a1");
    expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument();
  });
});
