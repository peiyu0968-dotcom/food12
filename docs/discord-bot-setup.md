# Discord Bot 設定步驟（StyleMate AI）

> 這份文件帶你在 **Discord Developer Portal**（Discord 給開發者的後台網站）建立一個 bot，拿到它的登入密碼（**token**），並把它邀進你自己的 Server。
>
> 前提：
> - 你已經照 `docs/discord-server-setup.md` 做好 Server 和頻道。
> - 你的電腦是 **Windows**。
> - 你有 Discord 帳號。
>
> **畫面可能和文件有一點不同**：這個後台偶爾會改版。我在中文後面附英文名稱，找不到時請對照英文（後台大多是英文介面）。

---

## ⚠️ 先看這一段：bot token 是什麼、為什麼不能外洩

**Bot token 就是這個 bot 的密碼。** 誰拿到它，誰就能完全控制你的 bot：用它發訊息、讀它看得到的訊息，甚至在你的 Server 裡搗亂。

**絕對不要：**

- ❌ **貼給任何人**（同學、朋友、老師都一樣）
- ❌ **貼進跟 Claude 的對話**（這個對話、任何 AI 聊天都不行）
- ❌ **commit 進 git、上傳到 GitHub**（一旦上傳，即使馬上刪掉也看得到）
- ❌ 截圖時讓 token 入鏡
- ❌ 寫進 `docs/` 或任何文件、README

**只能放在一個地方：** 你電腦上 food12 資料夾裡的 `.env` 檔（這個檔案已被 `.gitignore` 擋住，不會被 commit）。

**萬一外洩了（包含不小心貼到對話裡）：** 不要慌，立刻照第四部分重新「Reset Token」（重設權杖）。舊的 token 馬上失效，別人拿到也沒用。

---

## 第一部分：建立 application 和 bot

**Application**（應用程式）是 Discord 後台裡「一個專案」的名稱；**bot** 是這個專案裡真正會登入聊天的那個機器人帳號。

1. 打開瀏覽器，網址列輸入 `https://discord.com/developers/applications`，按 Enter。
2. 如果要你登入，用你的 Discord 帳號登入。
3. 看畫面**右上角**，點藍紫色的按鈕 **「New Application」**（新增應用程式）。
4. 跳出視窗：
   - **Name** 欄位：打 bot 的名字，例如 `StyleMate`。**這個名字會顯示在 Discord 聊天裡**，其他人看得到。
   - 勾選下面同意條款的框框。
   - 點 **「Create」**。
5. 你會進到這個 application 的頁面。左邊有一排選單：General Information、Installation、OAuth2、Bot……。

---

## 第二部分：打開 Message Content Intent（一定要）

**Intent**（意向）是 Discord 的一種「權限開關」：bot 要先申請，Discord 才會把某些資料送給它。

### 為什麼一定要開

**Message Content Intent** 的意思是「允許 bot 讀訊息的內容」。沒開的話，Discord 會把一般訊息裡的**文字、附件（照片）都變成空的**後才給 bot。

對這個專案的影響：

- 使用者傳照片 → bot 看不到照片 → **根本沒東西可處理**
- 使用者 @bot 說話、打 `!衣櫃`，或回覆「全部」「1,3」來選擇衣服 → bot 看不到字 → **選擇、查詢、推薦全部失效**
- 而且這個狀況 bot 不會報錯，只會「像是沒反應」，很難找原因

所以這個開關一定要手動打開。

### 怎麼開

1. 在左邊選單，點 **「Bot」**。
2. 往下捲到 **「Privileged Gateway Intents」** 這一區（Privileged = 需要特別開啟的）。
3. 你會看到三個開關：
   - Presence Intent：**保持關閉**（用不到）
   - Server Members Intent：**保持關閉**（用不到）
   - **Message Content Intent：點開關，把它打開**（變成藍色／綠色）
4. 頁面下方（或上方）會跳出一個 **「Save Changes」**（儲存變更）的提示條，**點它**。
   - 如果沒按儲存，離開頁面後開關會恢復成關閉。

> 小型 bot（加入的 Server 少於 100 個）可以直接打開，不需要審核或申請。

---

## 第三部分：其他 Bot 頁面設定（在同一頁）

仍在左邊的 **「Bot」** 頁面：

1. 找到 **「Public Bot」**（公開 bot），**把開關關掉**。
   - 為什麼：關掉後只有你能把這個 bot 加到 Server。不然拿到連結的人都能把你的 bot 加進他們的 Server，你的 Claude 額度可能被別人用掉。
2. 找到 **「Requires OAuth2 Code Grant」**，**保持關閉**。
3. 點 **「Save Changes」**。

---

## 第四部分：拿到 bot token

> 再提醒一次：下面這串 token 只會顯示一次，**只能貼進 `.env`**。看一下第一部分的「⚠️」。

1. 還是在左邊的 **「Bot」** 頁面，往上找到 **「Token」** 區塊。
2. 點 **「Reset Token」**（重設權杖）。
3. 跳出確認視窗，點 **「Yes, do it!」**。
   - 如果你的帳號有開雙重驗證，會要你輸入驗證碼或密碼，照做。
4. 畫面會出現一串很長的亂碼，這就是 token。點旁邊的 **「Copy」**（複製）。
   - **只會顯示這一次。** 離開頁面就看不到了，只能再按一次 Reset Token 重新產生。
5. **不要貼到別的地方。** 直接做下一步，貼進 `.env`。

### 把 token 放進 `.env`

`.env` 是放設定的檔案，要放在你的專案資料夾（就是 `food12` 資料夾，和 `pyproject.toml` 放在同一層）裡。

1. 打開 **記事本**（Notepad）：按鍵盤的 Windows 鍵，打 `記事本`（或 `notepad`），按 Enter。
2. 輸入以下四行（**等號前後不要有空白，也不要加引號**）：

   ```
   DISCORD_TOKEN=貼上剛剛複製的 token
   CHANNEL_ID=貼上你在 server 文件裡拿到的頻道編號
   CLAUDE_MODEL=sonnet
   DATA_DIR=./data
   ```

   - 把「貼上……」那幾個字換成真正的內容，按 `Ctrl + V` 貼上。
   - `CLAUDE_MODEL` 與 `DATA_DIR` 先照上面填。以後想換再改。`DATA_DIR=./data` 的意思是專案資料夾底下的 `data` 資料夾，所有人的衣服照片與 Excel 都放在裡面，**已被 `.gitignore` 擋住，不會被 commit**。
3. 點左上角 **「檔案」→「另存新檔」**（File → Save As）。
4. 在跳出的視窗：
   - 左邊選到你的專案資料夾 `food12`。
   - **「存檔類型」**（Save as type）：選 **「所有檔案 (*.*)」**。（沒選的話會存成 `.env.txt`，程式讀不到。）
   - **「檔案名稱」**：打 **`.env`**（前面有一個點，後面什麼都沒有）。
   - 點「儲存」。
5. 打開專案資料夾 `food12`，確認有 `.env` 這個檔，**不是** `.env.txt`。
   - 如果看不到副檔名：在資料夾上方點「檢視」→ 勾選「副檔名」。

> 這個專案的 `.gitignore` 已經設定不追蹤 `.env`。做完之後可以在專案資料夾執行 `git status`，確認清單裡**沒有** `.env`。

### 如果不小心外洩了

不論是貼到對話、貼到群組、截圖、還是 commit 了：

1. 回到 `https://discord.com/developers/applications`，點進你的 application → **Bot** → **Reset Token**，產生新的。
2. 把新的 token 填進 `.env`。
3. 舊的立刻失效，不用擔心。

---

## 第五部分：把 bot 邀進你自己的 Server

邀請 bot 要先產生一條「邀請網址」，裡面包含：你要給 bot 哪些權限。

### 5-1 產生網址

1. 在左邊選單，點 **「OAuth2」**。
2. 往下捲到 **「OAuth2 URL Generator」**（網址產生器）。
3. **「Scopes」**（範圍）區：只勾 **`bot`**（讓它以機器人身分進 Server）。勾了以後，下面會多出一個「Bot Permissions」區。
   - 簡化版**沒有斜線指令**，所以不需要 `applications.commands`；你之前勾了也沒關係。
4. **「Bot Permissions」**（bot 的權限）區：只勾這五個。

   | 要勾的 | 為什麼 |
   |---|---|
   | **View Channels**（查看頻道） | 才看得到 `# stylemate` |
   | **Send Messages**（傳送訊息） | 才能回覆結果 |
   | **Read Message History**（讀取訊息歷史） | 才能用「回覆」的方式回應某一則訊息，也才能讀到被回覆的原訊息 |
   | **Add Reactions**（新增反應） | 收到照片時，bot 要在你的訊息上加 ⏳ 表示排隊中 |
   | **Attach Files**（附加檔案） | 查看單件衣服時，bot 要附上那件衣服的照片 |

   **其他一律不要勾（不需要 Manage Messages、Mention Everyone 等）。特別是 `Administrator`（管理員）絕對不要勾**：給了就等於把整個 Server 的鑰匙交出去。
5. 頁面最下面有 **「Generated URL」**（產生的網址），點旁邊的 **「Copy」**。
   - 這條網址**不是**密碼，可以放心貼在瀏覽器。但它能把 bot 加進 Server，所以也別亂貼給不認識的人。

### 5-2 用網址邀請

1. 打開瀏覽器新分頁，貼上剛剛複製的網址，按 Enter。
2. 畫面問 **「新增至伺服器」**（Add to Server）：下拉選單，選你剛建立的 Server（例如 `StyleMate 測試`）。
   - 如果下拉選單裡看不到，確認 Discord 登入的是同一個帳號，並且你是這個 Server 的擁有者。
3. 點 **「繼續」**（Continue）。
4. 下一個畫面列出剛才勾的五個權限，確認只有這五個，點 **「授權」**（Authorize）。
5. 可能會出現「我不是機器人」的驗證，照著點。
6. 看到成功的畫面。

### 5-3 確認 bot 進來了

1. 回到 Discord，點你的 Server。
2. 看 Server 右邊的成員清單（如果沒看到，點右上角的「顯示成員名單」圖示）。
3. 你會看到 bot 的名字，旁邊有一個 **`BOT`** 小標籤。
4. 目前它是**離線**（灰色）的，這是正常的。要等 agent 程式啟動它才會上線。

### 5-4 如果你的頻道是私人頻道

（照 `discord-server-setup.md` 預設不是私人頻道，這一小節可以跳過。）

1. 右鍵點 `# stylemate` → **「編輯頻道」**（Edit Channel）→ **「權限」**（Permissions）。
2. 點 **「新增成員或身分組」**（Add members or roles）→ 選 bot 的名字。
3. 確認 View Channel、Send Messages、Read Message History、Add Reactions、Attach Files 五項是綠色勾勾。
4. 儲存。

---

## 完成檢查

- [ ] Developer Portal 裡有你的 application，**Message Content Intent** 是開的，已按 Save Changes
- [ ] Public Bot 已關閉
- [ ] 邀請網址的 Scopes 有 `bot`，權限只有五個（沒有管理員）
- [ ] 專案資料夾 `food12` 裡的 `.env` 存在（不是 `.env.txt`），裡面有 `DISCORD_TOKEN` 和 `CHANNEL_ID`
- [ ] `git status` 的清單裡**沒有** `.env`
- [ ] Server 的成員清單裡看得到有 `BOT` 標籤的機器人（目前離線沒關係）
- [ ] 你沒有把 token 貼到任何地方（包含跟 Claude 的對話）

這些都完成後，agent 程式寫好、啟動時，bot 就會自動上線。

---

## 常見問題

| 狀況 | 可能原因 | 處理 |
|---|---|---|
| 啟動後程式說 `Improper token` 或登入失敗 | token 貼錯：多了空白、引號，或少複製一段 | 重新 Reset Token，再貼一次到 `.env`，等號前後不要有空白 |
| 啟動後程式說 `Privileged intent provided is not enabled` | Message Content Intent 沒開，或沒按 Save Changes | 回第二部分重新開，並按 Save Changes |
| bot 上線但對照片完全沒反應 | `CHANNEL_ID` 填錯；或 bot 在那個頻道沒有權限 | 重新複製頻道 ID；確認五個權限（5-4） |
| bot 上線但看不到訊息文字、照片 | Message Content Intent 沒開 | 同上 |
| bot 傳不出「只有你看得到的編號」私訊 | 使用者的 Discord 隱私設定不允許來自伺服器成員的私訊 | 使用者到「使用者設定」的隱私設定，打開「允許來自伺服器成員的私人訊息」；或用 `!衣櫃` 查看編號 |
| 邀請網址打開後下拉選單沒有我的 Server | 登入了別的 Discord 帳號，或不是 Server 擁有者 | 換成建立 Server 的那個帳號 |

---

## 使用者要知道的事

- 簡化版只有**公開頻道**可以操作：在 `# stylemate` 傳照片、打 `!` 開頭的指令。沒有斜線指令，也不能私訊 bot 下指令。
- 顯示衣櫃、單件資料、推薦穿搭前，bot 會先提醒「頻道所有人都看得到」，你確認才會顯示。
- 新加入衣服的編號，bot 會用「只有你看得到」的訊息告訴你（按按鈕時）或私訊你（用打字回覆時；需要你在 Discord 允許來自伺服器成員的私訊）。
- 每個按鈕都有「打字回覆」的替代方式，不方便按按鈕時可以用打字。
- 完整規則見 `docs/architecture.md` 第 3 節。
