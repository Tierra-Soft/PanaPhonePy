import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import subprocess
import datetime
import threading
import time
import warnings
import re
import requests

warnings.filterwarnings("ignore", category=UserWarning)
HISTORY_FILE = "folder_history.txt"
AMIVOICE_APPKEY = "B44F01E14C703D5E6CA2C30DE3F2EBA82C20D022DCA64E3C479BFA233CFDD430AF"

ULAW_TO_PCM = []
for i in range(256):
    c = ~i
    sign = -1 if (c & 0x80) else 1
    exponent = (c >> 4) & 0x07
    mantissa = c & 0x0F
    sample = (mantissa << 3) + 132
    sample <<= exponent
    sample -= 132
    ULAW_TO_PCM.append(sign * sample)

class FaxPlayerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Panasonic 電話/FAX 再生・マルチAI文字起こし")
        self.root.geometry("1150x850")
        
        self.selected_dir = tk.StringVar()
        self.current_process = None
        self.is_playing = False
        self.total_seconds = 0
        self.current_seconds = 0
        self.timer_thread = None
        self.playing_file_path = None
        
        self.current_peaks = []
        self.playback_start_time = 0.0
        self.seek_offset_seconds = 0.0
        
        saved_engine = self.load_saved_engine()
        self.ai_engine_var = tk.StringVar(value=saved_engine)
        self.whisper_model = None
        
        self.default_init_dir = os.path.expanduser("~/Documents")
        self.history_paths = self.load_history()
        self.create_widgets()
        
        if self.history_paths:
            last_path = self.history_paths[0]
            self.selected_dir.set(last_path)
            self.folder_combo.set(last_path)
            self.root.after(200, lambda: threading.Thread(target=self.load_files_recursive, args=(last_path,), daemon=True).start())

    def create_widgets(self):
        dir_frame = ttk.Frame(self.root, padding=10)
        dir_frame.pack(fill=tk.X)
        
        ttk.Label(dir_frame, text="対象フォルダ/SDカード:").pack(side=tk.LEFT, padx=5)
        self.folder_combo = ttk.Combobox(dir_frame, textvariable=self.selected_dir, width=55, state="normal")
        self.folder_combo['values'] = self.history_paths
        self.folder_combo.pack(side=tk.LEFT, padx=5)
        self.folder_combo.bind("<<ComboboxSelected>>", lambda event: self.on_history_selected())
        
        ttk.Button(dir_frame, text=" 参照... ", command=self.browse_folder).pack(side=tk.LEFT, padx=5)
        ttk.Button(dir_frame, text="🗑️ 履歴から削除", command=self.delete_current_history).pack(side=tk.LEFT, padx=5)
        
        ai_frame = ttk.LabelFrame(self.root, text=" 🎙️ 音声認識AIエンジンの選択 ", padding=10)
        ai_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Radiobutton(ai_frame, text="AmiVoice クラウド（国産電話特化・超高速・毎月60分無料）", 
                        variable=self.ai_engine_var, value="amivoice", command=self.on_engine_switched).pack(side=tk.LEFT, padx=20)
        ttk.Radiobutton(ai_frame, text="Whisper-turbo ローカル（完全無料・ネット通信なし・PC負荷高）", 
                        variable=self.ai_engine_var, value="whisper", command=self.on_engine_switched).pack(side=tk.LEFT, padx=20)
        
        list_frame = ttk.Frame(self.root, padding=10)
        list_frame.pack(fill=tk.BOTH, expand=True)
        
        columns = ("date_info", "filename", "duration", "transcription", "folder")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings")
        self.tree.heading("date_info", text="録音日時 (曜日) / 関連情報")
        self.tree.heading("filename", text="音声ファイル名 (.WAV)")
        self.tree.heading("duration", text="長さ")
        self.tree.heading("transcription", text="【選択AIで解析】文字起こし結果 (一行プレビュー)")
        self.tree.heading("folder", text="場所 (フォルダ)")
        
        self.tree.column("date_info", width=220, anchor=tk.W)
        self.tree.column("filename", width=120, anchor=tk.W)
        self.tree.column("duration", width=60, anchor=tk.CENTER)
        self.tree.column("transcription", width=530, anchor=tk.W)
        self.tree.column("folder", width=110, anchor=tk.W)
        
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.tree.bind("<Double-1>", lambda event: self.play_audio())
        self.tree.bind("<<TreeviewSelect>>", lambda event: self.on_file_selected_changed())
        self.tree.bind("<Alt-1>", lambda event: self.delete_single_transcription())

        detail_frame = ttk.LabelFrame(self.root, text=" 📝 選択中の文字起こし対話全文（時間・話者ごとのタイムライン表示）", padding=5)
        detail_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # 💡 ハイライトカラー用のタグ設定（薄い青色：#E6F2FF）
        self.detail_text = tk.Text(detail_frame, height=7, state=tk.DISABLED, bg="#FFFFFF", fg="#000000", font=("Menlo", 12), wrap=tk.WORD)
        self.detail_text.tag_configure("active_line", background="#E6F2FF", foreground="#000000")
        self.detail_text.pack(fill=tk.X, side=tk.LEFT, expand=True)
        detail_scroll = ttk.Scrollbar(detail_frame, orient=tk.VERTICAL, command=self.detail_text.yview)
        self.detail_text.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        player_frame = ttk.LabelFrame(self.root, text=" 💡 オーディオタイムライン波形（クリックするとその位置にジャンプして再生します） ", padding=10)
        player_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.time_label = ttk.Label(player_frame, text="00:00 / 00:00", font=("Menlo", 12, "bold"))
        self.time_label.pack(side=tk.RIGHT, padx=10)
        
        self.wave_canvas = tk.Canvas(player_frame, height=65, bg="#1a1a1a", highlightthickness=0, cursor="hand2")
        self.wave_canvas.pack(fill=tk.X, side=tk.BOTTOM, pady=(5, 0))
        self.wave_canvas.bind("<Button-1>", self.on_waveform_clicked)

        control_frame = ttk.Frame(self.root, padding=10)
        control_frame.pack(fill=tk.X)
        
        ttk.Button(control_frame, text="▶︎ 再生", command=self.play_audio).pack(side=tk.LEFT, padx=10)
        ttk.Button(control_frame, text="◼︎ 停止", command=self.stop_audio).pack(side=tk.LEFT, padx=10)
        ttk.Button(control_frame, text="🔄 選択ファイルを再認識", command=self.retranscribe_selected_file).pack(side=tk.LEFT, padx=10)
        ttk.Button(control_frame, text="🗑️ 全文字起こし削除", command=self.delete_all_transcriptions).pack(side=tk.LEFT, padx=20)
        
        self.status_label = ttk.Label(control_frame, text="SDカードのフォルダを選択してください。")
        self.status_label.pack(side=tk.RIGHT, padx=10)

    def log(self, message): self.status_label.config(text=message)
    def clear_log(self): pass

    def load_saved_engine(self):
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    for line in reversed(f.readlines()):
                        if line.startswith("LAST_ENGINE="):
                            engine = line.replace("LAST_ENGINE=", "").strip()
                            if engine in ["amivoice", "whisper"]: return engine
            except: pass
        return "amivoice"

    def save_current_engine(self):
        engine = self.ai_engine_var.get()
        lines = []
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    lines = [l for l in f.readlines() if not l.startswith("LAST_ENGINE=")]
            except: pass
        lines.append(f"LAST_ENGINE={engine}\n")
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f: f.writelines(lines)
        except: pass

    def load_history(self):
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    return list(dict.fromkeys([line.strip() for line in f.readlines() if line.strip() and not line.startswith("LAST_ENGINE=")]))
            except: pass
        return []

    def save_history(self, new_path):
        if not new_path: return
        if new_path in self.history_paths: self.history_paths.remove(new_path)
        self.history_paths.insert(0, new_path)
        self.history_paths = self.history_paths[:20]
        engine = self.ai_engine_var.get()
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                for path in self.history_paths: f.write(path + "\n")
                f.write(f"LAST_ENGINE={engine}\n")
            self.folder_combo['values'] = self.history_paths
            self.folder_combo.set(new_path)
        except: pass

    def delete_current_history(self):
        current_path = self.selected_dir.get()
        if not current_path or current_path not in self.history_paths: return
        if messagebox.askyesno("履歴削除の確認", f"以下のパスを履歴リストから削除しますか？\n\n{current_path}"):
            self.history_paths.remove(current_path)
            engine = self.ai_engine_var.get()
            try:
                with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                    for path in self.history_paths: f.write(path + "\n")
                    f.write(f"LAST_ENGINE={engine}\n")
                self.folder_combo['values'] = self.history_paths
                if self.history_paths:
                    next_path = self.history_paths[0]
                    self.selected_dir.set(next_path)
                    self.folder_combo.set(next_path)
                    self.refresh_list()
                else:
                    self.selected_dir.set("")
                    self.folder_combo.set("")
                    for item in self.tree.get_children(): self.tree.delete(item)
            except Exception as e: messagebox.showerror("エラー", f"履歴更新失敗:\n{e}")

    def browse_folder(self):
        folder = filedialog.askdirectory(title="PanasonicのSDカードを選択", initialdir=self.default_init_dir)
        if folder:
            self.selected_dir.set(folder)
            self.folder_combo.set(folder)
            self.save_history(folder)
            self.refresh_list()

    def on_history_selected(self):
        folder = self.folder_combo.get()
        if folder:
            self.selected_dir.set(folder)
            self.save_history(folder)
            self.refresh_list()

    def on_engine_switched(self):
        self.save_current_engine()
        self.refresh_list()

    def refresh_list(self):
        folder = self.selected_dir.get()
        if not folder: return
        for item in self.tree.get_children(): self.tree.delete(item)
        self.stop_audio()
        self.detail_text.config(state=tk.NORMAL)
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.config(state=tk.DISABLED)
        threading.Thread(target=self.load_files_recursive, args=(folder,), daemon=True).start()

    def get_audio_info(self, file_path):
        try:
            result = subprocess.run(["afinfo", file_path], capture_output=True, text=True, check=True)
            import re
            match = re.search(r"estimated duration:\s+([\d.]+)\s+sec", result.stdout)
            if match:
                seconds = float(match.group(1))
                return seconds, f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"
        except: pass
        return 0, "--:--"

    def preprocess_phone_audio_via_ffmpeg(self, input_path):
        temp_output = input_path + ".temp.wav"
        try:
            cmd = ["ffmpeg", "-i", input_path, "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", "-loglevel", "quiet", "-y", temp_output]
            subprocess.run(cmd, check=True)
            return temp_output
        except:
            if os.path.exists(temp_output): os.remove(temp_output)
            return input_path

    def clean_japanese_text(self, text):
        if not text: return ""
        try:
            from janome.tokenizer import Tokenizer
            t = Tokenizer()
            fillers = ["あの", "えっと", "えーと", "まあ", "なんか", "その", "あー", "えー"]
            words = []
            for token in t.tokenize(text):
                if token.surface in fillers or token.part_of_speech.startswith("感動詞"): continue
                words.append(token.surface)
            cleaned = "".join(words)
            cleaned = re.sub(r'、+', '、', cleaned)
            cleaned = re.sub(r'。+', '。', cleaned)
            return cleaned if cleaned else text
        except: return text

    def request_amivoice_transcribe(self, audio_path):
        url = "https://acp-api.amivoice.com/v1/recognize"
        payload = {
            'd': 'codec=8K grammar=-a-general keepFillerToken=0 profileWords=パナソニック',
            'u': AMIVOICE_APPKEY
        }
        try:
            with open(audio_path, 'rb') as f: audio_binary = f.read()
            files = {'a': ('audio.wav', audio_binary, 'audio/wav')}
            response = requests.post(url, data=payload, files=files, timeout=90)
            if response.status_code == 200:
                data = response.json()
                extracted_tokens = []
                if "results" in data:
                    res_node = data["results"]
                    if isinstance(res_node, list):
                        for r in res_node:
                            if "tokens" in r: extracted_tokens.extend(r["tokens"])
                    elif isinstance(res_node, dict) and "tokens" in res_node: extracted_tokens = res_node["tokens"]
                elif "tokens" in data: extracted_tokens = data["tokens"]
                if extracted_tokens: return extracted_tokens
                
                raw_text = data.get("text", "")
                if not raw_text and "results" in data:
                    res_node = data["results"]
                    if isinstance(res_node, list) and len(res_node) > 0: raw_text = res_node[0].get("resultText", "")
                    elif isinstance(res_node, dict): raw_text = res_node.get("resultText", "")
                if raw_text:
                    sentences = re.split(r'[。、\s\n]+', raw_text)
                    return [{"starttime": idx * 3000, "written": s.strip()} for idx, s in enumerate(sentences) if s.strip()]
        except: pass
        return []

    def format_timeline_lines(self, segments, is_amivoice, total_duration):
        timeline_lines = []
        current_speaker, has_started = "A", False
        for idx, seg in enumerate(segments):
            if is_amivoice:
                start = float(seg.get("starttime", 0)) / 1000.0
                text = seg.get("written", "").strip()
            else:
                start = seg.get("start", 0.0)
                text = seg.get("text", "").strip()
            if not text: continue
            if idx < 2 and start < 4.0 and any(x in text for x in ["つ", "ぷ", "ツ", "プ", "トゥ"]): text, speaker_label = "[発信音 (接続中...)]", "🤖"
            elif (total_duration - start) < 3.5 and any(x in text for x in ["つ", "ぷ", "ツ", "プ"]): text, speaker_label = "[話中音 (通話切断)]", "🤖"
            else:
                text = self.clean_japanese_text(text)
                if not text: continue
                if not has_started: current_speaker, has_started = "A", True
                else: current_speaker = "B" if current_speaker == "A" else "A"
                speaker_label = current_speaker
            timeline_lines.append(f"[{f'{int(start // 60):02d}:{int(start % 60):02d}'}] {speaker_label}: {text}")
        return "\n".join(timeline_lines)

    def load_files_recursive(self, root_folder_path):
        if not os.path.exists(root_folder_path): return
        current_mode = self.ai_engine_var.get()
        weeks = ["月", "火", "水", "木", "金", "土", "日"]
        all_wav_data = []
        for dirpath, dirnames, filenames in os.walk(root_folder_path):
            wav_files = [f for f in filenames if f.upper().endswith('.WAV') and not f.startswith('.') and not f.endswith('.temp.wav')]
            for wav in wav_files:
                full_wav_path = os.path.join(dirpath, wav)
                mtime = os.path.getmtime(full_wav_path)
                dt = datetime.datetime.fromtimestamp(mtime)
                mtime_str = dt.strftime(f'%Y-%m-%d ({weeks[dt.weekday()]}) %H:%M:%S')
                seconds, duration_str = self.get_audio_info(full_wav_path)
                
                rel_folder = os.path.relpath(dirpath, root_folder_path)
                if rel_folder == ".": rel_folder = "（直下）"
                base_name = os.path.splitext(wav)[0]
                
                txt_path = os.path.join(dirpath, base_name + ".TXT")
                info_text = ""
                if os.path.exists(txt_path):
                    try:
                        with open(txt_path, 'r', encoding='cp932', errors='ignore') as f: info_text = f.read().replace('\n', ' ').strip()
                    except: pass
                info_text = f"{info_text} [{mtime_str}]" if info_text else f"日時: {mtime_str}"
                
                all_wav_data.append({
                    "mtime": mtime, "wav": wav, "seconds": seconds, "duration": duration_str,
                    "rel_folder": rel_folder, "info_text": info_text, "full_path": full_wav_path,
                    "dirpath": dirpath, "base_name": base_name
                })
        
        if not all_wav_data: return
        all_wav_data.sort(key=lambda x: x["mtime"], reverse=True)
        for data in all_wav_data:
            txt_save_path = os.path.join(data["dirpath"], data["base_name"] + f".transcription.{current_mode}.txt")
            initial_status = "📄 保存済みデータを読込中..." if os.path.exists(txt_save_path) else f"📄 AI（{current_mode.upper()}）の解析待ち..."
            self.tree.insert("", tk.END, values=(data["info_text"], data["wav"], data["duration"], initial_status, data["rel_folder"]), tags=(data["full_path"],))

        if current_mode == "whisper":
            need_whisper = any(not os.path.exists(os.path.join(d["dirpath"], d["base_name"] + ".transcription.whisper.txt")) for d in all_wav_data if d["seconds"] > 0.5)
            if need_whisper and self.whisper_model is None:
                try: import whisper; self.whisper_model = whisper.load_model("turbo")
                except: return

        for index, data in enumerate(all_wav_data):
            txt_save_path = os.path.join(data["dirpath"], data["base_name"] + f".transcription.{current_mode}.txt")
            text_result = ""
            if os.path.exists(txt_save_path):
                try:
                    with open(txt_save_path, 'r', encoding='utf-8') as f: text_result = f.read().strip()
                except: pass
            
            if not text_result and data["seconds"] > 0.5:
                if current_mode == "amivoice":
                    try:
                        tokens = self.request_amivoice_transcribe(data["full_path"])
                        text_result = self.format_timeline_lines(tokens, True, data["seconds"])
                    except: pass
                elif current_mode == "whisper" and self.whisper_model:
                    optimized_wav = self.preprocess_phone_audio_via_ffmpeg(data["full_path"])
                    try:
                        result = self.whisper_model.transcribe(optimized_wav, language="ja", fp16=False, initial_prompt="これは2人の電話録音です。")
                        text_result = self.format_timeline_lines(result.get("segments", []), False, data["seconds"])
                    except: pass
                    finally:
                        if optimized_wav != data["full_path"] and os.path.exists(optimized_wav): os.remove(optimized_wav)
                if text_result:
                    with open(txt_save_path, 'w', encoding='utf-8') as f: f.write(text_result)
            if not text_result:
                if "用件" in data["info_text"] or "通話" in data["info_text"]: text_result = f"({data['info_text'].split('[')[0].strip()})"
                else: text_result = "（通話内容なし）"
            self.root.after(0, lambda idx=index, txt=text_result: self.update_tree_item(idx, txt))

    def update_tree_item(self, index, text):
        children = self.tree.get_children()
        if index < len(children):
            item_id = children[index]
            current_values = self.tree.item(item_id, "values")
            self.tree.item(item_id, values=(current_values[0], current_values[1], current_values[2], text.replace('\n', '  '), current_values[4]))
            original_tags = self.tree.item(item_id, "tags")
            if original_tags: self.tree.item(item_id, tags=(original_tags[0], text))
            selected = self.tree.selection()
            if selected and selected[0] == item_id: self.show_full_transcription()

    def on_file_selected_changed(self):
        self.show_full_transcription()
        selected_item = self.tree.selection()
        if not selected_item: return
        tags = self.tree.item(selected_item[0], "tags")
        if not tags or not tags[0]: return
        
        file_path = tags[0]
        if not self.is_playing:
            self.seek_offset_seconds = 0.0
        threading.Thread(target=self.generate_and_cache_waveform, args=(file_path,), daemon=True).start()

    def generate_and_cache_waveform(self, file_path):
        if not os.path.exists(file_path): return
        try:
            with open(file_path, 'rb') as f:
                f.seek(0, 2)
                total_bytes = f.tell()
                data_start_offset = 44
                if total_bytes <= data_start_offset: return
                f.seek(data_start_offset)
                raw_samples = f.read()
                
            total_samples = len(raw_samples)
            self.root.update_idletasks()
            canvas_width = max(self.wave_canvas.winfo_width(), 100)
            step = max(total_samples // canvas_width, 1)
            
            peaks = []
            for i in range(0, total_samples, step):
                chunk = raw_samples[i:i+step]
                if not chunk: break
                max_v = 0
                for b in chunk:
                    v = abs(ULAW_TO_PCM[b])
                    if v > max_v: max_v = v
                peaks.append(max_v)
                
            self.current_peaks = peaks
            init_pct = (self.seek_offset_seconds / self.total_seconds * 100.0) if self.total_seconds > 0 else 0.0
            self.root.after(0, lambda: self.redraw_waveform_timeline(init_pct))
        except: pass

    def redraw_waveform_timeline(self, progress_percent):
        self.wave_canvas.delete("all")
        canvas_width = max(self.wave_canvas.winfo_width(), 100)
        canvas_height = 65
        mid_y = canvas_height // 2
        
        for x, max_amp in enumerate(self.current_peaks):
            if x >= canvas_width: break
            pct = max_amp / 32635.0
            line_h = max(int(pct * (canvas_height - 10)), 2)
            y1 = mid_y - (line_h // 2)
            y2 = mid_y + (line_h // 2)
            
            current_x_percent = (x / canvas_width) * 100.0
            if current_x_percent <= progress_percent:
                color = "#00d2ff" if (y2 - y1) > 2 else "#226677"
            else:
                color = "#39FF14" if (y2 - y1) > 2 else "#334433"
                
            self.wave_canvas.create_line(x, y1, x, y2, fill=color)
            
        cursor_x = int((progress_percent / 100.0) * canvas_width)
        self.wave_canvas.create_line(cursor_x, 0, cursor_x, canvas_height, fill="#ff3333", width=2)
        self.wave_canvas.create_oval(cursor_x-4, mid_y-4, cursor_x+4, mid_y+4, fill="#ff3333", outline="#ffffff")

    def on_waveform_clicked(self, event):
        if not self.current_peaks: return
        canvas_width = max(self.wave_canvas.winfo_width(), 100)
        click_x = event.x
        click_percent = min(max((click_x / canvas_width) * 100.0, 0.0), 100.0)
        
        selected_item = self.tree.selection()
        if not selected_item: return
        tags = self.tree.item(selected_item[0], "tags")
        if not tags or not tags[0]: return
        file_path = tags[0]
        
        duration, _ = self.get_audio_info(file_path)
        if duration == 0: return
        self.total_seconds = duration
        
        target_seconds = (click_percent / 100.0) * duration
        self.seek_offset_seconds = target_seconds
        
        cur_time_str = f"{int(target_seconds // 60):02d}:{int(target_seconds % 60):02d}"
        total_time_str = f"{int(duration // 60):02d}:{int(duration % 60):02d}"
        self.update_player_timeline_ui(click_percent, f"{cur_time_str} / {total_time_str}")
        
        if self.is_playing:
            self.play_audio_from_seconds(file_path, target_seconds)

    def show_full_transcription(self):
        """💡 テキスト全文をエリアに流し込む（自動追跡スクロールのために現在表示中の状態を初期化）"""
        selected_item = self.tree.selection()
        if not selected_item: return
        tags = self.tree.item(selected_item[0], "tags")
        full_text = tags[1] if len(tags) > 1 else self.tree.item(selected_item[0], "values")[3]
        
        self.detail_text.config(state=tk.NORMAL)
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.insert(tk.END, full_text)
        self.detail_text.config(state=tk.DISABLED)
        # 選択変更時はスクロール追跡をリセット
        self.sync_text_scroll_to_time(0.0)

    def sync_text_scroll_to_time(self, elapsed_seconds):
        """💡 【本日の主役】現在の再生秒数に一致するタイムコードをテキスト内から全検索し、スクロール＆ハイライトする"""
        self.detail_text.config(state=tk.NORMAL)
        # 過去の古いハイライトタグを一旦すべてクリア
        self.detail_text.tag_remove("active_line", "1.0", tk.END)
        
        # テキスト全体の行数を取得
        total_lines = int(self.detail_text.index('end-1c').split('.')[0])
        best_match_line = None
        closest_diff = 9999.0
        
        # 1行ずつ解析し、行頭の [分:秒] のスタンプを抽出して現在の再生時間と比較
        for line_num in range(1, total_lines + 1):
            line_content = self.detail_text.get(f"{line_num}.0", f"{line_num}.end")
            match = re.match(r"^\[(\d+):(\d+)\]", line_content)
            if match:
                st_min = int(match.group(1))
                st_sec = int(match.group(2))
                timestamp_seconds = (st_min * 60) + st_sec
                
                # 再生時間をすでに通過している（かつ最も現在時刻に近い）発言行を探す
                if timestamp_seconds <= elapsed_seconds:
                    diff = elapsed_seconds - timestamp_seconds
                    if diff < closest_diff:
                        closest_diff = diff
                        best_match_line = line_num

        # 💡 現在再生中の発言行が見つかった場合、そこへジャンプ
        if best_match_line is not None:
            # 対象行の背景を薄い青色（active_lineタグ）にする
            self.detail_text.tag_add("active_line", f"{best_match_line}.0", f"{best_match_line}.end")
            # 画面中央付近にくるように滑らかに見える位置へ自動スクロール
            self.detail_text.see(f"{best_match_line}.0")
            
        self.detail_text.config(state=tk.DISABLED)

    def retranscribe_selected_file(self):
        selected_item = self.tree.selection()
        if not selected_item:
            messagebox.showwarning("ファイル未選択", "リストから再認識させたい音声ファイルを選択してください。")
            return
        file_path = self.tree.item(selected_item[0], "tags")[0]
        base_path, _ = os.path.splitext(file_path)
        current_mode = self.ai_engine_var.get()
        txt_save_path = base_path + f".transcription.{current_mode}.txt"
        if os.path.exists(txt_save_path):
            try: os.remove(txt_save_path)
            except: pass
        self.refresh_list()

    def delete_single_transcription(self):
        selected_item = self.tree.selection()
        if not selected_item: return
        file_path = self.tree.item(selected_item[0], "tags")[0]
        base_path, _ = os.path.splitext(file_path)
        current_mode = self.ai_engine_var.get()
        txt_save_path = base_path + f".transcription.{current_mode}.txt"
        if os.path.exists(txt_save_path):
            if messagebox.askyesno("確認", f"このファイルの文字起こしキャッシュ（{current_mode.upper()}）を削除して再解析しますか？"):
                try: os.remove(txt_save_path); self.refresh_list()
                except: pass

    def delete_all_transcriptions(self):
        folder = self.selected_dir.get()
        current_mode = self.ai_engine_var.get()
        if not folder or not messagebox.askyesno("全削除", f"現在選択中の {current_mode.upper()} の文字起こしデータをすべて完全に削除して再解析しますか？"): return
        for dirpath, _, filenames in os.walk(folder):
            for f in filenames:
                if f.endswith(f".transcription.{current_mode}.txt"):
                    try: os.remove(os.path.join(dirpath, f))
                    except: pass
        self.refresh_list()

    def play_audio(self):
        selected_item = self.tree.selection()
        if not selected_item: return
        tags = self.tree.item(selected_item[0], "tags")
        if not tags or not tags[0]: return
        self.play_audio_from_seconds(tags[0], self.seek_offset_seconds)

    def play_audio_from_seconds(self, file_path, start_seconds):
        self.is_playing = False
        if self.current_process:
            self.current_process.terminate()
            self.current_process = None
            
        self.playing_file_path = file_path
        self.total_seconds, duration_str = self.get_audio_info(self.playing_file_path)
        if self.total_seconds == 0: self.total_seconds = 1
        
        try:
            cmd = ["ffplay", "-nodisp", "-autoexit", "-ss", str(round(start_seconds, 2)), self.playing_file_path]
            self.current_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.is_playing = True
            
            self.playback_start_time = time.time()
            self.seek_offset_seconds = start_seconds
            
            self.timer_thread = threading.Thread(target=self.track_playback_progress_with_waveform, args=(duration_str,), daemon=True)
            self.timer_thread.start()
        except: pass

    def track_playback_progress_with_waveform(self, duration_str):
        while self.is_playing and self.current_process:
            if self.current_process.poll() is not None: break
            elapsed = (time.time() - self.playback_start_time) + self.seek_offset_seconds
            if elapsed >= self.total_seconds: break
            
            percent = (elapsed / self.total_seconds) * 100.0
            cur_time_str = f"{int(elapsed // 60):02d}:{int(elapsed % 60):02d}"
            
            self.root.after(0, lambda p=percent, t=f"{cur_time_str} / {duration_str}", e=elapsed: self.update_player_timeline_ui(p, t, e))
            time.sleep(0.1)
            
        if not self.is_playing: return
        self.root.after(0, lambda: self.update_player_timeline_ui(100.0, f"{duration_str} / {duration_str}", self.total_seconds))
        self.is_playing = False
        self.seek_offset_seconds = 0.0

    def update_player_timeline_ui(self, percent, time_text, elapsed_seconds):
        """💡 時間の文字列、波形描画に加えて、テキストエリアの自動スクロールを同期して呼び出す"""
        self.time_label.config(text=time_text)
        self.redraw_waveform_timeline(percent)
        # 💡 再生時間の経過に合わせて文字のスクロール位置を同期！
        self.sync_text_scroll_to_time(elapsed_seconds)

    def stop_audio(self):
        self.is_playing = False
        if self.current_process: 
            self.current_process.terminate()
            self.current_process = None
        self.seek_offset_seconds = 0.0
        self.update_player_timeline_ui(0.0, "00:00 / 00:00", 0.0)

if __name__ == "__main__":
    root = tk.Tk()
    app = FaxPlayerApp(root)
    root.mainloop()
