// Pure helpers both sites share; the headless checks under e2e/ cover the pages themselves.
import { describe, expect, it } from "vitest";
import { episodeNumber, episodeRanges, episodeSpan, missingEpisodes } from "./components/MissingEpisodes";
import { pageParam } from "./hooks/useAppendListing";
import { parseScope, searchPath } from "./searchPrefs";
import { isPortrait, searchHint } from "./source";
import { BROWSE_TITLES } from "./browseTitles";
import { APP, storageKey } from "./appConfig";

describe("episodes", () => {
  it("reads the number or span out of a title", () => {
    expect(episodeNumber("第 3 集", "3")).toBe(3);
    expect(episodeNumber("EP07", "x")).toBe(7);
    expect(episodeSpan("第1-4集", "x")).toEqual([1, 4]);
    expect(episodeNumber("花絮", "sp")).toBeNull();
  });
  it("lists the numbers missing between the first and the last", () => {
    const episodes = [1, 2, 5, 6].map((n) => ({ id: String(n), title: `第${n}集`, playlist: "" }));
    expect(missingEpisodes(episodes)).toEqual([3, 4]);
    expect(missingEpisodes([{ id: "a", title: "花絮", playlist: "" }])).toEqual([]);
    expect(episodeRanges([3, 4, 7, 9, 10, 11])).toBe("3–4、7、9–11");
  });
});

describe("listing helpers", () => {
  it("accepts only a positive page number", () => {
    expect(pageParam("3")).toBe(3);
    expect(pageParam("0")).toBe(1);
    expect(pageParam("x")).toBe(1);
    expect(pageParam(null)).toBe(1);
  });
  it("knows which sources use portrait posters and what to search for", () => {
    expect(isPortrait("hongguo")).toBe(true);
    expect(isPortrait("missav")).toBe(false);
    expect(isPortrait(undefined)).toBe(false);
    expect(searchHint("動畫")).toBe("搜尋動畫名稱");
    expect(searchHint("")).toBe("搜尋劇名、關鍵字");
  });
});

describe("search preferences", () => {
  it("keeps unknown scopes at the default", () => {
    expect(parseScope("all")).toBe("all");
    expect(parseScope("nope")).toBe("source");
  });
  it("builds a search path with the source and scope", () => {
    expect(searchPath("/tv", "a b", "mmov", "group", 2)).toBe("/tv/search?q=a+b&src=mmov&scope=group&page=2");
    expect(searchPath("", "x", "mmov")).toBe("/search?q=x&src=mmov");
  });
});

describe("site configuration", () => {
  it("prefixes storage keys and overlays the site's browse headings", () => {
    expect(storageKey("theme")).toBe(`${APP.storagePrefix}.theme`);
    for (const [key, title] of Object.entries(APP.browseTitles)) expect(BROWSE_TITLES[key]).toBe(title);
    expect(BROWSE_TITLES.cn).toBe("陸劇");
  });
});
