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


def delete_audio_track_by_name(timeline, track_name):
    """指定名のオーディオトラックを削除する（存在しない場合は何もしない）"""
    count = timeline.GetTrackCount("audio") or 0
    for i in range(1, count + 1):
        name = timeline.GetTrackName("audio", i)
        if name == track_name:
            try:
                timeline.DeleteTrack("audio", i)
                print(f"🧹 オーディオトラック削除: {track_name} (トラック {i})")
            except Exception as e:
                print(f"⚠️ オーディオトラック削除に失敗: {track_name} ({e})")
            break


def remove_imported_audio(mediapool, timeline, audio_output_dir, track_prefix=None):
    """タイムラインとメディアプールにある生成済み音声を掃除する（現在のタイムラインのフォルダのみ）"""
    removed_tl = 0
    removed_pool = 0

    # タイムライン上のクリップ削除（指定prefixのトラックを対象）
    track_count = timeline.GetTrackCount("audio") or 0
    track_indices = []
    
    if track_prefix:
        # prefixに合致するトラックのみ対象
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

            # タイムラインアイテムからメディアプールアイテムを取得し、そこからパスを取る
            clip = item.GetMediaPoolItem() if hasattr(item, "GetMediaPoolItem") else None
            props = clip.GetClipProperty() if clip and hasattr(clip, "GetClipProperty") else {}
            path = props.get("File Path", "") if isinstance(props, dict) else ""

            # 現在のタイムライン用フォルダの音声のみ削除
            if name.startswith("voice_") and path.startswith(audio_output_dir):
                try:
                    item.Delete()
                    removed_tl += 1
                except Exception as e:
                    print(f"⚠️ タイムライン音声削除に失敗: {name} ({e})")

    # メディアプール上のクリップ削除（現在のタイムライン用フォルダのみ）
    root = mediapool.GetRootFolder()
    clips = root.GetClipList()
    targets = []
    for clip in clips:
        props = clip.GetClipProperty()
        name = props.get("Clip Name", "") if props else ""
        path = props.get("File Path", "") if props else ""
        # 現在のタイムライン用フォルダの音声のみ削除
        if name.startswith("voice_") and path.startswith(audio_output_dir):
            targets.append(clip)

    if targets:
        try:
            mediapool.DeleteClips(targets)
            removed_pool = len(targets)
        except Exception as e:
            print(f"⚠️ メディアプール音声削除に失敗: {e}")

    print(f"🧹 タイムライン掃除: {removed_tl}件 / メディアプール掃除: {removed_pool}件")

# ==========================================
# 🎬 メイン処理（書き出し不要Ver.）
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


def remove_imported_audio_from_bin(mediapool, target_bin, audio_output_dir):
    """指定binにある生成済み音声を掃除する"""
    removed_pool = 0
    
    clips = target_bin.GetClipList() or []
    targets = []
    for clip in clips:
        props = clip.GetClipProperty()
        name = props.get("Clip Name", "") if props else ""
        path = props.get("File Path", "") if props else ""
        # 現在のタイムライン用フォルダの音声のみ削除
        if name.startswith("voice_") and path.startswith(audio_output_dir):
            targets.append(clip)

    if targets:
        try:
            mediapool.DeleteClips(targets)
            removed_pool = len(targets)
        except Exception as e:
            print(f"⚠️ メディアプール音声削除に失敗: {e}")

    print(f"🧹 メディアプール(bin)掃除: {removed_pool}件")
    return removed_pool



def main(force_regen=False):
    print("✨ 自動音声生成（Direct Mode）起動っ！ ///")

    
    resolve = get_resolve()
    if not resolve: return

    project = resolve.GetProjectManager().GetCurrentProject()
    timeline = project.GetCurrentTimeline()
    mediapool = project.GetMediaPool()
    
    if not timeline:
        print("❌ タイムラインが開いてないよ！")
        return

    # タイムライン名を取得
    timeline_name = timeline.GetName()
    print(f"📹 現在のタイムライン: {timeline_name}")

    # タイムラインごとの出力ディレクトリを決定
    audio_output_dir = get_audio_output_dir(timeline_name)

    # 保存先フォルダ作成
    if not os.path.exists(audio_output_dir):
        os.makedirs(audio_output_dir)
        print(f"📁 出力フォルダ作成: {audio_output_dir}")

        print(f"📁 出力フォルダ作成: {audio_output_dir}")

    # 強制再生成の場合のみ、既存の音声ファイルを全部消す
    if force_regen:
        print("🔥 強制再生成モード: 既存のキャッシュを削除するよ！")
        clean_audio_output_dir(audio_output_dir)
    else:
        print("♻️ キャッシュモード: 変更がないファイルは使い回すよ！")

    # 1. 字幕トラックを探して、クリップを直接引っこ抜く！
    # "subtitle" はAPIで字幕トラックを指定するキーワードだよ
    print("🔍 タイムラインから字幕を直接読んでるよ...")
    
    # トラックタイプ "subtitle" の 1番目のトラックのアイテムを取得
    subtitle_items = timeline.GetItemListInTrack("subtitle", 1)
    
    if not subtitle_items:
        print("⚠️ 字幕トラックが見つからないか、空っぽだよぉ...")
        return

    total_items = len(subtitle_items)
    print(f"✅ {total_items}個の字幕クリップを発見！")

    # Master内にタイムライン名のbinを作成または取得
    target_bin = get_or_create_bin(mediapool, timeline_name)

    # ==========================================
    # ♻️ 差分更新ロジック
    # ==========================================
    print("🔄 既存のトラックとクリップを確認中...")

    # 1. 既存の音声トラック情報を収集
    existing_tracks = {}
    occupancy = {}
    existing_clips = [] 

    track_count = timeline.GetTrackCount("audio") or 0
    for i in range(1, track_count + 1):
        t_name = timeline.GetTrackName("audio", i)
        if t_name.startswith(TARGET_AUDIO_TRACK_NAME):
            # トラック番号を推定 (字幕音声 -> 1, 字幕音声2 -> 2)
            suffix = t_name[len(TARGET_AUDIO_TRACK_NAME):]
            logical_id = 1 if suffix == "" else int(suffix) if suffix.isdigit() else None
            
            if logical_id:
                existing_tracks[logical_id] = i
                if logical_id not in occupancy:
                    occupancy[logical_id] = []
                
                # トラック上のクリップを収集
                items = timeline.GetItemListInTrack("audio", i) or []
                for item in items:
                    start = item.GetStart()
                    duration = item.GetDuration() 
                    end = start + duration
                    
                    mp_item = item.GetMediaPoolItem()
                    if mp_item:
                        props = mp_item.GetClipProperty()
                        file_path = props.get("File Path", "")
                        filename = os.path.basename(file_path)
                        
                        match = re.match(r"voice_([a-f0-9]+)\.wav", filename)
                        if match:
                             file_hash = match.group(1)
                             existing_clips.append({
                                 'item': item,
                                 'start': start,
                                 'end': end,
                                 'hash': file_hash,
                                 'track_index': i,
                                 'track_logic_id': logical_id,
                                 'is_valid': False 
                             })
    
    # 2. 字幕と既存クリップをマッチング
    clips_to_create = [] 
    
    for i, item in enumerate(subtitle_items):
        text = item.GetName()
        if not text: continue
        
        start_frame = item.GetStart()
        text_hash = get_audio_hash(text, SPEAKER_ID, SPEED_SCALE)
        
        found = False
        for ex_clip in existing_clips:
            if not ex_clip['is_valid']: 
                # 開始位置とハッシュが一致するか (許容誤差なし)
                if ex_clip['hash'] == text_hash and abs(ex_clip['start'] - start_frame) < 2:
                    ex_clip['is_valid'] = True
                    found = True
                    occupancy[ex_clip['track_logic_id']].append((ex_clip['start'], ex_clip['end']))
                    break
        
        if not found:
            clips_to_create.append({
                'item': item,
                'text': text,
                'hash': text_hash,
                'index': i
            })

    # 3. 不要なクリップ（ゴミ）を削除
    garbage_count = 0
    for ex_clip in existing_clips:
        if not ex_clip['is_valid']:
            try:
                ex_clip['item'].Delete()
                garbage_count += 1
            except:
                pass
    
    if garbage_count > 0:
        print(f"🧹 不要なクリップを削除しました: {garbage_count}件")

    # 4. 新規音声の生成と配置
    if not clips_to_create:
        print("🎉 全て同期済みだよ！追加処理なし！")
        return

    print(f"🎙️ 新規作成が必要なクリップ: {len(clips_to_create)}件")
    
    track_occupancy = occupancy if occupancy else {1: []}
    track_indices = existing_tracks.copy()
    
    clips_to_append = []

    for job in clips_to_create:
        item = job['item']
        text = job['text']
        text_hash = job['hash']
        
        wav_filename = f"voice_{text_hash}.wav"
        wav_path = os.path.join(audio_output_dir, wav_filename)
        
        media_item = None
        
        if not force_regen and os.path.exists(wav_path):
             mediapool.SetCurrentFolder(target_bin)
             imported = mediapool.ImportMedia([wav_path])
             if imported: media_item = imported[0]
        
        if not media_item:
            print(f"Generating: {text[:10]}...")
            if generate_voicevox_audio(text, wav_path):
                mediapool.SetCurrentFolder(target_bin)
                imported = mediapool.ImportMedia([wav_path])
                if imported: media_item = imported[0]
        
        if media_item:
            start_frame = item.GetStart()
            duration = get_clip_duration_frames(media_item, timeline)
            end_frame = start_frame + duration
            
            assigned_track = find_available_track(track_occupancy, start_frame, end_frame)
            
            if assigned_track is None:
                new_track_num = max(track_occupancy.keys()) + 1 if track_occupancy else 1
                assigned_track = new_track_num
                track_occupancy[assigned_track] = []

            track_occupancy[assigned_track].append((start_frame, end_frame))
            
            if assigned_track not in track_indices:
                t_name = get_track_name_for_index(TARGET_AUDIO_TRACK_NAME, assigned_track)
                track_indices[assigned_track] = get_or_create_audio_track(timeline, t_name)
            
            clips_to_append.append({
                "mediaPoolItem": media_item,
                "startFrame": 0,
                "endFrame": duration,
                "trackIndex": track_indices[assigned_track],
                "recordFrame": start_frame
            })

    if clips_to_append:
        print(f"🎞️ {len(clips_to_append)}個のクリップをタイムラインに追加するよ...")
        mediapool.AppendToTimeline(clips_to_append)
        print("🎉 更新完了！ だーりん、賢くやっといたよ♡")
    else:
        print("⚠️ 追加できたクリップはなかったみたい...")

if __name__ == "__main__":
    main()