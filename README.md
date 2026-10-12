# VF Grid DICOM Maker

A small desktop tool for typing the numerical grid of a Humphrey visual-field printout into a fixed
template and saving it as a DICOM image.

Three test patterns are supported: **24-2**, **30-2** and **60-4**. Each pattern has its own template in
which every tested location has a fixed position, so pages made with this tool always have the same
layout.

## How it works

1. **Test pattern.** Choose 24-2, 30-2 or 60-4.
2. **Details.** Enter the subject name and choose the eye (OD / OS), the timepoint (Timepoint 1 /
   Timepoint 2) and the data type (Total Deviation / Sensitivity).
3. **Values.** The grid for the chosen pattern is shown with one box per tested location. Type the
   values; the page preview on the right updates as you type. Then save the page as DICOM (or PNG).

Rules for the value boxes:

- A value is a whole number from -99 to 99. The Unicode minus sign and dashes are accepted as a minus.
- `X` marks a missing location and is drawn as a cross. One box is pre-marked with `X` (row 6 of the
  lattice, third column for OD, eighth column for OS); clear it and type `X` elsewhere to move it.
- A box that holds anything else is shown in red, and the page cannot be saved until it is corrected.
- Boxes left empty are saved as `X` after a confirmation.
- **Enter** moves to the next location; the **up** and **down** arrow keys move between rows. The line
  under the grid shows the index and the visual-field coordinates of the selected location.

## Templates

Every pattern is laid out on a 10 x 10 lattice. Locations are stored row by row, from the top row to the
bottom row and from left to right.

| Pattern | Locations | Spacing | x and y range | Locations per row (top to bottom) |
|---|---|---|---|---|
| 24-2 | 54 | 6 deg | x: -21 to 27, y: 21 to -21 | 4, 6, 8, 9, 9, 8, 6, 4 |
| 30-2 | 76 | 6 deg | -27 to 27 | 4, 6, 8, 10, 10, 10, 10, 8, 6, 4 |
| 60-4 | 60 | 12 deg | -54 to 54 | 4, 8, 6, 6, 6, 6, 6, 6, 8, 4 |

A location in lattice row `r` and column `c` (both counted from 0) lies at

```
x = spacing * (c - 4.5)      y = spacing * (4.5 - r)      (degrees)
```

The templates are defined in `PATTERNS` at the top of `vf_grid_dicom.py`.

## Page layout

| Item | Value |
|---|---|
| Page | 1000 x 1000 px, 8-bit grayscale, white background |
| Grid | cell (row 0, column 0) starts at x = 100, y = 100; cells are 80 x 80 px |
| Values | DejaVu Sans Bold, 42 px, centred in the cell |
| Missing location | cross with 64 px arms, 16 px line width |
| Title | DejaVu Sans Bold, 22 px, at x = 100, y = 40: `subject   eye   timepoint   pattern   data type` |

For a program that reads the grid by position, the alignment is therefore: origin (100, 100), cell width
80, cell height 80, no rotation.

## Output

The DICOM file is a Secondary Capture image (modality `OT`, `MONOCHROME2`, RLE Lossless) with

- Patient's Name and Patient ID set to the subject name,
- Laterality `R` for OD and `L` for OS,
- Series Description `<pattern> <data type> <timepoint> <eye>`,
- the current date and time as study and content date/time.

All files saved for the same subject name share one Study Instance UID. The suggested file name is
`<subject>_<eye>_<t1|t2>_<pattern>_<td|sensitivity>.dcm`.

The subject name is written into the DICOM header and printed on the page. Use a study code, not a
patient's name, if the files are going to be shared.

## Installation

Python 3.9 or newer with Tkinter (included in the standard Windows and macOS installers; on Debian or
Ubuntu install `python3-tk`).

```
pip install -r requirements.txt
python vf_grid_dicom.py
```
