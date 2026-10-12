#!/usr/bin/env python3
"""VF Grid DICOM Maker.

Type the values of a Humphrey visual-field printout into a fixed grid (24-2, 30-2 or 60-4) and save the
page as a DICOM image.

Steps: choose the test pattern, enter the subject name, eye, timepoint and data type, then fill in the
grid. Every tested location has a fixed position on a 1000 x 1000 px page; "X" marks a missing location
(for example the blind spot).

    python vf_grid_dicom.py
"""
import datetime
import os
import re
import sys
import tkinter as tk
import uuid
from tkinter import filedialog, messagebox

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, RLELossless, SecondaryCaptureImageStorage

# ----------------------------------------------------------------------------- page layout (pixels)
PAGE_SIZE = 1000
GRID_ORIGIN = (100, 100)        # top-left corner of cell (row 0, column 0)
CELL = 80                       # cell width and height
VALUE_FONT_PX = 42
TITLE_FONT_PX = 22
TITLE_XY = (100, 40)
CROSS_HALF = 32                 # half-length of each arm of the "missing" cross
CROSS_WIDTH = 16

# ----------------------------------------------------------------------------- pattern templates
# A pattern is a 10 x 10 lattice. For each row (top to bottom) the template lists the columns (left to
# right) that are tested locations. Values are stored in this row-major order.
PATTERNS = {
    "24-2": {
        "spacing_deg": 6,
        "rows": {
            1: [3, 4, 5, 6],
            2: [2, 3, 4, 5, 6, 7],
            3: [1, 2, 3, 4, 5, 6, 7, 8],
            4: [1, 2, 3, 4, 5, 6, 7, 8, 9],
            5: [1, 2, 3, 4, 5, 6, 7, 8, 9],
            6: [1, 2, 3, 4, 5, 6, 7, 8],
            7: [2, 3, 4, 5, 6, 7],
            8: [3, 4, 5, 6],
        },
    },
    "30-2": {
        "spacing_deg": 6,
        "rows": {
            0: [3, 4, 5, 6],
            1: [2, 3, 4, 5, 6, 7],
            2: [1, 2, 3, 4, 5, 6, 7, 8],
            3: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
            4: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
            5: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
            6: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
            7: [1, 2, 3, 4, 5, 6, 7, 8],
            8: [2, 3, 4, 5, 6, 7],
            9: [3, 4, 5, 6],
        },
    },
    "60-4": {
        "spacing_deg": 12,
        "rows": {
            0: [3, 4, 5, 6],
            1: [1, 2, 3, 4, 5, 6, 7, 8],
            2: [1, 3, 4, 5, 6, 8],
            3: [0, 1, 2, 7, 8, 9],
            4: [0, 1, 2, 7, 8, 9],
            5: [0, 1, 2, 7, 8, 9],
            6: [0, 1, 2, 7, 8, 9],
            7: [1, 3, 4, 5, 6, 8],
            8: [1, 2, 3, 4, 5, 6, 7, 8],
            9: [3, 4, 5, 6],
        },
    },
}
PATTERN_ORDER = ("24-2", "30-2", "60-4")
DATA_TYPES = ("Total Deviation", "Sensitivity")
TIMEPOINTS = ("Timepoint 1", "Timepoint 2")
EYES = {"OD": "R", "OS": "L"}
DEFAULT_MISSING_CELL = {"OD": (5, 2), "OS": (5, 7)}     # (row, column) pre-marked with X; can be changed
MISSING = "X"
MAX_ABS_VALUE = 99

IMPLEMENTATION_CLASS_UID = "2.25.109132673247091262548247942209356229865"
UID_NAMESPACE = uuid.UUID("6f0c2f0e-5a0b-4d0f-9d57-4e6f76665f31")


def pattern_cells(pattern):
    """(row, column) of every tested location, in storage order."""
    rows = PATTERNS[pattern]["rows"]
    return [(r, c) for r in sorted(rows) for c in rows[r]]


def cell_center(row, col):
    """Pixel position of a cell centre on the page."""
    return GRID_ORIGIN[0] + CELL * col + CELL // 2, GRID_ORIGIN[1] + CELL * row + CELL // 2


def cell_degrees(pattern, row, col):
    """Visual-field coordinates (x, y) of a cell in degrees; the lattice straddles both meridians."""
    step = PATTERNS[pattern]["spacing_deg"]
    return int(step * (col - 4.5)), int(step * (4.5 - row))


# ----------------------------------------------------------------------------- rendering
def find_font():
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "DejaVuSans-Bold.ttf"),
        os.path.join(here, "fonts", "DejaVuSans-Bold.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/DejaVuSans-Bold.ttf",
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "DejaVuSans-Bold.ttf"),
    ]
    try:
        import matplotlib                      # ships DejaVu Sans Bold
        candidates.append(os.path.join(matplotlib.get_data_path(), "fonts", "ttf", "DejaVuSans-Bold.ttf"))
    except ImportError:
        pass
    for path in candidates:
        if os.path.exists(path):
            return path
    raise FileNotFoundError("DejaVuSans-Bold.ttf was not found. Install matplotlib, or put the font file next to this script.")


def page_title(subject, eye, timepoint, pattern, data_type):
    return f"{subject}   {eye}   {timepoint}   {pattern}   {data_type}"


def render_page(pattern, title, values, font_path):
    """Draw the page. `values` holds one entry per location: an int, MISSING (a cross) or None (left blank)."""
    cells = pattern_cells(pattern)
    if len(values) != len(cells):
        raise ValueError(f"{pattern} has {len(cells)} locations, got {len(values)} values")
    value_font = ImageFont.truetype(font_path, VALUE_FONT_PX)
    image = Image.new("L", (PAGE_SIZE, PAGE_SIZE), 255)
    draw = ImageDraw.Draw(image)
    draw.text(TITLE_XY, title, font=ImageFont.truetype(font_path, TITLE_FONT_PX), fill=0)
    for (row, col), value in zip(cells, values):
        cx, cy = cell_center(row, col)
        if value is None:
            continue
        if value == MISSING:
            h = CROSS_HALF
            draw.line([(cx - h, cy - h), (cx + h, cy + h)], fill=0, width=CROSS_WIDTH)
            draw.line([(cx - h, cy + h), (cx + h, cy - h)], fill=0, width=CROSS_WIDTH)
            continue
        text = str(value)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=value_font)
        draw.text((cx - (left + right) / 2, cy - (top + bottom) / 2), text, font=value_font, fill=0)
    return image


# ----------------------------------------------------------------------------- DICOM
def new_uid(*parts):
    """A 2.25.<uuid> UID: derived from `parts` if given, random otherwise."""
    u = uuid.uuid5(UID_NAMESPACE, "|".join(parts)) if parts else uuid.uuid4()
    return "2.25." + str(u.int)


def save_dicom(path, image, subject, eye, timepoint, pattern, data_type):
    """Write the page as an 8-bit grayscale Secondary Capture image (RLE Lossless)."""
    pixels = np.asarray(image, dtype=np.uint8)
    now = datetime.datetime.now()
    sop_uid = new_uid()

    meta = FileMetaDataset()
    meta.FileMetaInformationGroupLength = 0          # filled in when the file is written
    meta.FileMetaInformationVersion = b"\x00\x01"
    meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    meta.MediaStorageSOPInstanceUID = sop_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = IMPLEMENTATION_CLASS_UID

    ds = Dataset()
    ds.file_meta = meta
    ds.SOPClassUID = SecondaryCaptureImageStorage
    ds.SOPInstanceUID = sop_uid
    ds.StudyDate = ds.ContentDate = now.strftime("%Y%m%d")
    ds.StudyTime = ds.ContentTime = now.strftime("%H%M%S")
    ds.Modality = "OT"
    ds.ConversionType = "WSD"
    ds.StudyDescription = "Visual field"
    ds.SeriesDescription = f"{pattern} {data_type} {timepoint} {eye}"
    ds.PatientName = subject
    ds.PatientID = subject
    ds.PatientBirthDate = ""
    ds.PatientSex = ""
    ds.StudyInstanceUID = new_uid("study", subject)      # one study per subject name
    ds.SeriesInstanceUID = new_uid()
    ds.InstanceNumber = "1"
    ds.Laterality = ds.ImageLaterality = EYES[eye]
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows, ds.Columns = pixels.shape
    ds.BitsAllocated = ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.PixelData = pixels.tobytes()
    ds.compress(RLELossless, encoding_plugin="pydicom", generate_instance_uid=False)
    ds.preamble = b"\x00" * 128
    ds.save_as(path)


# ----------------------------------------------------------------------------- input handling
_SIGNS = str.maketrans({"\u2212": "-", "\u2013": "-", "\u2014": "-"})     # minus sign, en dash, em dash


def parse_cell(text):
    """Return (kind, value): ("empty", None), ("missing", MISSING), ("value", int) or ("invalid", None)."""
    text = text.strip().translate(_SIGNS)
    if not text:
        return "empty", None
    if text.upper() == MISSING:
        return "missing", MISSING
    if re.fullmatch(r"[+-]?\d{1,2}", text):
        return "value", int(text)
    return "invalid", None


def safe_filename(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "subject"


# ----------------------------------------------------------------------------- user interface
EDITOR_SCALE = 0.62             # editor canvas size relative to the page
PREVIEW_PX = 500
COLOR_OK, COLOR_BAD, COLOR_MISSING = "white", "#f8c8c8", "#e6e6e6"


class App:
    def __init__(self, root):
        self.root = root
        root.title("VF Grid DICOM Maker")
        root.resizable(False, False)
        self.font_path = find_font()
        self.pattern = tk.StringVar(value="30-2")
        self.subject = tk.StringVar(value="subject-01")
        self.eye = tk.StringVar(value="OD")
        self.timepoint = tk.StringVar(value=TIMEPOINTS[0])
        self.data_type = tk.StringVar(value=DATA_TYPES[0])
        self.frame = None
        self.cell_vars = []
        self.entries = []
        self._preview_job = None
        self._preview_image = None
        self.show_pattern_step()

    # ---- step handling
    def _new_frame(self):
        if self.frame is not None:
            self.frame.destroy()
        self.frame = tk.Frame(self.root, padx=18, pady=14)
        self.frame.pack()
        return self.frame

    @staticmethod
    def _heading(parent, text):
        tk.Label(parent, text=text, font=("TkDefaultFont", 13, "bold")).pack(anchor="w", pady=(0, 10))

    @staticmethod
    def _radio_row(parent, label, variable, options):
        row = tk.Frame(parent)
        row.pack(anchor="w", pady=4)
        tk.Label(row, text=label, width=12, anchor="w").pack(side="left")
        for value, text in options:
            tk.Radiobutton(row, text=text, value=value, variable=variable).pack(side="left", padx=(0, 12))

    # ---- step 1: pattern
    def show_pattern_step(self):
        f = self._new_frame()
        self._heading(f, "1. Test pattern")
        for name in PATTERN_ORDER:
            n = len(pattern_cells(name))
            tk.Radiobutton(f, text=f"{name}   ({n} locations, {PATTERNS[name]['spacing_deg']}\u00b0 spacing)", value=name,
                           variable=self.pattern, font=("TkDefaultFont", 11)).pack(anchor="w", pady=3)
        tk.Button(f, text="Next", width=12, command=self.show_details_step).pack(anchor="e", pady=(14, 0))

    # ---- step 2: subject, eye, timepoint, data type
    def show_details_step(self):
        f = self._new_frame()
        self._heading(f, f"2. Details   ({self.pattern.get()})")
        row = tk.Frame(f)
        row.pack(anchor="w", pady=4)
        tk.Label(row, text="Subject name", width=12, anchor="w").pack(side="left")
        entry = tk.Entry(row, textvariable=self.subject, width=28)
        entry.pack(side="left")
        entry.focus_set()
        entry.bind("<Return>", lambda _e: self._details_done())
        self._radio_row(f, "Eye", self.eye, [(e, e) for e in EYES])
        self._radio_row(f, "Timepoint", self.timepoint, [(t, t) for t in TIMEPOINTS])
        self._radio_row(f, "Data type", self.data_type, [(d, d) for d in DATA_TYPES])
        buttons = tk.Frame(f)
        buttons.pack(fill="x", pady=(14, 0))
        tk.Button(buttons, text="Back", width=12, command=self.show_pattern_step).pack(side="left")
        tk.Button(buttons, text="Next", width=12, command=self._details_done).pack(side="right")

    def _details_done(self):
        if not self.subject.get().strip():
            messagebox.showwarning("Subject name", "Enter a subject name.", parent=self.root)
            return
        self.subject.set(self.subject.get().strip())
        self.show_grid_step()

    # ---- step 3: grid
    def title_text(self):
        return page_title(self.subject.get(), self.eye.get(), self.timepoint.get(), self.pattern.get(), self.data_type.get())

    def show_grid_step(self):
        f = self._new_frame()
        pattern = self.pattern.get()
        cells = pattern_cells(pattern)
        self._heading(f, f"3. Values   ({len(cells)} locations)")
        body = tk.Frame(f)
        body.pack()

        size = int(PAGE_SIZE * EDITOR_SCALE)
        canvas = tk.Canvas(body, width=size, height=size, bg="white", highlightthickness=1, highlightbackground="#999999")
        canvas.pack(side="left")
        mid = size // 2
        canvas.create_line(mid, int(GRID_ORIGIN[1] * EDITOR_SCALE), mid, size - int(GRID_ORIGIN[1] * EDITOR_SCALE), fill="#cccccc")
        canvas.create_line(int(GRID_ORIGIN[0] * EDITOR_SCALE), mid, size - int(GRID_ORIGIN[0] * EDITOR_SCALE), mid, fill="#cccccc")
        canvas.create_text(int(TITLE_XY[0] * EDITOR_SCALE), int(TITLE_XY[1] * EDITOR_SCALE), anchor="nw", text=self.title_text(),
                           font=("TkDefaultFont", 10, "bold"))

        self.cell_vars, self.entries = [], []
        missing_cell = DEFAULT_MISSING_CELL[self.eye.get()]
        for index, (row, col) in enumerate(cells):
            var = tk.StringVar(value=MISSING if (row, col) == missing_cell else "")
            entry = tk.Entry(canvas, textvariable=var, width=3, justify="center", font=("TkDefaultFont", 13, "bold"), relief="solid", bd=1)
            cx, cy = cell_center(row, col)
            canvas.create_window(int(cx * EDITOR_SCALE), int(cy * EDITOR_SCALE), window=entry)
            entry.bind("<FocusIn>", lambda _e, i=index: self._on_focus(i))
            entry.bind("<Return>", lambda _e, i=index: self._focus_cell(i + 1))
            entry.bind("<Up>", lambda _e, i=index: self._move_vertical(i, -1))
            entry.bind("<Down>", lambda _e, i=index: self._move_vertical(i, +1))
            var.trace_add("write", lambda *_a, i=index: self._on_edit(i))
            self.cell_vars.append(var)
            self.entries.append(entry)

        self.preview_label = tk.Label(body, bd=1, relief="solid")
        self.preview_label.pack(side="left", anchor="n", padx=(14, 0))

        self.position_label = tk.Label(f, anchor="w", fg="#444444")
        self.position_label.pack(fill="x", pady=(8, 0))
        self.status_label = tk.Label(f, anchor="w", fg="#444444")
        self.status_label.pack(fill="x")
        tk.Label(f, anchor="w", fg="#777777",
                 text=f"Whole numbers from -{MAX_ABS_VALUE} to {MAX_ABS_VALUE}.  X = missing location.  "
                      "Enter moves to the next location, arrow keys move between rows.").pack(fill="x")

        buttons = tk.Frame(f)
        buttons.pack(fill="x", pady=(12, 0))
        tk.Button(buttons, text="Back", width=10, command=self._back_from_grid).pack(side="left")
        tk.Button(buttons, text="Clear values", width=12, command=self._clear).pack(side="left", padx=8)
        tk.Button(buttons, text="Save DICOM...", width=14, command=self.save_dicom_dialog).pack(side="right")
        tk.Button(buttons, text="Save PNG...", width=12, command=self.save_png_dialog).pack(side="right", padx=8)

        for i in range(len(cells)):
            self._paint(i)
        self._update_status()
        self._refresh_preview()
        first_empty = next((i for i, v in enumerate(self.cell_vars) if not v.get()), 0)
        self._focus_cell(first_empty)

    def _on_focus(self, index):
        pattern = self.pattern.get()
        row, col = pattern_cells(pattern)[index]
        x, y = cell_degrees(pattern, row, col)
        self.position_label.config(text=f"Location {index + 1} of {len(self.entries)}:   x = {x}\u00b0,  y = {y}\u00b0")
        self.entries[index].select_range(0, "end")

    def _focus_cell(self, index):
        if 0 <= index < len(self.entries):
            self.entries[index].focus_set()

    def _move_vertical(self, index, direction):
        cells = pattern_cells(self.pattern.get())
        row, col = cells[index]
        candidates = [i for i, (r, _c) in enumerate(cells) if r == row + direction]
        if candidates:
            self._focus_cell(min(candidates, key=lambda i: abs(cells[i][1] - col)))
        return "break"

    def _paint(self, index):
        kind, _value = parse_cell(self.cell_vars[index].get())
        self.entries[index].config(bg={"invalid": COLOR_BAD, "missing": COLOR_MISSING}.get(kind, COLOR_OK))

    def _on_edit(self, index):
        self._paint(index)
        self._update_status()
        if self._preview_job is not None:
            self.root.after_cancel(self._preview_job)
        self._preview_job = self.root.after(150, self._refresh_preview)

    def _counts(self):
        kinds = [parse_cell(v.get())[0] for v in self.cell_vars]
        return {k: kinds.count(k) for k in ("value", "missing", "empty", "invalid")}

    def _update_status(self):
        c = self._counts()
        text = f"Entered {c['value']}   |   missing (X) {c['missing']}   |   empty {c['empty']}"
        if c["invalid"]:
            text += f"   |   not valid {c['invalid']}"
        self.status_label.config(text=text, fg="#b00020" if c["invalid"] else "#444444")

    def page_values(self, empty_as_missing=False):
        """Values for render_page; invalid cells count as empty."""
        values = []
        for var in self.cell_vars:
            kind, value = parse_cell(var.get())
            if kind in ("empty", "invalid"):
                value = MISSING if empty_as_missing else None
            values.append(value)
        return values

    def _refresh_preview(self):
        self._preview_job = None
        if not self.cell_vars or not self.preview_label.winfo_exists():
            return
        page = render_page(self.pattern.get(), self.title_text(), self.page_values(), self.font_path)
        self._preview_image = ImageTk.PhotoImage(page.resize((PREVIEW_PX, PREVIEW_PX), Image.LANCZOS))
        self.preview_label.config(image=self._preview_image)

    def _clear(self):
        if messagebox.askyesno("Clear values", "Remove all entered values?", parent=self.root):
            missing_cell = DEFAULT_MISSING_CELL[self.eye.get()]
            for var, cell in zip(self.cell_vars, pattern_cells(self.pattern.get())):
                var.set(MISSING if cell == missing_cell else "")
            self._focus_cell(0)

    def _back_from_grid(self):
        if self._counts()["value"] and not messagebox.askyesno("Back", "Going back discards the entered values. Continue?", parent=self.root):
            return
        self.cell_vars, self.entries = [], []
        self.show_details_step()

    # ---- saving
    def _final_page(self):
        """Return the finished page, or None if the user has to correct something first."""
        c = self._counts()
        if c["invalid"]:
            messagebox.showwarning("Check the values", f"{c['invalid']} location(s) hold something other than a whole number or X "
                                   "(marked in red).", parent=self.root)
            return None
        if c["empty"] and not messagebox.askyesno(
                "Empty locations", f"{c['empty']} location(s) are empty. They will be saved as X (missing).\n\nSave anyway?", parent=self.root):
            return None
        return render_page(self.pattern.get(), self.title_text(), self.page_values(empty_as_missing=True), self.font_path)

    def _default_name(self):
        tp = "t" + self.timepoint.get().split()[-1]
        return f"{safe_filename(self.subject.get())}_{self.eye.get().lower()}_{tp}_{self.pattern.get()}_{'td' if self.data_type.get() == DATA_TYPES[0] else 'sensitivity'}"

    def save_dicom_dialog(self):
        page = self._final_page()
        if page is None:
            return
        path = filedialog.asksaveasfilename(parent=self.root, title="Save DICOM", defaultextension=".dcm",
                                            initialfile=self._default_name() + ".dcm", filetypes=[("DICOM", "*.dcm"), ("All files", "*.*")])
        if path:
            self.write_dicom(path, page)
            messagebox.showinfo("Saved", f"Saved:\n{path}", parent=self.root)

    def write_dicom(self, path, page):
        save_dicom(path, page, self.subject.get(), self.eye.get(), self.timepoint.get(), self.pattern.get(), self.data_type.get())

    def save_png_dialog(self):
        page = self._final_page()
        if page is None:
            return
        path = filedialog.asksaveasfilename(parent=self.root, title="Save PNG", defaultextension=".png",
                                            initialfile=self._default_name() + ".png", filetypes=[("PNG", "*.png")])
        if path:
            page.save(path)
            messagebox.showinfo("Saved", f"Saved:\n{path}", parent=self.root)


def main():
    root = tk.Tk()
    try:
        App(root)
    except FileNotFoundError as exc:
        root.withdraw()
        messagebox.showerror("Font not found", str(exc))
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
