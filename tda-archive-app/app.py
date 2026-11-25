from __future__ import annotations

import base64
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from archive_format import (
    ArchiveError,
    ENCRYPTED_MAGIC,
    PasswordError,
    TDAArchive,
    human_size,
)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    RootWindow = TkinterDnD.Tk
    HAS_DND = True
except ImportError:
    DND_FILES = None
    TkinterDnD = None
    RootWindow = tk.Tk
    HAS_DND = False


TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml", ".html",
    ".htm", ".css", ".js", ".py", ".c", ".cpp", ".h", ".java", ".log",
    ".ini", ".yaml", ".yml", ".toml", ".sql", ".rtf",
}
IMAGE_EXTENSIONS = {".png", ".gif"}  # Tkinter's built-in PhotoImage formats.


class TDAArchiveApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.archive: TDAArchive | None = None
        self.theme = "light"
        self.item_paths: dict[str, str] = {}
        self._image_ref = None
        self.root.title("TDA Archive")
        self.root.geometry("1040x680")
        self.root.minsize(780, 500)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build()
        self._apply_theme()
        self._set_enabled(False)
        if HAS_DND:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self.on_drop)
        self._set_status("Yeni bir .tda arşivi oluşturun veya mevcut arşivi açın.")

    def _build(self) -> None:
        self.style = ttk.Style(self.root)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

        self.top = ttk.Frame(self.root, padding=(18, 14, 18, 8))
        self.top.pack(fill="x")
        brand = ttk.Frame(self.top)
        brand.pack(side="left", fill="x", expand=True)
        ttk.Label(brand, text="TDA Archive", style="Brand.TLabel").pack(anchor="w")
        ttk.Label(brand, text="Taşınabilir, yerel ve bağımsız arşiv yöneticisi", style="Subtle.TLabel").pack(anchor="w", pady=(2, 0))
        self.theme_button = ttk.Button(self.top, text="Koyu tema", command=self.toggle_theme)
        self.theme_button.pack(side="right", padx=(8, 0))

        self.toolbar = ttk.Frame(self.root, padding=(18, 4, 18, 12))
        self.toolbar.pack(fill="x")
        self.new_button = ttk.Button(self.toolbar, text="Yeni arşiv", command=self.new_archive)
        self.new_button.pack(side="left", padx=(0, 7))
        self.open_button = ttk.Button(self.toolbar, text="Arşiv aç", command=self.open_archive)
        self.open_button.pack(side="left", padx=(0, 14))
        self.add_button = ttk.Button(self.toolbar, text="Dosya ekle", command=self.add_files)
        self.add_button.pack(side="left", padx=(0, 7))
        self.folder_button = ttk.Button(self.toolbar, text="Klasör ekle", command=self.add_folder)
        self.folder_button.pack(side="left", padx=(0, 7))
        self.rename_button = ttk.Button(self.toolbar, text="Yeniden adlandır", command=self.rename_selected)
        self.rename_button.pack(side="left", padx=(0, 7))
        self.delete_button = ttk.Button(self.toolbar, text="Sil", command=self.delete_selected)
        self.delete_button.pack(side="left", padx=(0, 7))

        self.main = ttk.Frame(self.root, padding=(18, 0, 18, 10))
        self.main.pack(fill="both", expand=True)
        columns = ("size", "kind")
        self.tree = ttk.Treeview(self.main, columns=columns, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Ad")
        self.tree.heading("size", text="Boyut")
        self.tree.heading("kind", text="Tür")
        self.tree.column("#0", width=620, minwidth=260, stretch=True)
        self.tree.column("size", width=115, minwidth=90, anchor="e", stretch=False)
        self.tree.column("kind", width=100, minwidth=80, anchor="center", stretch=False)
        yscroll = ttk.Scrollbar(self.main, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(self.main, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        self.main.rowconfigure(0, weight=1)
        self.main.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", lambda _event: self.preview_selected())
        if HAS_DND:
            self.tree.drop_target_register(DND_FILES)
            self.tree.dnd_bind("<<Drop>>", self.on_drop)

        self.actions = ttk.Frame(self.root, padding=(18, 0, 18, 8))
        self.actions.pack(fill="x")
        self.preview_button = ttk.Button(self.actions, text="Önizle", command=self.preview_selected)
        self.preview_button.pack(side="left", padx=(0, 7))
        self.extract_button = ttk.Button(self.actions, text="Seçileni çıkar", command=self.extract_selected)
        self.extract_button.pack(side="left", padx=(0, 7))
        self.extract_all_button = ttk.Button(self.actions, text="Tümünü çıkar", command=self.extract_all)
        self.extract_all_button.pack(side="left", padx=(0, 7))
        self.verify_button = ttk.Button(self.actions, text="Bütünlüğü doğrula", command=self.verify_archive)
        self.verify_button.pack(side="left", padx=(0, 7))
        self.info_label = ttk.Label(self.actions, text="Arşiv açılmadı", style="Subtle.TLabel")
        self.info_label.pack(side="right", padx=(10, 0))

        self.status_var = tk.StringVar()
        self.status = ttk.Label(self.root, textvariable=self.status_var, anchor="w", padding=(18, 9), style="Status.TLabel")
        self.status.pack(side="bottom", fill="x")

    def _set_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in (
            self.add_button, self.folder_button, self.rename_button, self.delete_button,
            self.preview_button, self.extract_button, self.extract_all_button, self.verify_button,
        ):
            button.configure(state=state)

    def _apply_theme(self) -> None:
        dark = self.theme == "dark"
        bg = "#151922" if dark else "#f4f6f9"
        panel = "#1e2430" if dark else "#ffffff"
        fg = "#eef2f7" if dark else "#17202d"
        muted = "#a7b0bf" if dark else "#647184"
        accent = "#78a9ff" if dark else "#245ec7"
        border = "#343d4b" if dark else "#d9e0e9"
        self.root.configure(bg=bg)
        self.style.configure("TFrame", background=bg)
        self.style.configure("TLabel", background=bg, foreground=fg, font=("Segoe UI", 10))
        self.style.configure("Brand.TLabel", background=bg, foreground=fg, font=("Segoe UI Semibold", 21))
        self.style.configure("Subtle.TLabel", background=bg, foreground=muted, font=("Segoe UI", 9))
        self.style.configure("Status.TLabel", background=panel, foreground=muted, font=("Segoe UI", 9))
        self.style.configure("TButton", background=panel, foreground=fg, bordercolor=border, padding=(11, 7), font=("Segoe UI", 9))
        self.style.map("TButton", background=[("active", accent), ("disabled", bg)], foreground=[("active", "#ffffff"), ("disabled", muted)])
        self.style.configure("Treeview", background=panel, fieldbackground=panel, foreground=fg, rowheight=30, bordercolor=border, font=("Segoe UI", 10))
        self.style.map("Treeview", background=[("selected", accent)], foreground=[("selected", "#ffffff")])
        self.style.configure("Treeview.Heading", background=bg, foreground=muted, relief="flat", font=("Segoe UI Semibold", 9))
        self.style.map("Treeview.Heading", background=[("active", panel)])
        self.theme_button.configure(text="Açık tema" if dark else "Koyu tema")

    def toggle_theme(self) -> None:
        self.theme = "light" if self.theme == "dark" else "dark"
        self._apply_theme()

    def _set_status(self, message: str) -> None:
        dnd = "Sürükleyip bırakın veya Dosya ekle'yi kullanın." if HAS_DND else "Dosya eklemek için üstteki düğmeleri kullanın."
        self.status_var.set(f"{message}  •  {dnd}")

    def _selected_path(self) -> str | None:
        selected = self.tree.selection()
        return self.item_paths.get(selected[0]) if selected else None

    def _open_path(self, path: str | Path) -> None:
        target = Path(path)
        password = None
        try:
            with target.open("rb") as f:
                encrypted = f.read(len(ENCRYPTED_MAGIC)) == ENCRYPTED_MAGIC
            if encrypted:
                password = simpledialog.askstring("Parola", "Bu arşivin parolasını girin:", show="*", parent=self.root)
                if password is None:
                    return
            new_archive = TDAArchive.open(target, password)
            if self.archive:
                self.archive.close()
            self.archive = new_archive
            self._refresh()
            self._set_enabled(True)
            self._set_status("Arşiv açıldı; SHA-256 ve ZIP CRC kontrolleri başarılı.")
        except PasswordError as exc:
            messagebox.showerror("Arşiv açılamadı", str(exc), parent=self.root)
        except (ArchiveError, OSError) as exc:
            messagebox.showerror("Arşiv açılamadı", str(exc), parent=self.root)

    def new_archive(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Yeni TDA arşivi", defaultextension=".tda",
            filetypes=[("TDA Archive", "*.tda")],
        )
        if not path:
            return
        overwrite = Path(path).exists()
        if overwrite and not messagebox.askyesno(
            "Dosyanın üzerine yaz", f"'{Path(path).name}' zaten var. Üzerine yazılsın mı?", parent=self.root
        ):
            return
        password = None
        if messagebox.askyesno("Parola koruması", "Bu arşivi AES-256-GCM ile parola korumalı oluşturulsun mu?", parent=self.root):
            password = simpledialog.askstring("Yeni parola", "Arşiv parolasını girin:", show="*", parent=self.root)
            if password is None:
                return
            if not password:
                messagebox.showwarning("Parola gerekli", "Boş parola kullanılamaz.", parent=self.root)
                return
            confirm = simpledialog.askstring("Parolayı doğrula", "Parolayı tekrar girin:", show="*", parent=self.root)
            if confirm is None:
                return
            if confirm != password:
                messagebox.showerror("Parola uyuşmuyor", "Girilen parolalar aynı değil.", parent=self.root)
                return
        try:
            new_archive = TDAArchive.create(path, password, overwrite=overwrite)
            if self.archive:
                self.archive.close()
            self.archive = new_archive
            self._refresh()
            self._set_enabled(True)
            self._set_status("Yeni TDA Archive 1.0 oluşturuldu." + (" AES-256-GCM ile korumalı." if password else ""))
        except (ArchiveError, OSError) as exc:
            messagebox.showerror("Arşiv oluşturulamadı", str(exc), parent=self.root)

    def open_archive(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, title="TDA arşivi aç", filetypes=[("TDA Archive", "*.tda"), ("Tüm dosyalar", "*.*")])
        if path:
            self._open_path(path)

    def add_files(self) -> None:
        if not self.archive:
            return
        paths = filedialog.askopenfilenames(parent=self.root, title="Arşive dosya ekle")
        if paths:
            self._add_paths(paths)

    def add_folder(self) -> None:
        if not self.archive:
            return
        path = filedialog.askdirectory(parent=self.root, title="Arşive klasör ekle")
        if path:
            self._add_paths([path])

    def _add_paths(self, paths) -> None:
        if not self.archive:
            return
        try:
            self.archive.add_paths(paths)
            self._refresh()
            self._set_status("Seçilen dosya ve klasörler arşive eklendi.")
        except (ArchiveError, OSError) as exc:
            messagebox.showerror("Dosya eklenemedi", str(exc), parent=self.root)

    def on_drop(self, event) -> None:
        if not self.archive:
            self._set_status("Önce bir arşiv oluşturun veya açın.")
            return
        try:
            paths = self.root.tk.splitlist(event.data)
            if paths:
                self._add_paths(paths)
        except tk.TclError as exc:
            messagebox.showerror("Sürükle-bırak", f"Dosya yolları okunamadı: {exc}", parent=self.root)

    def _refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.item_paths.clear()
        if not self.archive:
            self.info_label.configure(text="Arşiv açılmadı")
            return
        dirs = {e["path"] for e in self.archive.entries if e["type"] == "dir"}
        item_ids: dict[str, str] = {}
        for entry in sorted(self.archive.entries, key=lambda e: (len(Path(e["path"]).parts), e["path"].casefold())):
            rel = entry["path"]
            parent_rel = str(Path(rel).parent).replace("\\", "/")
            parent_iid = item_ids.get(parent_rel, "") if parent_rel != "." else ""
            iid = ("d:" if entry["type"] == "dir" else "f:") + rel
            if entry["type"] == "dir":
                label = "▸  " + Path(rel).name
                values = ("—", "Klasör")
            else:
                label = self._icon(Path(rel).suffix) + "  " + Path(rel).name
                values = (human_size(entry["size"]), Path(rel).suffix.upper().lstrip(".") or "Dosya")
            self.tree.insert(parent_iid, "end", iid=iid, text=label, values=values, open=True)
            item_ids[rel] = iid
            self.item_paths[iid] = rel
        protection = "Şifreli AES-256-GCM" if self.archive.encrypted else "Şifrelenmemiş ZIP"
        archive_size = self.archive.path.stat().st_size if self.archive.path else 0
        self.info_label.configure(
            text=f"{self.archive.file_count} dosya · {self.archive.folder_count} klasör · içerik {human_size(self.archive.total_size)} · arşiv {human_size(archive_size)} · {protection}"
        )
        self.root.title(f"TDA Archive — {self.archive.path.name if self.archive.path else ''}")

    @staticmethod
    def _icon(extension: str) -> str:
        ext = extension.lower()
        if ext in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}:
            return "[IMG]"
        if ext == ".pdf":
            return "[PDF]"
        if ext in {".doc", ".docx", ".odt", ".rtf"}:
            return "[DOC]"
        if ext in {".xls", ".xlsx", ".ods", ".csv"}:
            return "[XLS]"
        if ext in {".ppt", ".pptx", ".odp"}:
            return "[PPT]"
        if ext in TEXT_EXTENSIONS:
            return "[TXT]"
        if ext in {".zip", ".7z", ".rar", ".tda"}:
            return "[ARC]"
        return "[FILE]"

    def _selected_entry(self) -> dict | None:
        rel = self._selected_path()
        if not rel or not self.archive:
            return None
        return next((e for e in self.archive.entries if e["path"] == rel), None)

    def preview_selected(self) -> None:
        entry = self._selected_entry()
        if not entry or not self.archive:
            messagebox.showinfo("Önizleme", "Önizlemek için bir dosya seçin.", parent=self.root)
            return
        if entry["type"] == "dir":
            messagebox.showinfo("Önizleme", "Klasörlerin önizlemesi yoktur.", parent=self.root)
            return
        rel = entry["path"]
        extension = Path(rel).suffix.lower()
        try:
            if extension in TEXT_EXTENSIONS:
                raw = self.archive.read_preview(rel, max_bytes=8 * 1024 * 1024)
                try:
                    text = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = raw.decode("utf-8", errors="replace")
                self._show_text(rel, text)
            elif extension in IMAGE_EXTENSIONS:
                raw = self.archive.read_preview(rel, max_bytes=16 * 1024 * 1024)
                self._show_image(rel, raw)
            else:
                messagebox.showinfo(
                    "Önizleme desteklenmiyor",
                    "Bu sürüm metin dosyalarını ve Tkinter tarafından desteklenen PNG/GIF görsellerini önizler. "
                    "PDF, DOCX, XLSX ve diğer dosyalar arşivde saklanabilir ve çıkarılabilir.",
                    parent=self.root,
                )
        except (ArchiveError, tk.TclError) as exc:
            messagebox.showerror("Önizleme açılamadı", str(exc), parent=self.root)

    def _show_text(self, title: str, text: str) -> None:
        window = tk.Toplevel(self.root)
        window.title(f"Önizleme — {Path(title).name}")
        window.geometry("760x520")
        frame = ttk.Frame(window, padding=10)
        frame.pack(fill="both", expand=True)
        widget = tk.Text(frame, wrap="none", font=("Consolas", 10), undo=False)
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=widget.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=widget.xview)
        widget.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        widget.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _show_image(self, title: str, raw: bytes) -> None:
        window = tk.Toplevel(self.root)
        window.title(f"Önizleme — {Path(title).name}")
        window.geometry("800x600")
        image = tk.PhotoImage(data=base64.b64encode(raw).decode("ascii"))
        self._image_ref = image
        label = ttk.Label(window, image=image)
        label.pack(fill="both", expand=True, padx=10, pady=10)
        label.image = image

    def rename_selected(self) -> None:
        entry = self._selected_entry()
        if not entry or not self.archive:
            messagebox.showinfo("Yeniden adlandır", "Önce bir dosya veya klasör seçin.", parent=self.root)
            return
        old = entry["path"]
        initial = Path(old).name
        name = simpledialog.askstring("Yeniden adlandır", "Yeni ad:", initialvalue=initial, parent=self.root)
        if not name:
            return
        parent = str(Path(old).parent).replace("\\", "/")
        new = name if parent == "." else parent + "/" + name
        try:
            self.archive.rename(old, new)
            self._refresh()
            self._set_status("Arşiv içi ad değiştirildi.")
        except (ArchiveError, OSError) as exc:
            messagebox.showerror("Yeniden adlandırılamadı", str(exc), parent=self.root)

    def delete_selected(self) -> None:
        entry = self._selected_entry()
        if not entry or not self.archive:
            messagebox.showinfo("Sil", "Önce bir dosya veya klasör seçin.", parent=self.root)
            return
        path = entry["path"]
        message = f"'{path}' arşivden silinsin mi?"
        if entry["type"] == "dir":
            message += "\nBu klasörün içindeki tüm kayıtlar da silinir."
        if not messagebox.askyesno("Arşivden sil", message, parent=self.root):
            return
        try:
            self.archive.delete(path)
            self._refresh()
            self._set_status("Kayıt arşivden silindi.")
        except (ArchiveError, OSError) as exc:
            messagebox.showerror("Silinemedi", str(exc), parent=self.root)

    def extract_selected(self) -> None:
        rel = self._selected_path()
        if not rel or not self.archive:
            messagebox.showinfo("Çıkar", "Önce bir dosya veya klasör seçin.", parent=self.root)
            return
        destination = filedialog.askdirectory(parent=self.root, title="Çıkarma klasörünü seç")
        if not destination:
            return
        try:
            paths = self.archive.extract(destination, [rel])
            self._set_status(f"{len(paths)} öğe çıkarıldı; mevcut dosyaların üzerine yazılmadı.")
            messagebox.showinfo("Çıkarma tamamlandı", f"Çıkarılan öğe sayısı: {len(paths)}", parent=self.root)
        except (ArchiveError, OSError) as exc:
            messagebox.showerror("Çıkarma başarısız", str(exc), parent=self.root)

    def extract_all(self) -> None:
        if not self.archive:
            return
        destination = filedialog.askdirectory(parent=self.root, title="Tüm arşivi çıkar")
        if not destination:
            return
        try:
            paths = self.archive.extract(destination)
            self._set_status(f"{len(paths)} öğe çıkarıldı; mevcut dosyaların üzerine yazılmadı.")
            messagebox.showinfo("Çıkarma tamamlandı", f"Çıkarılan öğe sayısı: {len(paths)}", parent=self.root)
        except (ArchiveError, OSError) as exc:
            messagebox.showerror("Çıkarma başarısız", str(exc), parent=self.root)

    def verify_archive(self) -> None:
        if not self.archive:
            return
        try:
            count, size = self.archive.verify()
            messagebox.showinfo("Bütünlük doğrulandı", f"{count} dosya ve {human_size(size)} veri doğrulandı.\nSHA-256 ve ZIP CRC kontrolleri başarılı.", parent=self.root)
            self._set_status("Bütünlük kontrolü başarılı.")
        except ArchiveError as exc:
            messagebox.showerror("Bütünlük kontrolü başarısız", str(exc), parent=self.root)

    def on_close(self) -> None:
        if self.archive:
            self.archive.close()
        self.root.destroy()


def main() -> None:
    root = RootWindow()
    TDAArchiveApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
