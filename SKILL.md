---
name: classroom-video-analysis
description: >-
  學習共同體公開課影片分析與 16:9 自動插圖簡報生成技能。協助使用者下載 YouTube 影片、以 Groq Whisper 語音轉譯並「對照教材課本原文校正字幕」、進行「描述-詮釋-反思」課例研究分析，並依據影片截圖重繪抹茶綠繪本插圖，最終生成可編輯文字的 16:9 PPTX 簡報與互動式 HTML 網頁簡報，以及可在議課現場播放的燒字幕影片片段。當使用者說「分析公開課影片」「課例研究」「觀課簡報」「校正字幕」「字幕對不上課文」「把字幕燒進影片」時使用。
---

# Classroom-video Analysis（學習共同體課例探究與簡報生成技能）

## Overview
本技能提供一整套「學習共同體（Study of Learning Community, SLC）」課例研究與簡報排版工作流。從課堂錄影出發，經過**音訊增益 → 語音轉譯 → 字幕校正審稿 → 課例分析 → 關鍵影像擷取與重繪 → 簡報生成 → 影片片段燒字幕**，產出可直接用於議課現場的成品。

> **v2 更新重點**：新增「Groq 審稿校正字幕」流程（第 2 階段）。課堂錄影的字幕品質跟一般影片完全不同——麥克風多半只收得到教師、學生發言又小又遠、教材專有名詞大量出錯——**未經校正的逐字稿會讓整份課例分析建立在錯誤的事實上**。這一段不是可選的美化步驟，是分析可信度的前提。

## 🎯 最終產出成品
1. **完整影片** (`output/video.mp4`) 與**增益後音訊** (`output/audio_boost.mp3`)
2. **校正後字幕與逐字稿** (`output/subtitles_final.srt`、`output/transcript.txt`)
3. **簡報（雙格式）**：`output/slides.pptx`（16:9、文字可編輯）與 `output/slides.html`（互動網頁簡報）
4. **燒字幕的焦點影片片段** (`output/video_clips_sub/*.mp4`)，供議課現場播放

---

## 📝 課例研究分析與插圖生成規範

執行本技能時必須嚴格遵守以下規範：

1. **時間戳記標記**：分析報告必須以影片**時間碼（如 `12:34`）**精確標記課堂事件與學生互動段落。
2. **三階層分析**：明確區分並落實**「描述－詮釋－反思」**三個層次。
3. **聚焦微觀行為**：聚焦學生的**語言、眼神、姿態、手勢、沉默**與**同伴互動**。
4. **探究多維關係**：分析學生與**教材**、與**同伴**、與**先前理解**之間的關係。
5. **SLC 哲學概念**：凸顯**傾聽關係、互惠學習、言談權力、伸展跳躍任務、質性時間**。
6. **去評價、去建言立場**：聚焦學生的「學習事實」，不對授課教師打分或指導。
7. **日系水彩／抹茶綠繪本風格生圖**：以 `generate_image` (GPTimage2) 重繪，畫面聚焦學生的學習、傾聽、指圖與共同推理；**完全移除外圍觀課教師**，授課教師僅在直接參與學生互動時保留。
8. **【v2 新增】引用逐字稿前必須確認**：簡報或分析報告中每一句放進引號的課堂語句，都必須來自**校正後**的字幕；只要該句要成為分析的立論基礎（例如「學生答錯了」「教師沒有追問」），就必須用 `subtitle_qc.py verify` 重聽該時間窗確認後才能寫進報告。

---

## Dependencies
- **Python 3.10+**、**FFmpeg/ffprobe**、**python-pptx**、**Pillow**、**yt-dlp**（建議附帶 Node.js）
- **groq**（`pip install groq`）與 `GROQ_API_KEY` 環境變數
- 教材課本 PDF（用於字幕校正；沒有課本就無法做第 2 階段的逐字對齊）

---

## 🎨 簡報色彩主題
執行時**必須主動提供四個選項讓使用者選擇**：
1. **`learning` 學習共同體哲學風格**：有機茶白背景、深松針綠標題、溫潤石板灰內文。
2. **`pastel` 粉彩柔和教育風**：暖白背景、暖深褐標題、柔黑內文。
3. **`blue` 科技商務藍風**：淡藍灰背景、深海軍藍標題、藍灰內文。
4. **`modern` 現代極簡黑白風**：極淡灰背景、曜石黑標題、中灰內文。

---

## Workflow (Agent 執行指引)

### 階段 1：風格選擇與影片準備
- **主動列出四種風格**引導使用者選擇。
- **先評估影片長度**，詢問要下載分析整段還是特定段落（引起動機／分組討論／發表分享）。
- 下載影片；私密影片需引導使用者匯出 `cookies.txt`。
  ```bash
  yt-dlp --cookies cookies.txt --js-runtimes node --remote-components ejs:github \
    -f "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]" -o output/video.mp4 "<URL>"
  ffmpeg -i output/video.mp4 -q:a 0 -map a output/audio.mp3
  ```

### 階段 2：字幕轉譯與審稿校正 【v2 新增，不可略過】

**先問使用者要教材**：「這堂課上的是哪一課？可以提供課本 PDF 嗎？」沒有課本，朗讀段就只能靠猜。

#### 2-1 音訊增益（課堂錄影幾乎一定要做）
```bash
python scripts/subtitle_qc.py boost --input output/audio.mp3 --output output/audio_boost.mp3
```
腳本會印出增益前後的平均音量。**平均音量低於 -30 dB 就是典型的課堂錄影**，學生發言基本上辨識不出來，一定要做。實測 -39.4 dB → -18.9 dB 後，討論段的辨識率有肉眼可見的改善。

#### 2-2 建立詞彙表並轉譯
依課本內容寫一份**純名詞**詞彙表（格式見 `scripts/vocab.example.txt`），特別要放入**課文中的數字**（最容易被聽錯）。
```bash
python scripts/subtitle_qc.py transcribe --input output/audio_boost.mp3 \
  --vocab output/vocab.txt --output output/subtitles_raw.srt --transcript output/transcript.txt
```
> ⚠️ **詞彙表絕不能寫成句子**。Whisper 在靜音段會把 prompt 當成內容背出來——實測用「請以繁體中文輸出。」當 prompt，該句在字幕裡出現八次以上。

#### 2-3 清理
```bash
python scripts/subtitle_qc.py clean --input output/subtitles_raw.srt --output output/subtitles_clean.srt
```
處理三類問題：**Whisper 訓練資料污染**（「字幕志愿者 李宗盛」「请不吝点赞 订阅…」）、**簡體字**、**重複迴圈與靜音段亂碼**。

#### 2-4 對照課本原文校正
讀課本 PDF，逐條比對**學生朗讀課文的段落**（通常在課堂前段，可以逐字對齊），寫成對照表：
```json
{
  "cues":  {"6": "許多受苦的孩子。", "11": "因為感染瘧疾而去世時，"},
  "terms": [["文帳","蚊帳"], ["海瑟琳","凱瑟琳"], ["異議段","意義段"]]
}
```
```bash
python scripts/subtitle_qc.py collate --input output/subtitles_clean.srt \
  --output output/subtitles_final.srt --map output/collate_map.json
```
- `cues`：只用在能逐字對齊的**朗讀段**，直接換成課本原文（含標點）。
- `terms`：只用在**無歧義的專有名詞**。**不要把學生的口語硬改成書面語**——口語的不完整正是課例分析的材料。

#### 2-5 重聽確認可疑語句
凡是「要拿來當分析立論基礎」的句子，都要重聽：
```bash
python scripts/subtitle_qc.py verify --input output/audio_boost.mp3 \
  --start 21:00 --duration 30 --vocab output/vocab.txt
```
> 🔴 **這一步救過一次重大錯誤**：某次分析寫著「學生回答『50歲』，是值得保留的錯誤答案與概念混淆」，整頁投影片與討論問題都建立在這個前提上。重聽後發現學生說的是「**五歲**」——完全正確的答案。Whisper 把「五歲」聽成「50歲」，而課本寫的正是「五歲的凱瑟琳」。**只要某個引用讓你想寫出「學生答錯了」，就一定要重聽。**

#### 2-6（選用）合成最佳版本
若朗讀段用課本校正版、對話段用增益重轉版效果更好：
```bash
python scripts/subtitle_qc.py merge --head output/subtitles_final.srt \
  --tail output/subtitles_boosted_clean.srt --output output/subtitles_final_v2.srt --split-at 09:06
```

### 階段 3：詢問 NotebookLM 與課例分析
- **必須主動詢問**：「請問您有沒有要連接 NotebookLM 的筆記來作為簡報分析的架構？如果有，請提供筆記名稱與內容。」
- 若使用者提供筆記，以其主題結構作為 `analysis.txt` 的主要架構，對齊**校正後**的逐字稿做三階層分析。
- 若 NotebookLM MCP 認證過期，請使用者在終端機執行 `nlm login`（需先完全關閉 Chrome，否則 CDP 啟動會失敗）；或請使用者直接提供檔案，通常更快。

### 階段 4：擷圖與二次確認重繪
- **段落語境對齊擷圖**：對照字幕時間戳記，找出最能反映該頁主題的時間點，用 `ffmpeg` 擷取 `screenshot_{N}.png`。
- **必須展示截圖請使用者確認**：「校長，這是為您擷取的公開課關鍵畫格（時間點：XX:XX），您是否滿意？」
- 確認後才用 `generate_image` 重繪為 `output/images/slide_{N}.png`。

### 階段 5：產生簡報
- **內容頁數規劃在 15-20 頁**。
```bash
uv run scripts/classroom_analyzer_helper.py generate-slides \
  --analysis output/analysis.txt --output output/slides.pptx --style learning
```

### 階段 6：焦點影片片段與燒字幕 【v2 新增】
議課時要能立刻播放對應片段，而且要**聽得見、看得懂**。
```bash
# 切出焦點片段
ffmpeg -ss 00:21:10 -to 00:21:53 -i output/video.mp4 -c copy output/video_clips/clip_03.mp4

# 燒字幕 + 換上增益音訊
python scripts/subtitle_qc.py burn --input output/video_clips/clip_03.mp4 \
  --srt output/subtitles_final_v2.srt --output output/video_clips_sub/clip_03_sub.mp4 \
  --start 1270 --audio output/audio_boost.mp3

# 過大的片段壓到指定大小（先縮放再燒字，字比較清楚）
python scripts/subtitle_qc.py burn --input output/video_clips/clip_04.mp4 \
  --srt output/subtitles_final_v2.srt --output output/video_clips_sub/clip_04_720p.mp4 \
  --start 1337 --audio output/audio_boost.mp3 --scale 1280:720 --font-size 14 --target-mb 30
```
- `--start` 是該片段**在完整影片中的起點秒數**，腳本用它把字幕時間軸平移對齊。
- **換用增益音訊是刻意的**：原本 -39 dB 的音量放給一整間會議室聽等於沒有。若使用者想呈現「現場真的很難聽清楚」，再改用原音。
- 簡報若已內嵌影片超連結，燒完字幕記得**把連結改指向 `_sub` 版本**並逐一驗證檔案存在。

---

## Utility Scripts

### `scripts/subtitle_qc.py`（v2 新增）
字幕品質保證工具。子命令：`boost`／`transcribe`／`clean`／`collate`／`verify`／`merge`／`burn`。
各參數見 `python scripts/subtitle_qc.py <子命令> --help`。

### `scripts/classroom_analyzer_helper.py`
- **`generate-slides`**：`--analysis`／`--output`／`--style`。16:9（13.333×7.5 吋）、標題 32-40pt 單行、內文 20-24pt 不溢出、有插圖時自動左右對稱排版。若 `slides.pptx` 正被 PowerPoint 開啟而無法寫入，會自動另存 `*_copy.pptx` 與 `*_copy.html`。
- **`generate-image`**：`--text`／`--output`／`--style`，用 Pillow 產生 FB/IG 分享圖。

---

## Common Mistakes

**字幕與事實**
* 🔴 **拿未校正的逐字稿直接寫分析**。這是最嚴重的錯誤——Whisper 的一個同音錯字（「五歲」→「50歲」）足以讓一整頁課例分析的立論完全站不住腳，而且錯得非常隱蔽：文字讀起來很通順，只有對照課本或重聽才會發現。
* **沒有先做音訊增益就抱怨學生的話聽不清楚**。課堂錄影平均音量常在 -35 dB 以下，增益是最高投報率的一步。
* **用句子當 Whisper 的 prompt**，導致靜音段被 prompt 內容汙染。只用純名詞詞彙表。
* **把學生的口語硬改成書面語**。校正的對象是「辨識錯誤」，不是「學生講得不夠好」——不完整的發言正是課例分析要保留的材料。
* **修正了字幕卻沒有回頭更新簡報**。字幕一改，所有引用該段落的投影片、分析稿、HTML 都要同步；改完要重新驗證簡報裡的影片超連結。

**流程與產出**
* **刪除影片或字幕檔**：`output/video.mp4` 與各版本字幕都是研究成果，工作流結束不得為了清理而刪除。保留字幕的**版本家譜**（raw → clean → final → final_v2），方便回溯每一次改動的依據。
* **未詢問 NotebookLM 筆記**就直接套用預設架構，導致簡報與使用者的教學研究脈絡不符。
* **未提供擷圖供確認**就直接重繪插圖。
* **頁數少於 15 頁**，未達課例研究的深度要求。
* **議課用的影片片段沒燒字幕**：現場投影時沒有字幕，觀課者聽不清學生說什麼，討論就無從聚焦。
