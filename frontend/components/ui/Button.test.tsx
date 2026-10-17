import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BUTTON_VARIANTS, Button } from "@/components/ui/Button";

describe("Button", () => {
  it("makes a form's submit button the primary action, other buttons secondary, unless told otherwise", () => {
    render(
      <>
        <Button type="submit">Save</Button>
        <Button type="button">Cancel</Button>
        <Button type="submit" variant="danger">Delete</Button>
      </>,
    );
    expect(screen.getByText("Save").className).toContain(BUTTON_VARIANTS.primary);
    expect(screen.getByText("Cancel").className).toContain(BUTTON_VARIANTS.secondary);
    expect(screen.getByText("Delete").className).toContain(BUTTON_VARIANTS.danger);
  });
});
