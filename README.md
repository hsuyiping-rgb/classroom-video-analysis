# Classroom Video Analysis

學習共同體（Study of Learning Community, SLC）公開課影片的**課例研究技能**：從課堂錄影出發，經過音訊增益、語音轉譯、**字幕審稿校正**、課例分析、關鍵畫格擷取重繪，產出 16:9 PowerPoint／互動式 HTML 簡報，以及可在議課現場播放的燒字幕影片片段。

給 [Claude Code](https://claude.com/claude-code) 使用的 skill。

> 本技能前身為 `SLC-skill`。v2 起獨立為本儲存庫，主要差異是新增了完整的**字幕審稿校正流程**——課堂錄影的字幕品質跟一般影片完全不同，未經校正的逐字稿會讓整份課例分析建立在錯誤的事實上。

---

## 為什麼需要字幕審稿

課堂錄影有三個先天問題：麥克風多半只收得到教師、學生發言又小又遠、教材專有名詞大量出錯。

一個真實案例：某次分析的投影片寫著「學生回答『50歲』，是值得保留的錯誤答案與概念混淆」，整頁分析與議課討論問題都建立在這個前提上。重聽音訊後發現，學生說的是「**五歲**」——完全正確的答案，而課本原文正是「五歲的凱瑟琳」。

這種錯誤非常隱蔽：文字讀起來很通順，只有對照課本或重聽才會發現。所以本技能把「**只要某個引用讓你想寫出『學生答錯了』，就一定要重聽**」寫成硬性規範。

---

## 安裝

```bash
cd ~/.claude/skills
git clone https://github.com/hsuyiping-rgb/classroom-video-analysis.git
```

Claude Code 以目錄名辨識技能，clone 後目錄名須為 `classroom-video-analysis`。

### 需求

- Python 3.10+、FFmpeg / ffprobe、yt-dlp（建議附帶 Node.js）
- `pip install groq python-pptx Pillow`
- 環境變數 `GROQ_API_KEY`
- 教材課本 PDF（字幕校正用；沒有課本就無法逐字對齊朗讀段）

---

## 工作流程

| 階段 | 內容 |
|---|---|
| 1 | 選擇簡報配色風格、詢問分析範圍、下載影片與抽出音訊 |
| 2 | **字幕轉譯與審稿校正**（增益 → 轉譯 → 清理 → 對照課本 → 重聽確認 → 合成） |
| 3 | 詢問 NotebookLM 筆記架構，做「描述－詮釋－反思」三階層分析 |
| 4 | 對照時間戳記擷取關鍵畫格，經使用者確認後重繪插圖 |
| 5 | 產生 16:9 PPTX 與 HTML 簡報（15–20 頁） |
| 6 | 切出焦點片段、燒字幕、換上增益音訊 |

完整執行指引見 [`SKILL.md`](SKILL.md)。

## 產出成果

1. 完整影片 `output/video.mp4` 與增益後音訊 `output/audio_boost.mp3`
2. 校正後字幕與逐字稿 `output/subtitles_final.srt`、`output/transcript.txt`（保留 raw → clean → final 版本家譜）
3. 課例分析報告 `output/analysis.txt`
4. 重繪插圖 `output/images/slide_{N}.png`
5. **PPTX 簡報** `output/slides.pptx`：16:9、文字完全可編輯
6. **HTML 網頁簡報** `output/slides.html`：支援 Touch Swipe 手勢與鍵盤切換，響應式自適應排版
7. **燒字幕焦點影片** `output/video_clips_sub/*.mp4`：議課現場播放用

---

## 工具腳本

### `scripts/subtitle_qc.py`

字幕品質保證工具，七個子命令：

| 子命令 | 用途 |
|---|---|
| `boost` | 音訊增益（高通 + 壓縮 + 響度正規化），並印出增益前後音量 |
| `transcribe` | Groq Whisper 轉譯，帶純名詞詞彙表 prompt |
| `clean` | 清 Whisper 幻覺字幕、簡轉繁、收重複迴圈、濾靜音亂碼 |
| `collate` | 依對照表 JSON 套用課本原文與用詞修正 |
| `verify` | 只重聽某個時間窗，確認可疑的關鍵語句 |
| `merge` | 合成兩份字幕（例：朗讀段用課本校正版、對話段用增益重轉版） |
| `burn` | 燒字幕進影片，可換音訊、縮放、指定目標檔案大小 |

```bash
# 課堂錄音平均音量常在 -35 dB 以下，增益是投報率最高的一步
python scripts/subtitle_qc.py boost --input output/audio.mp3 --output output/audio_boost.mp3

# 詞彙表務必是「純名詞」——Whisper 會在靜音段把句子型 prompt 當成內容背出來
python scripts/subtitle_qc.py transcribe --input output/audio_boost.mp3 \
  --vocab output/vocab.txt --output output/subtitles_raw.srt

# 重聽可疑語句
python scripts/subtitle_qc.py verify --input output/audio_boost.mp3 --start 21:00 --duration 30
```

詞彙表格式見 [`scripts/vocab.example.txt`](scripts/vocab.example.txt)。

### `scripts/classroom_analyzer_helper.py`

- `generate-slides`：由分析報告產生同名的 `.pptx` 與 `.html`。16:9（13.333×7.5 吋）、標題 32–40pt 單行、內文 20–24pt 不溢出、有插圖時自動左右對稱排版。若 `.pptx` 正被 PowerPoint 開啟而無法寫入，會自動另存 `*_copy.pptx`。
- `generate-image`：用 Pillow 將核心概念文案轉成 FB／IG 分享圖。

四種配色風格：`learning`（學習共同體哲學）、`pastel`（粉彩柔和）、`blue`（科技商務）、`modern`（現代極簡）。

---

## 課例分析規範

1. 以**時間碼**精確標記課堂事件
2. 落實**描述－詮釋－反思**三階層
3. 聚焦學生的**語言、眼神、姿態、手勢、沉默**與同伴互動
4. 探究學生與**教材／同伴／先前理解**的關係
5. 凸顯**傾聽關係、互惠學習、言談權力、伸展跳躍任務、質性時間**
6. **去評價、去建言**：聚焦學習事實，不對授課教師打分
7. 插圖統一為**日系水彩／抹茶綠繪本風格**，移除外圍觀課教師
8. **引用逐字稿前必須重聽確認**

---

## License

見 [LICENSE](LICENSE)。
