#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""subtitle_qc.py — 課堂錄影字幕品質保證工具（Groq 審稿校正流程）

課堂錄影的字幕跟一般影片不同：麥克風多半只收得到教師，學生發言又小又遠，
Whisper 在靜音段會產生幻覺，在教材專有名詞上會大量出錯。這支腳本把
「音訊增益 → 帶詞彙 prompt 轉譯 → 清理 → 對照教材校正 → 重聽確認 → 合成 → 燒字幕」
這條線包成可重跑的子命令。

子命令：
  boost       音訊增益（高通 + 壓縮 + 響度正規化），課堂錄音幾乎一定要做
  transcribe  Groq Whisper 轉譯，帶「純名詞詞彙表」prompt
  clean       清幻覺字幕、簡轉繁、收重複迴圈、濾靜音亂碼
  collate     依對照表（JSON）逐條套用教材原文與用詞修正
  verify      只重聽某個時間窗，用來確認可疑的關鍵語句
  merge       合成兩份字幕（例：朗讀段用教材校正版、對話段用增益重轉版）
  burn        把字幕燒進影片，可同時換音訊、縮放、指定目標檔案大小

需求：ffmpeg / ffprobe 在 PATH；GROQ_API_KEY 環境變數（transcribe、verify 才需要）。
"""
import argparse
import glob
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

# --------------------------------------------------------------------------
# SRT 基本讀寫
# --------------------------------------------------------------------------

def to_sec(t):
    h, m, rest = t.split(':')
    s, ms = rest.split(',')
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def stamp(x):
    x = max(0.0, x)
    ms = int(round(x * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'


def load_srt(path):
    """回傳 [(start, end, text), ...]"""
    raw = Path(path).read_text(encoding='utf-8').strip()
    cues = []
    for block in re.split(r'\n\s*\n', raw):
        lines = block.split('\n')
        if len(lines) < 3:
            continue
        a, z = lines[1].split(' --> ')
        cues.append((to_sec(a), to_sec(z), ' '.join(lines[2:]).strip()))
    return cues


def save_srt(path, cues):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        for i, (a, z, t) in enumerate(cues, 1):
            f.write(f'{i}\n{stamp(a)} --> {stamp(z)}\n{t}\n\n')
    print(f'寫入 {path}（{len(cues)} 條）')


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        sys.exit(f'ffmpeg/ffprobe 失敗：\n{r.stderr[-2000:]}')
    return r

# --------------------------------------------------------------------------
# boost：音訊增益
# --------------------------------------------------------------------------

def measure(path):
    r = subprocess.run(['ffmpeg', '-hide_banner', '-i', str(path), '-af', 'volumedetect',
                        '-f', 'null', '-'], capture_output=True, text=True)
    m = re.search(r'mean_volume:\s*(-?[\d.]+) dB', r.stderr)
    return float(m.group(1)) if m else None


def cmd_boost(a):
    before = measure(a.input)
    vf = (f'highpass=f={a.highpass},'
          f'acompressor=threshold={a.threshold}dB:ratio=4:attack=20:release=250:makeup=2,'
          f'loudnorm=I={a.target}:TP=-1.5:LRA=11')
    run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(a.input),
         '-af', vf, '-ar', '16000', '-ac', '1', '-b:a', '64k', str(a.output)])
    after = measure(a.output)
    print(f'平均音量 {before} dB -> {after} dB')
    print(f'輸出 {a.output}')
    if before is not None and before > -25:
        print('注意：原始音量已不算小，增益帶來的辨識改善可能有限。')

# --------------------------------------------------------------------------
# transcribe / verify：Groq
# --------------------------------------------------------------------------

def groq_client():
    try:
        from groq import Groq
    except ImportError:
        sys.exit('缺少 groq 套件：pip install groq')
    if not os.environ.get('GROQ_API_KEY'):
        sys.exit('請設定 GROQ_API_KEY 環境變數')
    return Groq()


def read_vocab(path):
    """詞彙表檔：每行一個詞或以空白分隔，註解行以 # 開頭。

    務必是「純名詞詞彙表」，不要寫成句子。Whisper 在靜音段會把 prompt
    當成內容背出來，句子型 prompt（例如「請以繁體中文輸出」）會整段汙染字幕。
    """
    if not path:
        return ''
    words = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        line = line.split('#')[0].strip()
        if line:
            words += line.split()
    if any(len(w) > 12 or w.endswith(('。', '！', '？')) for w in words):
        print('警告：詞彙表看起來含有句子，Whisper 可能在靜音段把它背出來。')
    return ' '.join(words)


def transcribe_file(client, path, prompt, model, language):
    with open(path, 'rb') as fh:
        r = client.audio.transcriptions.create(
            file=(os.path.basename(path), fh.read()), model=model, language=language,
            prompt=prompt, response_format='verbose_json',
            timestamp_granularities=['segment'])
    out = []
    for s in r.segments:
        s = s if isinstance(s, dict) else s.__dict__
        text = s['text'].strip()
        if text:
            out.append((float(s['start']), float(s['end']), text))
    return out


def cmd_transcribe(a):
    client = groq_client()
    prompt = read_vocab(a.vocab)
    tmp = Path(tempfile.mkdtemp(prefix='sqc_chunks_'))
    run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(a.input),
         '-f', 'segment', '-segment_time', str(a.chunk), '-c', 'copy',
         str(tmp / 'part_%03d.mp3')])
    cues, offset = [], 0.0
    for p in sorted(glob.glob(str(tmp / 'part_*.mp3'))):
        print('轉譯', os.path.basename(p), flush=True)
        for s, e, t in transcribe_file(client, p, prompt, a.model, a.language):
            cues.append((s + offset, e + offset, t))
        offset += a.chunk
    save_srt(a.output, cues)
    if a.transcript:
        Path(a.transcript).write_text(
            '\n'.join(f'[{stamp(s)}] {t}' for s, e, t in cues), encoding='utf-8', newline='\n')
        print(f'寫入 {a.transcript}')
    shutil.rmtree(tmp, ignore_errors=True)


def cmd_verify(a):
    """只重聽一個時間窗——用來確認「這句學生到底說了什麼」。"""
    client = groq_client()
    tmp = Path(tempfile.mkdtemp(prefix='sqc_verify_'))
    clip = tmp / 'w.mp3'
    run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', str(a.start),
         '-t', str(a.duration), '-i', str(a.input), str(clip)])
    print(f'=== {a.input} {a.start} 起 {a.duration} 秒 ===')
    for s, e, t in transcribe_file(client, clip, read_vocab(a.vocab), a.model, a.language):
        print(f'  [+{stamp(s)}] {t}')
    shutil.rmtree(tmp, ignore_errors=True)

# --------------------------------------------------------------------------
# clean：清理
# --------------------------------------------------------------------------

# Whisper 中文訓練資料的常見污染字串
HALLUCINATION = [
    r'请不吝点赞[\s、,，]*订阅[\s、,，]*转发[\s、,，]*打赏支持明镜与点点栏目',
    r'(?:谢谢观看)?(?:中文)?字幕志愿者\s*[一-鿿]{2,3}',
    r'(?:谢谢)?字幕志愿者\s*[一-鿿]{2,3}',
    r'(?:中文)?字幕提供',
    r'字幕(?:由)?.{0,6}提供',
    r'作词\s*[一-鿿]{2,3}',
    r'请订阅.{0,20}',
    r'Amara\.org.*',
]

S2T = dict(zip(
    "这谢来个状点请们记页题觉师课赞订阅转赏镜么现问说时学习对会讲买卖顶帐张让还没两异边处贝汉极务万妈义钱设计护争赠凯奖给写关图贴网贵见广账贤开应响许伞区带扰诚够仪场虽风纪团励况认将决虫传净难变于条动仅儿远离飞静几观层苏补组连标缩帮称样统复规签译颜线挡彻胜尽满书钟词扩罗马积黄强园种挥队气号费吨谅系楼盘险据厨进别话与栏术试领扑篮为类当专体过亲热梦着吗约纹红",
    "這謝來個狀點請們記頁題覺師課贊訂閱轉賞鏡麼現問說時學習對會講買賣頂帳張讓還沒兩異邊處貝漢極務萬媽義錢設計護爭贈凱獎給寫關圖貼網貴見廣賬賢開應響許傘區帶擾誠夠儀場雖風紀團勵況認將決蟲傳淨難變於條動僅兒遠離飛靜幾觀層蘇補組連標縮幫稱樣統復規簽譯顏線擋徹勝盡滿書鐘詞擴羅馬積黃強園種揮隊氣號費噸諒係樓盤險據廚進別話與欄術試領撲籃為類當專體過親熱夢著嗎約紋紅"))

CONTEXT = [('后来', '後來'), ('以后', '以後'), ('之后', '之後'), ('然后', '然後'),
           ('发挥', '發揮'), ('发言', '發言'), ('发现', '發現'), ('发生', '發生'),
           ('信里', '信裡'), ('这里', '這裡'), ('那里', '那裡'), ('哪里', '哪裡')]

COMMON = set('的是我你他她好來這那就有嗎呢很不對了個把說看寫要們也都在跟和')


def to_traditional(t):
    for a, b in CONTEXT:
        t = t.replace(a, b)
    return ''.join(S2T.get(ch, ch) for ch in t)


def collapse_repeats(t):
    """收掉 Whisper 的重複迴圈（「記性 看獎 記性 看獎 ...」）。"""
    tok = t.split()
    for size in (1, 2, 3):
        out, i = [], 0
        while i < len(tok):
            out += tok[i:i + size]
            j = i + size
            while tok[i:i + size] and tok[j:j + size] == tok[i:i + size]:
                j += size
            i = j
        tok = out
    return ' '.join(tok)


def make_noise_test(corpus_text):
    """靜音段的詞彙沙拉偵測。corpus_text 是這堂課的合理用字語料。"""
    corpus = set(re.sub(r'[\d\s:,>-]', '', corpus_text)) if corpus_text else set()

    def is_noise(t):
        tok = t.split()
        if len(tok) < 6:
            return False
        avg = statistics.mean(len(x) for x in tok)
        chars = t.replace(' ', '')
        common = sum(1 for ch in chars if ch in COMMON) / max(1, len(chars))
        unseen = (sum(1 for ch in chars if ch not in corpus) / max(1, len(chars))) if corpus else 0
        return avg <= 1.8 or (avg <= 2.6 and common < 0.12) or (corpus and len(chars) >= 6 and unseen >= 0.25)
    return is_noise


def parse_windows(values):
    """--drop 09:50-10:15 -> [(590.0, 615.0)]"""
    out = []
    for v in values or []:
        a, z = v.split('-')
        def p(x):
            parts = [float(y) for y in x.split(':')]
            s = 0.0
            for q in parts:
                s = s * 60 + q
            return s
        out.append((p(a), p(z)))
    return out


def cmd_clean(a):
    cues = load_srt(a.input)
    corpus = Path(a.corpus).read_text(encoding='utf-8') if a.corpus else ''
    is_noise = make_noise_test(corpus)
    drops = parse_windows(a.drop)
    hall = [re.compile(p) for p in HALLUCINATION]
    out, removed = [], []
    for s, e, t in cues:
        orig = t
        for r in hall:
            t = r.sub('', t)
        t = collapse_repeats(re.sub(r'[ \t]+', ' ', t).strip(' ,，。'))
        if a.traditional:
            t = to_traditional(t)
        t = t.strip()
        if not t:
            removed.append((s, orig, '幻覺'))
            continue
        if a.filter_noise and is_noise(t):
            removed.append((s, orig, '靜音亂碼'))
            continue
        if any(s < dz and e > da for da, dz in drops):
            removed.append((s, orig, '指定時間窗'))
            continue
        out.append((s, e, t))
    save_srt(a.output, out)
    print(f'刪除 {len(removed)} 條：')
    for s, t, why in removed:
        print(f'  [{stamp(s)}] ({why}) {t[:60]}')

# --------------------------------------------------------------------------
# collate：依教材原文校正
# --------------------------------------------------------------------------

def cmd_collate(a):
    """對照表 JSON：
       {
         "cues":  {"6": "許多受苦的孩子。", "7": "西元二○○六年，"},
         "terms": [["文帳", "蚊帳"], ["海瑟琳", "凱瑟琳"]]
       }
       cues 用在「學生朗讀教材」這種可以逐字對齊的段落；
       terms 只用在無歧義的專有名詞，避免把口語硬改成書面語。
    """
    spec = json.loads(Path(a.map).read_text(encoding='utf-8'))
    by_index = {int(k): v for k, v in spec.get('cues', {}).items()}
    terms = [tuple(x) for x in spec.get('terms', [])]
    cues, changed = load_srt(a.input), []
    out = []
    for i, (s, e, t) in enumerate(cues, 1):
        orig = t
        if i in by_index:
            t = by_index[i]
        else:
            for x, y in terms:
                t = t.replace(x, y)
        if t != orig:
            changed.append((i, orig, t))
        out.append((s, e, t))
    save_srt(a.output, out)
    print(f'校正 {len(changed)} 條：')
    for i, o, n in changed[:80]:
        print(f'  #{i}\n    - {o}\n    + {n}')
    if len(changed) > 80:
        print(f'  （其餘 {len(changed) - 80} 條略）')

# --------------------------------------------------------------------------
# merge：合成
# --------------------------------------------------------------------------

def split_long(a, z, t, maxlen):
    if len(t) <= maxlen or ' ' not in t:
        return [(a, z, t)]
    parts, cur = [], ''
    for w in t.split():
        if cur and len(cur) + len(w) > maxlen:
            parts.append(cur)
            cur = w
        else:
            cur = f'{cur} {w}'.strip()
    if cur:
        parts.append(cur)
    total, dur, t0, out = sum(len(p) for p in parts), z - a, a, []
    for p in parts:
        d = dur * len(p) / total
        out.append((t0, t0 + d, p))
        t0 += d
    return out


def cmd_merge(a):
    """head 檔負責 split 之前的時間，tail 檔負責之後的時間。"""
    split = parse_windows([f'{a.split_at}-{a.split_at}'])[0][0]
    head = [c for c in load_srt(a.head) if c[0] < split]
    last = head[-1][1] if head else 0.0
    tail = []
    for s, e, t in load_srt(a.tail):
        if e <= split:
            continue
        s = max(s, split, last)
        if e - s < 0.4:
            continue
        tail += split_long(s, e, t, a.max_chars)
    save_srt(a.output, head + tail)
    print(f'前段 {len(head)} 條 + 後段 {len(tail)} 條')

# --------------------------------------------------------------------------
# burn：燒字幕
# --------------------------------------------------------------------------

def cmd_burn(a):
    cues = load_srt(a.srt)
    start, end = a.start, a.start + a.duration if a.duration else None
    if end is None:
        r = run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=nw=1:nk=1', str(a.input)])
        end = start + float(r.stdout.strip())
    window = [(max(0, s - start), min(end, e) - start, t)
              for s, e, t in cues if s < end and e > start]
    tmp = Path(tempfile.mkdtemp(prefix='sqc_burn_'))
    (tmp / 's.srt').write_text(
        '\n\n'.join(f'{i}\n{stamp(x)} --> {stamp(y)}\n{t}'
                    for i, (x, y, t) in enumerate(window, 1)) + '\n',
        encoding='utf-8', newline='\n')

    style = (f'FontName={a.font},FontSize={a.font_size},PrimaryColour=&H00FFFFFF,'
             f'OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,'
             f'MarginV={a.margin},Alignment=2')
    # 先縮放再燒字幕：字是用輸出解析度直接畫上去的，比縮小已燒好的畫面清楚
    chain = f"[0:v]scale={a.scale}:flags=lanczos[s];[s]subtitles=s.srt:force_style='{style}'[v]" \
        if a.scale else f"[0:v]subtitles=s.srt:force_style='{style}'[v]"

    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
           '-i', str(Path(a.input).resolve())]
    if a.audio:  # 換上增益後的音訊
        cmd += ['-ss', str(start), '-t', str(end - start), '-i', str(Path(a.audio).resolve())]
    cmd += ['-filter_complex', chain, '-map', '[v]']
    cmd += ['-map', '1:a'] if a.audio else ['-map', '0:a?']
    cmd += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p']

    out = Path(a.output).resolve()
    if a.target_mb:  # 兩階段編碼，準確命中檔案大小
        dur = end - start
        vbit = int((a.target_mb * 8192 / dur) - 96)
        if vbit < 120:
            sys.exit(f'目標 {a.target_mb}MB 對 {dur:.0f} 秒影片太小，畫質會不堪用。')
        print(f'兩階段編碼：video {vbit}k + audio 96k')
        base = cmd + ['-b:v', f'{vbit}k', '-preset', 'slow']
        run(base + ['-pass', '1', '-an', '-f', 'null', '-'], cwd=tmp)
        run(base + ['-pass', '2', '-c:a', 'aac', '-b:a', '96k', '-shortest', str(out)], cwd=tmp)
    else:
        run(cmd + ['-crf', str(a.crf), '-preset', 'veryfast',
                   '-c:a', 'aac', '-b:a', '128k', '-shortest', str(out)], cwd=tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f'{out.name}  字幕 {len(window)} 條  {out.stat().st_size / 1048576:.1f}MB')

# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('boost', help='音訊增益')
    p.add_argument('--input', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--target', default='-16', help='loudnorm 目標 LUFS（預設 -16）')
    p.add_argument('--highpass', type=int, default=80)
    p.add_argument('--threshold', type=int, default=-30)
    p.set_defaults(func=cmd_boost)

    p = sub.add_parser('transcribe', help='Groq 轉譯')
    p.add_argument('--input', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--transcript')
    p.add_argument('--vocab', help='純名詞詞彙表檔（強烈建議提供）')
    p.add_argument('--chunk', type=float, default=480)
    p.add_argument('--model', default='whisper-large-v3-turbo')
    p.add_argument('--language', default='zh')
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser('verify', help='重聽某個時間窗')
    p.add_argument('--input', required=True)
    p.add_argument('--start', required=True, help='起點，可用 21:00 或秒數')
    p.add_argument('--duration', type=float, default=30)
    p.add_argument('--vocab')
    p.add_argument('--model', default='whisper-large-v3-turbo')
    p.add_argument('--language', default='zh')
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser('clean', help='清幻覺、簡轉繁、濾亂碼')
    p.add_argument('--input', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--corpus', help='合理用字語料（通常是已校正過的字幕檔）')
    p.add_argument('--drop', action='append', help='要整段刪掉的時間窗，例 09:50-10:15，可重複')
    p.add_argument('--no-traditional', dest='traditional', action='store_false')
    p.add_argument('--no-filter-noise', dest='filter_noise', action='store_false')
    p.set_defaults(func=cmd_clean, traditional=True, filter_noise=True)

    p = sub.add_parser('collate', help='依教材對照表校正')
    p.add_argument('--input', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--map', required=True, help='對照表 JSON')
    p.set_defaults(func=cmd_collate)

    p = sub.add_parser('merge', help='合成兩份字幕')
    p.add_argument('--head', required=True)
    p.add_argument('--tail', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--split-at', required=True, help='分界時間，例 09:06')
    p.add_argument('--max-chars', type=int, default=22)
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser('burn', help='燒字幕進影片')
    p.add_argument('--input', required=True)
    p.add_argument('--srt', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--start', type=float, default=0, help='本片段在完整影片中的起點（秒）')
    p.add_argument('--duration', type=float)
    p.add_argument('--audio', help='改用這個音訊（通常是增益後的檔案）')
    p.add_argument('--scale', help='例 1280:720')
    p.add_argument('--target-mb', type=float, help='目標檔案大小，會改用兩階段編碼')
    p.add_argument('--crf', type=int, default=22)
    p.add_argument('--font', default='Microsoft JhengHei')
    p.add_argument('--font-size', type=int, default=16)
    p.add_argument('--margin', type=int, default=24)
    p.set_defaults(func=cmd_burn)

    a = ap.parse_args()
    if a.cmd == 'verify':
        a.start = str(a.start)
    a.func(a)


if __name__ == '__main__':
    main()
