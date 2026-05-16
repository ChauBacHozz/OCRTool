import threading
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog, messagebox

from qwen_ocr_viet import ocr_vietnamese
from parse_model import ExportedData

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class OCRApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("OCR Tool - Qwen3.5")
        self.geometry("780x700")

        self.files: list[str] = []
        self.results: list[dict] = []

        # ── File list ──
        top = ctk.CTkFrame(self)
        top.pack(fill="x", padx=12, pady=(12, 0))

        ctk.CTkLabel(top, text="Images:", anchor="w", font=("", 13)).pack(side="left")
        ctk.CTkButton(top, text="+ Add", width=70, command=self.add_files).pack(side="right", padx=(2, 0))
        ctk.CTkButton(top, text="+ Add Folder", width=100, command=self.add_folder).pack(side="right", padx=(2, 0))
        ctk.CTkButton(top, text="Clear", width=70, command=self.clear_files).pack(side="right", padx=(2, 0))

        self.listbox = ctk.CTkTextbox(self, height=150, font=("", 12))
        self.listbox.pack(fill="both", expand=True, padx=12, pady=6)
        self.listbox.configure(state="disabled")

        # ── Output dir ──
        dir_frame = ctk.CTkFrame(self)
        dir_frame.pack(fill="x", padx=12, pady=(0, 6))

        ctk.CTkLabel(dir_frame, text="Save to:", font=("", 13)).pack(side="left")
        self.dir_label = ctk.CTkLabel(dir_frame, text="(same folder as images)", text_color="gray")
        self.dir_label.pack(side="left", padx=6)
        ctk.CTkButton(dir_frame, text="Browse", width=80, command=self.choose_output_dir).pack(side="right")
        self.output_dir = ""

        # ── API URL ──
        api_frame = ctk.CTkFrame(self)
        api_frame.pack(fill="x", padx=12, pady=(0, 6))

        ctk.CTkLabel(api_frame, text="API URL:", font=("", 13)).pack(side="left")
        self.api_url_var = ctk.StringVar(value="http://100.73.239.86:8000/v1")
        api_entry = ctk.CTkEntry(api_frame, textvariable=self.api_url_var, placeholder_text="http://88.2.0.63:8000/v1")
        api_entry.pack(fill="x", side="left", padx=(6, 0), expand=True)

        # ── Field overrides ──
        field_frame = ctk.CTkFrame(self)
        field_frame.pack(fill="x", padx=12, pady=(0, 6))

        self.field_vars = {}
        field_labels = {
            "so_giay_phep": "Số giấy phép",
            "loai_giay_phep": "Loại giấy phép",
            "hieuluc": "Hiệu lực",
            "coso": "Cơ sở",
            "qlcm": "Người quản lý chuyên môn"
        }
        field_hints = {
            "so_giay_phep": "VD: GP-2024-00123, QĐ-456, Giấy phép số 789",
            "loai_giay_phep": "VD: Giấy phép lao động, Quyết định, Giấy chứng nhận, Công văn",
            "hieuluc": "VD: 01/01/2024 - 31/12/2024, 2 năm, Vô thời hạn. Nếu output chỉ ghi 'có giá trị x năm kể từ ngày ký' thì phải tra ngày ký trên văn bản và ghi rõ (VD: 3 năm kể từ ngày 15/03/2022 → 15/03/2022 - 14/03/2025)",
            "coso": "VD: Công ty TNHH ABC, Chi nhánh XYZ, 123 Đường Lê Lợi",
            "qlcm": "VD: TCD. Trần Thị Thủy"
        }
        self.field_hints = field_hints

        for key in ExportedData.model_fields:
            row = ctk.CTkFrame(field_frame)
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=field_labels.get(key, key), width=110, anchor="w").pack(side="left")
            hint = field_hints.get(key, "")
            var = ctk.StringVar(value=hint)
            ctk.CTkEntry(row, textvariable=var).pack(fill="x", side="left", padx=(6, 0), expand=True)
            self.field_vars[key] = var

        # ── Progress ──
        self.progress = ctk.CTkProgressBar(self)
        self.progress.pack(fill="x", padx=12, pady=4)
        self.progress.set(0)

        self.status_var = ctk.StringVar(value="Ready")
        ctk.CTkLabel(self, textvariable=self.status_var, anchor="w", text_color="gray").pack(fill="x", padx=12)

        # ── Log ──
        self.log_box = ctk.CTkTextbox(self, height=90, font=("", 11))
        self.log_box.pack(fill="both", padx=12, pady=6)
        self.log_box.configure(state="disabled")

        # ── Dedup toggle ──
        self.dedup_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(self, text="Keep only latest version per prefix (dedup)",
                        variable=self.dedup_var).pack(anchor="w", padx=16)

        # ── Run ──
        self.run_btn = ctk.CTkButton(self, text="Run OCR", height=38, command=self.run_ocr)
        self.run_btn.pack(fill="x", padx=12, pady=(0, 12))

    # ──────────────────────────────────────────────

    def log(self, msg: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select images",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.tiff *.webp")],
        )
        if not paths:
            return
        self.listbox.configure(state="normal")
        for p in paths:
            if p not in self.files:
                self.files.append(p)
                self.listbox.insert("end", Path(p).name + "\n")
        self.listbox.configure(state="disabled")
        self.progress.set(0)

    def add_folder(self):
        folder = filedialog.askdirectory(title="Select folder containing images")
        if not folder:
            return
        image_exts = (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp")
        paths = sorted(
            str(p) for p in Path(folder).iterdir()
            if p.suffix.lower() in image_exts
        )
        if not paths:
            messagebox.showinfo("No images", "No supported images found in the selected folder.")
            return
        self.listbox.configure(state="normal")
        for p in paths:
            if p not in self.files:
                self.files.append(p)
                self.listbox.insert("end", Path(p).name + "\n")
        self.listbox.configure(state="disabled")
        self.progress.set(0)

    def clear_files(self):
        self.files.clear()
        self.listbox.configure(state="normal")
        self.listbox.delete("1.0", "end")
        self.listbox.configure(state="disabled")
        self.progress.set(0)
        self.status_var.set("Ready")

    def choose_output_dir(self):
        d = filedialog.askdirectory(title="Select output folder")
        if d:
            self.output_dir = d
            self.dir_label.configure(text=d, text_color="white")

    # ──────────────────────────────────────────────

    def run_ocr(self):
        if not self.files:
            messagebox.showwarning("No files", "Please add at least one image.")
            return
        self.run_btn.configure(state="disabled")
        self.progress.set(0)
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        self.results.clear()

        api_url = self.api_url_var.get().strip() or "http://88.2.0.63:8000/v1"

        overrides = {}
        for key, var in self.field_vars.items():
            val = var.get().strip()
            if val and val != self.field_hints.get(key, ""):
                overrides[key] = val

        def task():
            n = len(self.files)
            for i, f in enumerate(self.files):
                name = Path(f).name
                self.after(0, self.log, f"[{i+1}/{n}] {name} ...")
                self.after(0, self.status_var.set, f"[{i+1}/{n}] Processing: {name}")

                try:
                    data = ocr_vietnamese(f, api_url=api_url)
                    data_dict = data.model_dump()
                    data_dict.update(overrides)
                    self.results.append({"file": name, **data_dict})
                    self.after(0, self.log, f"       -> OK")
                except Exception as e:
                    self.after(0, self.log, f"       -> ERROR: {e}")

                self.after(0, lambda v=(i+1)/n: self.progress.set(v))

            self.after(0, self._save_xlsx)
            self.after(0, self._done)

        threading.Thread(target=task, daemon=True).start()

    def _save_xlsx(self):
        if not self.results:
            self.log("No results to save.")
            return

        import pandas as pd

        out_dir = self.output_dir or Path(self.files[0]).parent
        out_path = Path(out_dir) / "ocr_results.xlsx"

        df = pd.DataFrame(self.results)
        df["version"] = df["file"].str.split("_").str[-1]
        df["prefix"] = df["file"].str.split("_").str[:-1].str.join("_")

        if self.dedup_var.get():
            df["_sort_key"] = (
                df["version"]
                .str.replace(r"\.[^.]+$", "", regex=True)
                .str.extract(r"(\d+)$", expand=False)
                .fillna("0")
                .astype(int)
            )
            df = df.sort_values(["prefix", "_sort_key"], ascending=[True, False])
            df = df.groupby("prefix", as_index=False).first()
            df = df.drop(columns=["_sort_key"])
        df.drop(["prefix", "version"], axis=1, errors="ignore", inplace=True)
        df.to_excel(out_path, index=False, sheet_name="OCR Results")

    def _done(self):
        self.run_btn.configure(state="normal")
        self.status_var.set("Done.")
        self.log("=== All done ===")
        messagebox.showinfo("Complete", "All files processed.")


if __name__ == "__main__":
    OCRApp().mainloop()
