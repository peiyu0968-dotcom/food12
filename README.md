# food12：食物照片記錄 Agent（第一版）

一支跑在你電腦上的 Discord bot：指定頻道有人傳食物照片，它就用 `claude -p` 判斷、估熱量、記進 Excel、存照片，並回覆上傳者。agent 關著就不處理。

設計請看 [`docs/architecture.md`](docs/architecture.md)（開頭有「第一版範圍」說明）。

## 啟動

```powershell
uv sync                 # 第一次，或套件有變動時
uv run food-agent       # 啟動 agent（Ctrl+C 結束）
```

等價的寫法：`uv run python -m food_agent`

第一次使用前：

1. 照 `docs/discord-server-setup.md` 建好 Server 和頻道。
2. 照 `docs/discord-bot-setup.md` 建好 bot、打開 **Message Content Intent**，並勾 **View Channels、Send Messages、Read Message History、Add Reactions** 四個權限。
3. 把真的 `DISCORD_TOKEN` 和 `CHANNEL_ID` 填進專案根目錄的 `.env`（**不要貼進對話、不要 commit**）。

`.env` 四個項目：

| 項目 | 意思 |
|---|---|
| `DISCORD_TOKEN` | bot 的登入密碼，絕不公開 |
| `CHANNEL_ID` | 只聽這一個頻道 |
| `CLAUDE_MODEL` | 給 `claude -p` 用的模型，例如 `haiku`、`sonnet` |
| `DATA_DIR` | 放 Excel 與照片的資料夾（相對路徑以 `.env` 所在位置為準） |

執行後會產生：`DATA_DIR/food_log.xlsx`（紀錄）、`DATA_DIR/photos/`（照片）。

## 怎麼運作

1. 頻道有人傳圖片 → bot 對那則訊息加 ⏳。
2. 圖片進排隊區，**一次只開一個 `claude -p`**。
3. `claude -p` 回固定格式 JSON；程式檢查過才採用（不是合法 JSON、重量或熱量不是數字、超出合理範圍，就回「分析失敗，請再傳一次」，不會當掉）。
4. 存照片到 `photos/`（檔名 `YYYY-MM-DD hh-mm-ss 名稱.副檔名`，同一秒撞名加 `-2`、`-3`）。
5. 在 Excel **最後加一列**。Excel 若正被你開著，終端機會印警告並重試。
6. 「回覆」該則訊息：是不是食物、成份、重量、熱量、當日已攝取熱量（**整個頻道合算，不分人**，以電腦本地日期為準）。
7. 不是食物：一樣建檔、存照片、回覆；食物敘述寫「不是食物」，成份寫判定出來的東西。

## 四段防護在哪裡

每一段都用下面這種註解框起來，**一開始全部開啟**。想關掉某一段，把「開始」到「結束」之間整段註解掉即可（程式仍能執行；框外那幾行就是「沒有防護」時的預設行為）。

| 防護 | 檔案 | 行數（大約） | 做什麼 |
|---|---|---|---|
| 防護 1：`claude -p` 的檔案範圍隔離 | `src/food_agent/analyzer.py` | 198–210 | 工作資料夾是只放那一張照片的暫存資料夾，只給讀檔工具並鎖定範圍，讀不到 DATA_DIR |
| 防護 2：上傳者的字只當資料 | `src/food_agent/analyzer.py` | 212–220 | 使用者的字只放進標準輸入的「資料區塊」，不進指令列；拿掉偽造標籤、限制長度；規則放系統提示 |
| 防護 3：食物名稱當檔名前先清洗 | `src/food_agent/photos.py` | 62–66 | 清掉 `/ \ ..` 冒號等字，並確認檔案落在照片資料夾內 |
| 防護 4：Excel 文字不被當公式 | `src/food_agent/storage.py` | 47–53 | 以 `= + - @` 開頭的文字強制設為文字，並加單引號 |

註解格式：

```python
# ==== 防護 1：claude -p 的檔案範圍隔離　開始 ====
...
# ==== 防護 1 結束 ====
```

關掉某一段後會發生什麼（我用測試和真實的 `claude -p` 驗證過）：

- 防護 1 關掉：`claude -p` 在 DATA_DIR 裡工作，叫它讀 DATA_DIR 裡的檔案，它真的讀得到並說出內容；開著時它回答「讀不到」。
- 防護 2 關掉：使用者的字直接接在指令後面，會被當成指令的一部分。
- 防護 3 關掉：食物名稱 `../../x` 之類會讓檔案寫到照片資料夾之外，或在 Windows 上存檔失敗。
- 防護 4 關掉：`=1+1` 之類的文字，打開 Excel 會被當公式算。

## 測試

```powershell
uv run pytest
```

測試不需要 Discord，也不會呼叫 `claude -p`（不花額度）。涵蓋：JSON 檢查、設定檢查、檔名清洗與撞名、Excel 往下加與當日加總、Excel 被鎖住時重試、公式防護、回覆內容。

## 不要 commit 的東西

- `.env`（裡面有 `DISCORD_TOKEN`）
- `DATA_DIR` 裡的照片與 Excel（`data/`、`photos/`、`backup/`、`*.xlsx`、`*.log` 都已在 `.gitignore`）
