# Office Hours Booking

A small web app for university office hours. Instructors publish blocks of time (for example
09:00-10:00 in Room 2A) and the app cuts them into bookable slots; students log in, pick an open
slot, and add an optional topic. Instructors see who booked which slot, and can cancel a whole
block, which cancels every booking inside it.

Everything runs locally: a FastAPI app, a few HTML pages, and one SQLite file.

## Requirements

- Python 3.12 or newer (developed and tested on Python 3.14)
- Git

Check what you have:

```bash
python --version
git --version
```

## Setup

### Windows (PowerShell)

```powershell
git clone https://github.com/TTariqq/office-hours-booking.git
cd office-hours-booking
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts/seed.py
python -m uvicorn app.main:app --reload
```

If PowerShell refuses to run `Activate.ps1` with a script-execution error, allow local scripts once
in that window and try again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### macOS / Linux

```bash
git clone https://github.com/TTariqq/office-hours-booking.git
cd office-hours-booking
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/seed.py
python -m uvicorn app.main:app --reload
```

The seed script prints how many users it added (`Seed finished: 4 new users.` the first time, and
`0` if you run it again — it never creates duplicates).

Then open **http://localhost:8000/login** in your browser.

## Test accounts

The seed script creates these four users:

| Name          | Role       | Email                     | Password              |
| ------------- | ---------- | ------------------------- | --------------------- |
| Dr. Sara Ahmed| instructor | sara.ahmed@example.edu    | instructor-sara-pass  |
| Dr. Omar Khalid| instructor | omar.khalid@example.edu   | instructor-omar-pass  |
| Lina Hassan   | student    | lina.hassan@example.edu   | student-lina-pass     |
| Yousef Ali    | student    | yousef.ali@example.edu    | student-yousef-pass   |

Instructors land on `/instructor`, students on `/student`.

## Running the tests

With the virtual environment active, from the project folder:

```bash
python -m pytest
```

A passing run looks like this (the exact time varies):

```text
123 passed, 1 warning in 33.71s
```

The tests use their own temporary SQLite databases and a frozen clock, so they never touch your
`ohb.db` and do not depend on the current date. The single warning is a deprecation notice from
Starlette's test client, not a failure.

## Quick demo

Sign in as **Dr. Sara Ahmed** (`sara.ahmed@example.edu` / `instructor-sara-pass`).

1. **Instructor publishes a block.** On the instructor home page, under *Publish a block*, fill in
   Date `(tomorrow's date)`, Start time `09:00`, End time `10:00`, Slot length `30 minutes`,
   Location `Room 2A`, then click **Publish block**. The block now appears under *Your blocks* with
   two slots, `09:00-09:30: open` and `09:30-10:00: open`.
2. **Student books.** Log out, sign in as **Lina Hassan**
   (`lina.hassan@example.edu` / `student-lina-pass`). Her *Open slots* list shows Dr. Sara's two
   slots. Type a topic such as `Homework 1` in the topic box next to `09:00-09:30` and click
   **Book**. The slot now shows up under *My bookings → Upcoming* as `Booked`.
3. **Instructor sees the booking.** Log out and sign in as Dr. Sara again. Under *Your blocks* the
   first slot now reads `09:00-09:30: booked - Lina Hassan (Homework 1)`.
4. **Instructor cancels the block.** Click **Cancel block** under that block. The block is marked
   `(cancelled)` and its slots read `cancelled`.
5. **Student sees the cancellation.** Log back in as Lina. Her upcoming booking still shows the time
   and instructor, now labelled **Cancelled by instructor**, and the *Cancel* button is gone.

Rules you will hit while trying this. For the instructor: a block must start in the future, last
between 30 and 240 minutes, use a slot length of 10, 15, 20 or 30 minutes, and not overlap another
of your own open blocks. For the student: only slots that start at least 60 minutes from now and at
most 14 days ahead are shown, a student may hold at most 2 active bookings with the same instructor,
and a booking can only be cancelled at least 120 minutes before it starts. All times you type and
see are Riyadh time; the database stores UTC.

## Notes

- The database is a local SQLite file called `ohb.db` in the project folder. It is created on first
  run and the tables are created automatically when the app starts.
- To reset everything, stop the app, delete `ohb.db`, and run `python scripts/seed.py` again.
- `OHB_SECRET_KEY` is optional for local use. If it is not set, the app logs a warning and uses a
  built-in development key for the session cookie; set it to a random string if you deploy the app.
- `ohb.db` and `.venv` are already in `.gitignore`, so neither ends up in a commit.
