#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import os
import re
import time

# ==========================================
# ⚙️ 設定エリア
# ==========================================
CHAR_DURATION_SEC = 0.13  # 1文字あたりの秒数
MAX_CHARS_PER_LINE = 40  # 1行の最大文字数（この文字数を目安に改行）
# 実行時の日時タグを生成
RUN_TAG = time.strftime("%Y%m%d_%H%M%S")
TEMP_SRT_PATH = os.path.join(os.path.expanduser('~'), 'Desktop', f'temp_split_subs_{RUN_TAG}.srt')

# ==========================================
# ⚙️ ユーティリティ
# ==========================================
def get_resolve():
    try:
        import DaVinciResolveScript as dvr_script
        resolve = dvr_script.scriptapp("Resolve")
        return resolve
    except ImportError:
        return dict(globals()).get("resolve")

def frames_to_srt_timecode(frames, fps):
    """フレーム数をSRT形式のタイムコード文字列(HH:MM:SS,mmm)に変換"""
    if frames < 0: frames = 0
    total_seconds = frames / fps
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)
    milliseconds = int((total_seconds - int(total_seconds)) * 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"

def get_or_create_bin(mediapool, bin_name):
    """Master内に指定名のbinを探すか、なければ作成して返す"""
    root = mediapool.GetRootFolder()
    
    # 既存のbinを探す
    subfolders = root.GetSubFolderList() or []
    for folder in subfolders:
        if folder.GetName() == bin_name:
            print(f"✅ bin「{bin_name}」発見！")
            mediapool.SetCurrentFolder(folder)
            return folder
    
    # 見つからなかったら新規作成
    print(f"📁 bin「{bin_name}」がないから作るね...")
    mediapool.SetCurrentFolder(root)
    new_bin = mediapool.AddSubFolder(root, bin_name)
    if new_bin:
        mediapool.SetCurrentFolder(new_bin)
        print(f"✅ bin「{bin_name}」作成完了！")
        return new_bin
    else:
        print(f"⚠️ bin作成に失敗、ルートを使うよ...")
        return root


def format_subtitle_text(text, max_chars=35):
    """
    指定文字数目安で、区切り文字で改行を入れる。
    区切り文字がない場合は無理に改行しない。
    """
    if len(text) <= max_chars:
        return text

    # 区切り文字で分割 (区切り文字は前の文に残す)
    # 区切り文字: 、 。 ♡ /// ！ ？ 
    # 正規表現: (///|[、。♡！？])
    parts = re.split(r'(///|[、。♡！？])', text)
    
    # 分割結果を再構成
    segments = []
    current_seg = ""
    for p in parts:
        current_seg += p
        # 区切り文字であればセグメント区切りとみなす
        # /// の場合も、1文字の区切り文字もどちらもマッチするのでここで判定
        if p == "///" or (len(p) == 1 and p in "、。♡！？"):
            segments.append(current_seg)
            current_seg = ""
    if current_seg:
        segments.append(current_seg)

    # 行ごとにまとめる
    lines = []
    current_line = ""
    
    for seg in segments:
        # 現在の行に追加してもmax_charsを超えない、または現在の行が空なら追加
        if not current_line:
            current_line = seg
        elif len(current_line) + len(seg) <= max_chars:
            current_line += seg
        else:
            # 超える場合は、現在の行を確定させて、新しい行にする
            lines.append(current_line)
            current_line = seg
            
    if current_line:
        lines.append(current_line)
        
    return "\n".join(lines)

def main():
    print("✨ 字幕分割＆再配置スクリプト起動っ！ (Safe Mode + Bin Support + Formatting) ///")
    
    resolve = get_resolve()
    if not resolve:
        print("❌ Resolveが見つからないよ！")
        return

    project = resolve.GetProjectManager().GetCurrentProject()
    timeline = project.GetCurrentTimeline()
    mediapool = project.GetMediaPool()
    
    if not timeline:
        print("❌ タイムラインが開いてないよ！")
        return

    timeline_name = timeline.GetName()
    print(f"📹 現在のタイムライン: {timeline_name}")
    
    fps = float(timeline.GetSetting("timelineFrameRate") or 30)
    print(f"⏱️ フレームレート: {fps}")

    # 1. 字幕トラックからクリップを取得
    print("🔍 タイムラインから字幕を読んでるよ...")
    
    # トラックタイプ "subtitle" の 1番目のトラックのアイテムを取得
    subtitle_items = timeline.GetItemListInTrack("subtitle", 1)
    
    if not subtitle_items:
        print("⚠️ 字幕トラックが見つからないか、空っぽだよぉ...")
        return

    total_items = len(subtitle_items)
    print(f"✅ {total_items}個の字幕クリップを発見！")

    # 新しい字幕データの構築
    srt_content = ""
    srt_index = 1
    
    # 全ての字幕クリップからテキストを収集して結合
    print("info: 複数の字幕クリップからテキストを収集中...")
    full_text_parts = []
    
    # 開始時間を基準にソート（念のため）
    subtitle_items.sort(key=lambda x: x.GetStart())
    
    # 開始フレームは最初のクリップのものを使う
    if not subtitle_items:
        print("⚠️ 字幕クリップが見当たらないよ。")
        return

    current_start_frame = subtitle_items[0].GetStart()
    
    for i, item in enumerate(subtitle_items):
        text = item.GetName()
        if text:
            # デバッグ用ログ: 何文字読み取れたか表示
            preview = text[:20].replace('\n', ' ') + "..." if len(text) > 20 else text.replace('\n', ' ')
            print(f"  📖 Clip {i+1}: {len(text)}文字 取得: 「{preview}」")
            full_text_parts.append(text)
        else:
            print(f"  ⚠️ Clip {i+1}: 文字が空っぽか、取得できなかったよ。")
            
    # 改行改行で合体
    full_text = "\n\n".join(full_text_parts)
    
    # 改行コードの正規化
    full_text = full_text.replace('\r\n', '\n').replace('\r', '\n')
    
    # 再び分割して処理
    parts = full_text.split('\n\n')
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
        
        # ここで、パーツ内のテキスト整形
        formatted_part = format_subtitle_text(part, max_chars=MAX_CHARS_PER_LINE)
        
        # ユーザー要望: 2回以上の改行が紛れ込んでいたら1回に置換
        formatted_part = re.sub(r'\n{2,}', '\n', formatted_part)
        
        # 文字数から長さを計算 (改行は含めず、純粋な文字数ベースの方が読みやすい？)
        # しかしlen(formatted_part)には改行も含まれるが、微々たる差なのでそのまま使う
        # 気になるなら .replace('\n', '') してカウントしてもいい
        char_count = len(formatted_part.replace('\n', ''))
        duration_sec = char_count * CHAR_DURATION_SEC
        
        # 最低でも数フレームは確保 (短すぎると見えない)
        duration_frames = int(duration_sec * fps)
        if duration_frames < 5: # 少なくとも5フレーム
            duration_frames = 5
        
        end_frame = current_start_frame + duration_frames
        
        # SRTフォーマット作成
        # 時間フォーマット: 00:00:00,000 --> 00:00:00,000
        start_tc = frames_to_srt_timecode(current_start_frame, fps)
        end_tc = frames_to_srt_timecode(end_frame, fps)
        
        srt_content += f"{srt_index}\n"
        srt_content += f"{start_tc} --> {end_tc}\n"
        srt_content += f"{formatted_part}\n\n"
        
        srt_index += 1
        current_start_frame = end_frame # 次のパーツは直後から開始

    # 2. SRTファイル書き出し
    print(f"💾 SRTファイルを保存中: {TEMP_SRT_PATH}")
    try:
        with open(TEMP_SRT_PATH, 'w', encoding='utf-8') as f:
            f.write(srt_content)
    except Exception as e:
        print(f"❌ SRTファイルの書き込みに失敗したよ: {e}")
        return

    # 3. 元の字幕クリップを削除
    print("🗑️ 元の字幕クリップを削除中...")
    try:
        if subtitle_items:
            time.sleep(0.5)
            timeline.DeleteClips(subtitle_items)
            print("✅ 削除完了！")
    except Exception as e:
        print(f"⚠️ 削除に失敗したかも: {e}")

    # 4. SRTをインポート（指定ビンへ）
    print("📂 インポート先のビンを準備中...")
    target_bin = get_or_create_bin(mediapool, timeline_name)
    
    print("📥 SRTをインポート中...")
    mediapool.SetCurrentFolder(target_bin)
    
    imported_items = mediapool.ImportMedia([TEMP_SRT_PATH])
    
    if imported_items:
        print(f"✅ インポート成功！ (Bin: {timeline_name})")
        print("⚠️ 【重要】メディアプールに追加されたSRTファイルを、")
        print("   手動でタイムラインにドラッグ＆ドロップしてください。")
    else:
        print("⚠️ SRTのインポートに失敗したかも？ 手動でインポート＆配置してね。")

if __name__ == "__main__":
    main()
