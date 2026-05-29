import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import subprocess
import datetime
import threading
import time
import warnings

# 警告メッセージを非表示にする
warnings.filterwarnings("ignore", category=UserWarning)

class FaxPlayerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Panasonic 電話/FAX 再生・高精度2名対話文字起こし")
        self.root.geometry("1150x720") # 全文表示エリア確保のため高さを少し広げました
        
        self.selected_dir = tk.StringVar()
        self.current_process = None
        
        self.is_playing = False
        self.total_seconds = 0
        self.current_seconds = 0
        self.timer_thread = None
        
        self.whisper_model = None
        self.default_init_dir = os.path.join(os.path.expanduser("~"), "Documents")
        
        self.create_widgets()

    def create_widgets(self):
        # 1. フォルダ選択
        dir_frame = ttk.Frame(self.root, padding=10)
        dir_frame.pack(fill=tk.X)
        
        ttk.Label(dir_frame, text="対象フォルダ/SDカード:").pack(side=tk.LEFT, padx=5)
        ttk.Entry(dir_frame, textvariable=self.selected_dir, width=60).pack(side=tk.LEFT, padx=5)
        ttk.Button(dir_frame, text=" 参照... ", command=self.browse_folder).pack(side=tk.LEFT, padx=5)
        
        # 2. リスト表示
        list_frame = ttk.Frame(self.root, padding=10)
        list_frame.pack(fill=tk.BOTH, expand=True)
        
        columns = ("filename", "duration", "transcription", "folder", "date_info")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings")
        self.tree.heading("filename", text="音声ファイル名 (.WAV)")
        self.tree.heading("duration", text="長さ")
        self.tree.heading("transcription", text="【電話・2名対話特化】文字起こし結果 (一行プレビュー)")
        self.tree.heading("folder", text="場所 (フォルダ)")
        self.tree.heading("date_info", text="関連情報 / 更新日時")
        
        self.tree.column("filename", width=130, anchor=tk.W)
        self.tree.column("duration", width=60, anchor=tk.CENTER)
        self.tree.column("transcription", width=480, anchor=tk.W)
        self.tree.column("folder", width=130, anchor=tk.W)
        self.tree.column("date_info", width=250, anchor=tk.W)
        
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # ダブルクリックで再生、シングルクリックで全文表示
        self.tree.bind("<Double-1>", lambda event: self.play_audio())
        self.tree.bind("<<TreeviewSelect>>", lambda event: self.show_full_transcription())

        # 3. 【新機能】文字起こし全文表示エリア
        detail_frame = ttk.LabelFrame(self.root, text=" 📝 選択中の文字起こし全文（クリックで表示）", padding=5)
        detail_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.detail_text = tk.Text(detail_frame, height=5, state=tk.DISABLED, bg="#FFFFFF", fg="#000000", font=("Helvetica", 12), wrap=tk.WORD)
        self.detail_text.pack(fill=tk.X, side=tk.LEFT, expand=True)
        
        detail_scroll = ttk.Scrollbar(detail_frame, orient=tk.VERTICAL, command=self.detail_text.yview)
        self.detail_text.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 4. 再生状況バー
        player_frame = ttk.LabelFrame(self.root, text=" 💡 再生状況 ", padding=10)
        player_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.time_label = ttk.Label(player_frame, text="00:00 / 00:00", font=("Menlo", 12, "bold"))
        self.time_label.pack(side=tk.RIGHT, padx=10)
        
        self.progress_val = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(player_frame, variable=self.progress_val, maximum=100, mode='determinate')
        self.progress_bar.pack(fill=tk.X, side=tk.LEFT, expand=True, padx=5)

        # 5. ログ表示
        log_frame = ttk.LabelFrame(self.root, text=" 処理ログ（解析状況）", padding=5)
        log_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.log_text = tk.Text(log_frame, height=3, state=tk.DISABLED, bg="#EAEAEA", fg="#000000", font=("Menlo", 11))
        self.log_text.pack(fill=tk.X, side=tk.LEFT, expand=True)
        
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 6. コントロールボタン
        control_frame = ttk.Frame(self.root, padding=10)
        control_frame.pack(fill=tk.X)
        
        ttk.Button(control_frame, text="▶︎ 再生", command=self.play_audio).pack(side=tk.LEFT, padx=10)
        ttk.Button(control_frame, text="◼︎ 停止", command=self.stop_audio).pack(side=tk.LEFT, padx=10)
        
        self.status_label = ttk.Label(control_frame, text="SDカードのフォルダを選択してください。")
        self.status_label.pack(side=tk.RIGHT, padx=10)

    def log(self, message):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        log_line = f"[{now}] {message}\n"
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, log_line)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state=tk.DISABLED)

    def browse_folder(self):
        folder = filedialog.askdirectory(title="PanasonicのSDカードを選択", initialdir=self.default_init_dir)
        if folder:
            self.selected_dir.set(folder)
            for item in self.tree.get_children():
                self.tree.delete(item)
            self.stop_audio()
            self.clear_log()
            
            # 全文表示エリアもクリア
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
        except:
            pass
        return 0, "--:--"

    def preprocess_phone_audio_via_ffmpeg(self, input_path):
        temp_output = input_path + ".temp.wav"
        try:
            cmd = ["ffmpeg", "-i", input_path, "-ar", "16000", "-ac", "1", "-loglevel", "quiet", "-y", temp_output]
            subprocess.run(cmd, check=True)
            return temp_output
        except:
            if os.path.exists(temp_output):
                os.remove(temp_output)
            return input_path

    def load_files_recursive(self, root_folder_path):
        self.root.after(0, lambda: self.log(f"検索開始: {root_folder_path}"))
        self.status_label.config(text="ファイルを検索中...")
        
        all_wav_data = []
        for dirpath, dirnames, filenames in os.walk(root_folder_path):
            wav_files = [f for f in filenames if f.upper().endswith('.WAV') and not f.startswith('.') and not f.endswith('.temp.wav')]

            for wav in wav_files:
                full_wav_path = os.path.join(dirpath, wav)
                mtime = os.path.getmtime(full_wav_path)
                mtime_str = datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
                seconds, duration_str = self.get_audio_info(full_wav_path)
                
                rel_folder = os.path.relpath(dirpath, root_folder_path)
                if rel_folder == ".": rel_folder = "（直下）"

                base_name = os.path.splitext(wav)[0]
                txt_path = os.path.join(dirpath, base_name + ".TXT")
                info_text = ""
                if os.path.exists(txt_path):
                    try:
                        with open(txt_path, 'r', encoding='cp932', errors='ignore') as f:
                            info_text = f.read().replace('\n', ' ').strip()
                    except: pass
                
                info_text = f"{info_text} [{mtime_str}]" if info_text else f"日時: {mtime_str}"
                
                all_wav_data.append({
                    "mtime": mtime, "wav": wav, "seconds": seconds, "duration": duration_str,
                    "rel_folder": rel_folder, "info_text": info_text, "full_path": full_wav_path,
                    "dirpath": dirpath, "base_name": base_name
                })
        
        total_files = len(all_wav_data)
        if total_files == 0:
            self.root.after(0, lambda: self.log("対象ファイルが見つかりませんでした。"))
            self.status_label.config(text="ファイルなし")
            return

        all_wav_data.sort(key=lambda x: x["mtime"], reverse=True)
        self.root.after(0, lambda: self.log(f"計 {total_files} 個のWAVファイルを検出"))
        
        for data in all_wav_data:
            # 各ファイルの保存済みテキストのパスを生成
            txt_save_path = os.path.join(data["dirpath"], data["base_name"] + ".transcription.txt")
            initial_status = "📄 保存済みデータを読込中..." if os.path.exists(txt_save_path) else "📄 AI解析待ち..."
            
            self.root.after(0, lambda d=data, status=initial_status: self.tree.insert(
                "", tk.END, values=(d["wav"], d["duration"], status, d["rel_folder"], d["info_text"]), tags=(d["full_path"],)
            ))

        # Whisperモデルの遅延ロード判定
        need_ai = any(not os.path.exists(os.path.join(d["dirpath"], d["base_name"] + ".transcription.txt")) for d in all_wav_data if d["seconds"] > 0.5)
        
        if need_ai:
            try:
                import whisper
                if self.whisper_model is None:
                    self.root.after(0, lambda: self.log("未解析のファイルがあるため、AI(Whisper)を起動しています..."))
                    self.status_label.config(text="AIエンジン起動中...")
                    self.whisper_model = whisper.load_model("base")
                    self.root.after(0, lambda: self.log("AIエンジンの起動が完了しました。"))
            except Exception as e:
                self.root.after(0, lambda err=e: self.log(f"❌ AI起動失敗: {err}"))
                return

        self.status_label.config(text="データ処理中...")

        for index, data in enumerate(all_wav_data):
            txt_save_path = os.path.join(data["dirpath"], data["base_name"] + ".transcription.txt")
            text_result = ""
            
            # 【新機能】すでに文字起こしファイルが存在する場合は読み込んでスキップ
            if os.path.exists(txt_save_path):
                self.root.after(0, lambda name=data['wav']: self.log(f"⚡️ 保存済みファイルを再利用 (AIスキップ): {name}"))
                try:
                    with open(txt_save_path, 'r', encoding='utf-8') as f:
                        text_result = f.read().strip()
                except Exception as e:
                    self.root.after(0, lambda name=data['wav']: self.log(f"⚠️ 保存済みファイルの読込失敗、再解析します: {name}"))
            
            # 保存データがない場合のみAIを実行
            if not text_result and data["seconds"] > 0.5 and self.whisper_model:
                self.root.after(0, lambda idx=index: self.log(f"[{idx + 1}/{total_files}] 新規AI文字起こし中: {data['wav']}"))
                optimized_wav = self.preprocess_phone_audio_via_ffmpeg(data["full_path"])
                try:
                    result = self.whisper_model.transcribe(
                        optimized_wav, language="ja", fp16=False,
                        initial_prompt="これは電話の通話録音です。2人の人間が交互に会話しています。"
                    )
                    text_result = result.get("text", "").strip()
                    
                    # 【新機能】結果をファイルに保存する
                    if text_result:
                        with open(txt_save_path, 'w', encoding='utf-8') as f:
                            f.write(text_result)
                            
                except Exception as e:
                    text_result = f"(解析エラー: {str(e)})"
                finally:
                    if optimized_wav != data["full_path"] and os.path.exists(optimized_wav):
                        os.remove(optimized_wav)
            
            # フォールバック処理
            if not text_result:
                if "用件" in data["info_text"] or "通話" in data["info_text"]:
                    text_result = f"({data['info_text'].split('[')[0].strip()})"
                else:
                    text_result = "（通話内容なし）"
            
            self.root.after(0, lambda idx=index, txt=text_result: self.update_tree_item(idx, txt))

        self.root.after(0, lambda: self.log("すべてのデータの読み込みが完了しました！"))
        self.status_label.config(text="読み込み完了")

    def update_tree_item(self, index, text):
        children = self.tree.get_children()
        if index < len(children):
            item_id = children[index]
            current_values = self.tree.item(item_id, "values")
            new_values = (current_values[0], current_values[1], text, current_values[2], current_values[3])
            self.tree.item(item_id, values=new_values)
            
            # 現在選択されている行が更新されたら全文表示も更新する
            selected = self.tree.selection()
            if selected and selected[0] == item_id:
                self.show_full_transcription()

    def show_full_transcription(self):
        """【新機能】リストで選択された行の文字起こし全文を下部のテキストエリアに表示する"""
        selected_item = self.tree.selection()
        if not selected_item:
            return
            
        # リストの3番目の列（文字起こし結果）を取得
        values = self.tree.item(selected_item[0], "values")
        full_text = values[2]
        
        # 表示を更新
        self.detail_text.config(state=tk.NORMAL)
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.insert(tk.END, full_text)
        self.detail_text.config(state=tk.DISABLED)

    def play_audio(self):
        self.stop_audio()
        selected_item = self.tree.selection()
        if not selected_item: return
        file_path = self.tree.item(selected_item[0], "tags")[0]
        filename = os.path.basename(file_path)
        self.total_seconds, duration_str = self.get_audio_info(file_path)
        if self.total_seconds == 0: self.total_seconds = 1
        try:
            self.current_process = subprocess.Popen(["afplay", file_path])
            self.log(f"▶︎ 再生開始: {filename}")
            self.status_label.config(text="♪ 再生中...")
            self.is_playing = True
            self.current_seconds = 0
            self.timer_thread = threading.Thread(target=self.track_playback_progress, args=(duration_str,), daemon=True)
            self.timer_thread.start()
        except Exception as e:
            self.log(f"❌ 再生エラー: {e}")

    def track_playback_progress(self, duration_str):
        while self.is_playing and self.current_process:
            if self.current_process.poll() is not None: break
            if self.current_seconds >= self.total_seconds: break
            percent = (self.current_seconds / self.total_seconds) * 100
            cur_time_str = f"{int(self.current_seconds // 60):02d}:{int(self.current_seconds % 60):02d}"
            self.root.after(0, lambda p=percent, text=f"{cur_time_str} / {duration_str}": self.update_player_ui(p, text))
            time.sleep(1)
            self.current_seconds += 1
        self.root.after(0, lambda: self.update_player_ui(100 if self.is_playing else 0, f"{duration_str} / {duration_str}" if self.is_playing else "00:00 / 00:00"))
        if self.is_playing:
            self.root.after(0, lambda: self.status_label.config(text="■ 再生終了"))
            self.is_playing = False

    def update_player_ui(self, percent, time_text):
        self.progress_val.set(percent)
        self.time_label.config(text=time_text)

    def stop_audio(self):
        self.is_playing = False
        if self.current_process:
            self.current_process.terminate()
            self.current_process = None
            self.log("◼︎ 再生を停止しました")
            self.status_label.config(text="■ 停止しました")
        self.update_player_ui(0, "00:00 / 00:00")

if __name__ == "__main__":
    root = tk.Tk()
    app = FaxPlayerApp(root)
    root.mainloop()

