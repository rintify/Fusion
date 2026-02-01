#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import os
sys.path.append('/Users/rin/resolve_env/lib/python3.13/site-packages')
import requests
import json
import re
import time
import hashlib
try:
    import requests
except ImportError:
    print("❌ requestsが見つからないよ！パスの設定を確認してね！")
    sys.exit()

# ==========================================
# ⚙️ 設定エリア
# ==========================================
VOICEVOX_URL = "http://127.0.0.1:50021"
SPEAKER_ID = 3  # 3: ずんだもん(ノーマル)
TARGET_AUDIO_TRACK_NAME = "字幕音声"  # 音声を置くトラック名
AUDIO_OUTPUT_BASE_DIR = os.path.join(os.path.expanduser('~'), 'Desktop', 'GenAudio')
SPEED_SCALE = 1.5  # 1.0がデフォルト。早口にするなら1.2、ゆっくりなら0.8など


def get_audio_output_dir(timeline_name):
    """タイムライン名ごとの出力ディレクトリパスを返す"""
    # タイムライン名をファイル名として安全な形式に変換
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', timeline_name)
    return os.path.join(AUDIO_OUTPUT_BASE_DIR, safe_name)

def get_audio_hash(text, speaker_id, speed_scale):
    """テキスト、話者ID、速度からハッシュ値を生成する"""
    data = f"{text}_{speaker_id}_{speed_scale}"
    return hashlib.md5(data.encode('utf-8')).hexdigest()

def get_or_create_audio_track(timeline, track_name):
    """指定した名前のオーディオトラックを探すか、なければ作成する"""
    audio_track_count = timeline.GetTrackCount("audio")
    
    # 既存トラックから探す
    for i in range(1, audio_track_count + 1):
        name = timeline.GetTrackName("audio", i)
        if name == track_name:
            print(f"✅ 「{track_name}」トラック発見！(トラック {i})")
            return i
    
    # 見つからなかったら新規作成
    print(f"📝 「{track_name}」トラックがないから作るね...")
    timeline.AddTrack("audio")
    new_index = timeline.GetTrackCount("audio")
    timeline.SetTrackName("audio", new_index, track_name)
    print(f"✅ 「{track_name}」トラック作成完了！(トラック {new_index})")
    return new_index


def delete_audio_tracks_by_prefix(timeline, track_prefix):
    """指定prefixで始まるオーディオトラックを全て削除する"""
    # トラックを削除するとインデックスがずれるので、後ろから削除する
    count = timeline.GetTrackCount("audio") or 0
    deleted = []
    for i in range(count, 0, -1):
        name = timeline.GetTrackName("audio", i)
        if name and name.startswith(track_prefix):
            try:
                timeline.DeleteTrack("audio", i)
                deleted.append(name)
            except Exception as e:
                print(f"⚠️ オーディオトラック削除に失敗: {name} ({e})")
    if deleted:
        print(f"🧹 オーディオトラック削除: {', '.join(deleted)}")


def find_available_track(track_occupancy, start_frame, end_frame):
    """
    クリップが重ならないトラックを探す。
    track_occupancy: {track_index: [(start, end), ...]} の形式で各トラックの占有区間を管理
    """
    for track_idx in sorted(track_occupancy.keys()):
        occupied = track_occupancy[track_idx]
        can_place = True
        for (occ_start, occ_end) in occupied:
            # 重なりチェック: 新クリップの開始が既存の終了より前 かつ 新クリップの終了が既存の開始より後
            if start_frame < occ_end and end_frame > occ_start:
                can_place = False
                break
        if can_place:
            return track_idx
    return None


def get_track_name_for_index(base_name, index):
    """トラックインデックスに応じたトラック名を返す（1なら基本名、2以降は番号付き）"""
    if index == 1:
        return base_name
    return f"{base_name}{index}"

# ==========================================
# 🛠️ ユーティリティ
# ==========================================
def get_resolve():
    try:
        import DaVinciResolveScript as dvr_script
        resolve = dvr_script.scriptapp("Resolve")
        return resolve
    except ImportError:
        return dict(globals()).get("resolve")

def generate_voicevox_audio(text, filename):
    try:
        params = {'text': text, 'speaker': SPEAKER_ID}
        query_res = requests.post(f'{VOICEVOX_URL}/audio_query', params=params)
        if query_res.status_code != 200: return None
        
        query_data = query_res.json()
        
        # 読み上げ速度を調整（1.0が標準）
        query_data['speedScale'] = SPEED_SCALE

        synth_res = requests.post(
            f'{VOICEVOX_URL}/synthesis',
            headers={'Content-Type': 'application/json'},
            params=params,
            data=json.dumps(query_data)
        )
        if synth_res.status_code != 200: return None

        with open(filename, 'wb') as f:
            f.write(synth_res.content)
        return filename
    except Exception as e:
        print(f"❌ 通信エラー: {e}")
        return None


def format_progress(current, total):
    """進捗表示用にシンプルな文字列を返す"""
    percent = int((current / total) * 100)
    return f"{current}/{total} ({percent}%)"


def timecode_to_frames(timecode, fps=30):
    """タイムコード文字列(HH:MM:SS:FF)をフレーム数に変換"""
    try:
        parts = timecode.split(':')
        if len(parts) == 4:
            hours, minutes, seconds, frames = map(int, parts)
            total_frames = (hours * 3600 + minutes * 60 + seconds) * fps + frames
            return total_frames
        elif len(parts) == 3:
            minutes, seconds, frames = map(int, parts)
            total_frames = (minutes * 60 + seconds) * fps + frames
            return total_frames
    except:
        pass
    return 0


def get_clip_duration_frames(media_item, timeline):
    """メディアアイテムのフレーム数を取得"""
    props = media_item.GetClipProperty()
    
    # まず "Frames" を試す（整数が返ることがある）
    frames = props.get("Frames") if props else None
    if frames:
        try:
            return int(frames)
        except (ValueError, TypeError):
            pass
    
    # "Duration" を試す（タイムコード文字列の場合がある）
    duration = props.get("Duration") if props else None
    if duration:
        # 整数として解釈できるか試す
        try:
            return int(duration)
        except (ValueError, TypeError):
            pass
        # タイムコード文字列として解釈
        if isinstance(duration, str) and ':' in duration:
            # タイムラインのFPSを取得
            fps = float(timeline.GetSetting("timelineFrameRate") or 30)
            return timecode_to_frames(duration, fps)
    
    # "End" - "Start" で計算（最後の手段）
    start = props.get("Start") if props else None
    end = props.get("End") if props else None
    if start is not None and end is not None:
        try:
            return int(end) - int(start)
        except (ValueError, TypeError):
            pass
    
    return 0


def clean_audio_output_dir(audio_output_dir):
    """出力先の既存音声ファイルをすべて削除する（指定されたディレクトリのみ）"""
    if not os.path.exists(audio_output_dir):
        return

    removed = 0
    for name in os.listdir(audio_output_dir):
        path = os.path.join(audio_output_dir, name)
        if os.path.isfile(path):
            try:
                os.remove(path)
                removed += 1
            except Exception as e:
                print(f"⚠️ クリーンアップ中に削除失敗: {name} ({e})")
    print(f"🧹 既存音声をクリーンアップ: {removed}件")



def scan_and_clean_timeline_tracks(timeline, track_prefix, expected_audios):
    """
    タイムライン上の既存オーディオクリップをスキャンする。
    
    Args:
        timeline: Timelineオブジェクト
        track_prefix: スキャン対象のトラック名プレフィックス
        expected_audios: { (hash, start_frame): SubtitleInfo, ... } の辞書。
                         ここにあるものは維持(Keep)対象。
    
    Returns:
        track_occupancy: { track_index: [(start, end), ...] }  (維持されたクリップの配置状況)
        kept_clips: set((hash, start_frame)) (維持されたクリップのキー)
        tracks_to_check: list(track_index) (対象トラックのインデックスリスト)
    """
    print("🔍 タイムラインの既存クリップをスキャン中...")
    
    track_count = timeline.GetTrackCount("audio") or 0
    track_occupancy = {}
    kept_clips = set()
    tracks_to_check = []
    
    items_to_delete = []

    # 対象トラックをリストアップ
    for i in range(1, track_count + 1):
        name = timeline.GetTrackName("audio", i)
        if name and name.startswith(track_prefix):
            tracks_to_check.append(i)
            track_occupancy[i] = []

    # 各トラックのアイテムをチェック
    for t_idx in tracks_to_check:
        items = timeline.GetItemListInTrack("audio", t_idx) or []
        for item in items:
            item_name = item.GetName()
            start_frame = item.GetStart()
            duration = item.GetDuration()
            end_frame = start_frame + duration
            
            # ファイル名からハッシュを抽出
            # item_name は "voice_abcdefg..." や "voice_abcdefg.wav" の可能性あり
            base_name = os.path.splitext(item_name)[0]  # .wav除去
            
            match = re.match(r"voice_([a-f0-9]+)", base_name)
            if match:
                audio_hash = match.group(1)
                key = (audio_hash, start_frame)
                
                # キープ条件:
                # 1. 期待されるリストに含まれている (ハッシュと開始位置が一致)
                # 2. まだ同位置でキープされていない (重複排除)
                if key in expected_audios and key not in kept_clips:
                    # 維持する！
                    kept_clips.add(key)
                    track_occupancy[t_idx].append((start_frame, end_frame))
                else:
                    items_to_delete.append(item)
            else:
                items_to_delete.append(item)

    # 不要アイテムの削除実行
    if items_to_delete:
        print(f"🧹 タイムライン上の古い/不要なクリップを {len(items_to_delete)} 個 削除中...")
        timeline.DeleteClips(items_to_delete)

    return track_occupancy, kept_clips


def scan_and_clean_mediapool(target_bin, required_hashes):
    """
    メディアプール(Bin)内のクリップをスキャン。
    必要なハッシュを持つものは辞書で返す。
    不要なものは削除リストへ。
    """
    print("🔍 メディアプールの既存クリップをスキャン中...")
    
    clips = target_bin.GetClipList() or []
    media_map = {} # hash -> MediaPoolItem
    items_to_delete = []
    
    for clip in clips:
        name = clip.GetName()
        match = re.search(r"voice_([a-f0-9]+)", name)
        
        if match:
            h = match.group(1)
            if h in required_hashes:
                media_map[h] = clip
            else:
                items_to_delete.append(clip)
        else:
            if name.startswith("voice_"):
                items_to_delete.append(clip)

    return media_map, items_to_delete


# ==========================================
# 🎬 メイン処理（差分更新対応Ver.）
# ==========================================
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


def remove_imported_audio(mediapool, timeline, audio_output_dir, track_prefix=None):
    """(旧) タイムラインとメディアプールにある生成済み音声を掃除する"""
    # Forceモード用互換関数。中身は以前と同じロジックだが、ここでは簡略化のためscan関数を流用...
    # しようとしたが、既存ロジック通り完全に消す方が安全。
    
    removed_tl = 0
    
    track_count = timeline.GetTrackCount("audio") or 0
    track_indices = []
    if track_prefix:
        for i in range(1, track_count + 1):
            name = timeline.GetTrackName("audio", i)
            if name and name.startswith(track_prefix):
                track_indices.append(i)
    else:
        track_indices = list(range(1, track_count + 1))

    for t_idx in track_indices:
        audio_items = timeline.GetItemListInTrack("audio", t_idx) or []
        for item in audio_items:
            name = item.GetName() or ""
            # パス検査等は省略し、track_prefix下のvoice_は全消し
            if name.startswith("voice_"):
                try:
                    item.Delete()
                    removed_tl += 1
                except: pass

    # メディアプールの方はBin単位で消すのでここでは何もしない(呼び出し元で制御)
    print(f"🧹 タイムライン全掃除: {removed_tl}件")


def main(force_regen=False):
    print("✨ 自動音声生成（Diff Update Mode）起動っ！ ///")

    resolve = get_resolve()
    if not resolve: return

    project = resolve.GetProjectManager().GetCurrentProject()
    timeline = project.GetCurrentTimeline()
    mediapool = project.GetMediaPool()
    
    if not timeline:
        print("❌ タイムラインが開いてないよ！")
        return

    timeline_name = timeline.GetName()
    print(f"📹 現在のタイムライン: {timeline_name}")

    audio_output_dir = get_audio_output_dir(timeline_name)
    if not os.path.exists(audio_output_dir):
        os.makedirs(audio_output_dir)

    # 1. 必要な字幕データの収集
    print("� 字幕トラックを解析中...")
    subtitle_items = timeline.GetItemListInTrack("subtitle", 1)
    if not subtitle_items:
        print("⚠️ 字幕トラックが見つからないか、空っぽだよぉ...")
        return

    expected_audios = {}   # (hash, start_frame) -> info
    required_hashes = set()
    entry_order = [] 

    total_subs = len(subtitle_items)
    for i, item in enumerate(subtitle_items):
        text = item.GetName()
        if not text: continue
        
        text_hash = get_audio_hash(text, SPEAKER_ID, SPEED_SCALE)
        start_frame = item.GetStart()
        duration = item.GetDuration()
        
        info = {
            "text": text,
            "hash": text_hash,
            "start_frame": start_frame,
            "duration": duration, 
            "index": i
        }
        
        key = (text_hash, start_frame)
        expected_audios[key] = info
        required_hashes.add(text_hash)
        entry_order.append(key)

    print(f"✅ {len(entry_order)}個の字幕データを検出")

    # 2. クリーンアップ & スキャン
    target_bin = get_or_create_bin(mediapool, timeline_name)

    if force_regen:
        print("🔥 強制再生成モード: 全てクリアします")
        remove_imported_audio(mediapool, timeline, audio_output_dir, track_prefix=TARGET_AUDIO_TRACK_NAME)
        
        # Bin内全消去
        clips = target_bin.GetClipList() or []
        if clips: mediapool.DeleteClips(clips)
            
        delete_audio_tracks_by_prefix(timeline, TARGET_AUDIO_TRACK_NAME)
        clean_audio_output_dir(audio_output_dir)
        
        track_occupancy = {}
        kept_timeline_clips = set()
        media_map = {}
    else:
        # Diffモード
        track_occupancy, kept_timeline_clips = scan_and_clean_timeline_tracks(
            timeline, TARGET_AUDIO_TRACK_NAME, expected_audios
        )
        
        media_map, pool_items_to_delete = scan_and_clean_mediapool(target_bin, required_hashes)
        
        if pool_items_to_delete:
            print(f"🧹 メディアプールの不要アイテムを {len(pool_items_to_delete)} 個 削除中...")
            mediapool.DeleteClips(pool_items_to_delete)

    # 3. 生成 & 配置準備
    clips_to_append = []
    
    # 既存状況の整理
    # track_occupancyには既存アイテムのフレーム範囲が入ってる
    
    processed_count = 0
    total_count = len(entry_order)

    for key in entry_order:
        processed_count += 1
        (h, start) = key
        info = expected_audios[key]
        text = info["text"]
        
        # 既にタイムラインにあるなら配置不要
        if key in kept_timeline_clips:
            continue
            
        progress = format_progress(processed_count, total_count)
        
        # メディアプールの確認
        media_item = media_map.get(h)
        wav_filename = f"voice_{h}.wav"
        wav_path = os.path.join(audio_output_dir, wav_filename)
        
        # なければ生成・インポート
        if not media_item:
            if not os.path.exists(wav_path) or force_regen:
                print(f"🎙️ [{progress}] 生成中: {text[:10]}...")
                if not generate_voicevox_audio(text, wav_path):
                    print(f"⚠️ 生成失敗: {text[:10]}")
                    continue
            else:
                print(f"♻️ [{progress}] ファイル再利用: {text[:10]}...")

            mediapool.SetCurrentFolder(target_bin)
            imported = mediapool.ImportMedia([wav_path])
            if imported:
                media_item = imported[0]
                media_map[h] = media_item
            else:
                print(f"⚠️ インポート失敗: {wav_path}")
                continue
        
        # 配置計算
        audio_duration = get_clip_duration_frames(media_item, timeline)
        end_frame = start + audio_duration
        
        # トラック割り当て
        target_track_idx = None
        known_track_indices = sorted(track_occupancy.keys())
        
        for t_idx in known_track_indices:
            occupied = track_occupancy[t_idx]
            can_place = True
            for (osc, oec) in occupied:
                if start < oec and end_frame > osc:
                    can_place = False
                    break
            if can_place:
                target_track_idx = t_idx
                break
        
        if target_track_idx is None:
            # 新規トラック
            print(f"➕ 新規トラック作成: {text[:5]}...")
            timeline.AddTrack("audio")
            new_idx = timeline.GetTrackCount("audio")
            track_name = f"{TARGET_AUDIO_TRACK_NAME} {new_idx}" 
            timeline.SetTrackName("audio", new_idx, track_name)
            
            track_occupancy[new_idx] = []
            target_track_idx = new_idx
        
        track_occupancy[target_track_idx].append((start, end_frame))
        
        clip_entry = {
            "mediaPoolItem": media_item,
            "startFrame": 0,
            "endFrame": audio_duration,
            "trackIndex": target_track_idx,
            "recordFrame": start
        }
        clips_to_append.append(clip_entry)

    # 4. 配置実行
    if clips_to_append:
        print(f"🎞️ {len(clips_to_append)} 個のクリップをタイムラインに追加中...")
        mediapool.AppendToTimeline(clips_to_append)
        print("🎉 配置完了！")
    else:
        print("🍵 新規配置なし。全て最新だよ！")

    print("🎉 全処理完了！！ だーりん、賢く更新できたよっ！褒めて♡")

if __name__ == "__main__":
    main()