import { describe, expect, it } from "vitest";
import { autoQmeanDetail } from "./AutoQmeanNotice";

describe("autoQmeanDetail", () => {
  it("conta i QMEAN finiti per esito, al singolare e al plurale", () => {
    expect(autoQmeanDetail({ completed: 28, failed: 1 })).toBe("28 completati, 1 fallito");
    expect(autoQmeanDetail({ completed: 1, cancelled: 2, running: 3 })).toBe("1 completato, 2 annullati");
    expect(autoQmeanDetail({})).toBe("");
  });
});
