# 全來源繁簡／異體字搜尋檢查 — 給 Codex 的修改說明（MissAV＋線上電影院）

> 給 Codex 的修改說明（MissAV 與線上電影院兩個 repo 各放一份，內容相同）。所有數據都在 2026-10-09 用各 app 自己的 adapter 實際打站方測得。
> **前置條件**：兩個 repo 目前有我還沒 commit 的修改，Codex 開工前要先 commit：
> - MissAV：`backend/zh.py` 改用 `tw2s`／`s2tw`、紅果封面修正等。
> - 線上電影院：移植了 `backend/zh.py`、紅果繁簡搜尋與封面修正，`requirements.txt` 加了 opencc。
> 這份文件的修法都建立在 `backend/zh.py` 上。

## Context

使用者用台灣繁體搜尋。前兩份文件已經修好 51短劇、紅果、黃果，這次把**兩個 app 的所有來源**都檢查一遍，看還有沒有繁體搜不到、或搜到比較少的情況。

兩個 app 共用的 adapter（anigamer、chinaq、gimy、mmov、mvffm、hongguo）程式碼**完全相同**（只差換行字元），所以只在 MissAV 測一次，修改要**兩邊都套用**。dramaq 只有線上電影院有。

## 檢查結果

數字格式：第 1 頁筆數／總頁數（`+` 表示還有下一頁，`-` 表示沒有）。

| 來源 | App | 繁體 vs 簡體實測 | 結論 |
|---|---|---|---|
| **dramaq** | 線上電影院 | 慶餘年 **0** vs 庆余年 2；餘生 **8** vs 余生 18；其他 8 組相同 | **要修**：站方自己做繁轉簡時把「餘」轉錯 |
| **chinaq** | 兩邊 | 站方沒有搜尋功能，是在 `/all.html` 的 6055 個片名裡比對；「鬥羅大陸」0（片名寫「斗羅大陸」）、「裡」21（另有 26 部寫「裏」）、简体 0 | **要修**：比對前先把查詢字和片名都正規化 → 裡 47、鬥羅大陸 2、长相思 2 |
| **mmov** | 兩邊 | 繁體正常（慶餘年 32、長相思 33）；簡體全部 0；「鬥羅大陸」0 vs「斗羅大陸」36 | **要修**：異體字查不到。0 筆時改用正規化後的繁體字再查一次 |
| anigamer | 兩邊 | 繁體正常（進擊的巨人 18）；簡體全部 0 | 可選（P3）：只影響簡體輸入 |
| mvffm | 兩邊 | 繁體 ≥ 簡體（慶餘年 36+ vs 18、與鳳行 19 vs 4） | 不用改 |
| missav、123av、pornhub | MissAV | 繁簡結果相同 | 不用改 |
| netflav、ininav | MissAV | 繁體多很多（ininav 國產 1496 頁 vs 国产 5 筆） | 不用改 |
| javday、huangguo2 | MissAV | 前一份已確認繁體沒問題 | 不用改 |
| duanju51、hongguo、huangguo | MissAV（hongguo 兩邊） | 前兩份已修 | 已完成 |
| **gimy** | 兩邊 | 首頁正常，但搜尋頁 `/find/` 回 Cloudflare JS 驗證（403「Just a moment...」） | **無法測**。這是另一個問題：目前 gimy 的搜尋**全部失敗**，不在這份範圍 |
| **jable** | MissAV | 搜尋頁回 Cloudflare 429「Access denied」（首頁正常；可能是我連續測試觸發的） | **無法測**，之後再測 |
| **avple** | MissAV | 整站 403 | **無法測**。少數成功的請求裡，繁體「護士」有 100 頁 |

## 修改

### P0：`backend/zh.py` 新增兩個 helper（兩個 repo 都要加）

```python
def normalize_traditional(text: str) -> str:
    """Taiwan-standard Traditional form; folds variants such as 鬥→斗 (鬥羅大陸→斗羅大陸) and 裏→裡."""
    return to_traditional(to_simplified(text))


def search_key(text: str) -> str:
    """Script- and variant-insensitive key for local title matching."""
    return to_simplified(text).casefold()
```

### P1：dramaq（只有線上電影院，`backend/sites/dramaq.py` 的 `search()`）

- 用 `zh.to_simplified(q)` 呼叫 `/search?q=`。如果結果是 0 筆，而且簡體跟原字不同，就用原字再查一次。`Listing` 的 title 維持使用者輸入的 `q`。
- 站方有 429 限流（測試時出現過），所以**只在 0 筆時才多送一次請求**，不要兩種寫法都查。
- 測試：查詢字有轉成簡體（`quote("庆余年")`）；0 筆時改用原字；簡體跟原字相同時只送一次請求。

### P1：chinaq（兩邊，`backend/sites/chinaq.py`）

- `_all_cards()` 的 memo 改成同時存每張卡片的 `zh.search_key(title)`，不要每次搜尋都把 6000 個片名重新轉換一次。可以把 `_all_memo` 改成 `(time, items, keys)`。
- `search()`：`needle = zh.search_key(q)`，用 `needle in key` 比對；`_slice` 的分頁邏輯不變。
- 效能：先量一次 6055 個片名的轉換時間。超過 1 秒的話，就在第一次建 memo 時做（TTL 內只做一次），**不能**每次搜尋都做。
- 測試：「鬥羅大陸」找得到「斗羅大陸」；「裡」找得到寫「裏」的片名；简体「长相思」找得到繁體片名；英數查詢照常；轉換只在建 memo 時做一次（patch `zh.search_key` 計算呼叫次數）。

### P1：mmov（兩邊，`backend/sites/mmov.py` 的 `search()`）

- 先照原樣用 `query` 查。如果 0 筆，而且 `zh.normalize_traditional(query) != query`，就用正規化後的字再查一次；正規化也會把簡體輸入轉成繁體。title 維持原字。
- 注意 `_listing` 的快取 key 是 `"search:" + query`，第二次查詢要用正規化後的字當 key，兩次結果才不會混在一起。
- 站方有 `SiteBusy` 冷卻機制（`backend/sites/mmov.py:48-95`）。第一次查詢就被限流時**不要重試**，直接丟出例外。
- 測試：「鬥羅大陸」0 筆時會改查「斗羅大陸」；「庆余年」0 筆時會改查「慶餘年」；第一次就有結果時只送一次請求；`SiteBusy` 不重試。

### P3（可選）：anigamer（兩邊）

- 繁體使用者不受影響。如果要讓簡體輸入也查得到，比照 mmov：0 筆時改用 `zh.normalize_traditional(query)` 再查一次。

## 不在這份範圍、但要注意

- **gimy 搜尋目前全部失敗**：`/find/` 被 Cloudflare JS 驗證擋住，app 的原則是不執行遠端 JS，所以無法通過。這是獨立問題，要另外決定怎麼處理（例如改用站方其他沒被擋的搜尋介面）。
- jable 和 avple 等站方恢復後再補測繁簡。

## 驗證

1. 兩個 repo 各自跑 `python -m unittest tests.test_zh tests.test_chinaq tests.test_mmov`，線上電影院再加 `tests.test_dramaq`。這些測試檔都已存在，新測試加在裡面。
2. 兩邊各跑一次完整的 `unittest discover`。目前兩邊都有 4 個舊的失敗（`test_mmov.test_registration_history_and_explicit_episode_seconds`、`test_playback_repairs` 的 3 個），不能變多；如果改 mmov 剛好修好它們更好。
3. 實機（app 搜尋框輸入繁體）：
   - dramaq：搜「慶餘年」要有結果（原本 0）。
   - chinaq：搜「鬥羅大陸」要有結果（原本 0）；搜「裡」約 47 筆（原本 21）。
   - mmov：搜「鬥羅大陸」要有約 36 筆（原本 0）；搜「慶餘年」照常 32 筆左右。
