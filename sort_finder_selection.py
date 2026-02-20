#!/usr/bin/env python3
"""
Sort Finder Selection (Number Input)
=====================================
Replaces the Keyboard Maestro macro of the same name.

Usage
-----
Standalone:
    python3 sort_finder_selection.py

Via Keyboard Maestro (minimal setup):
    1. Create a new macro and add your hotkey trigger (e.g. Control-Option-S).
    2. Add a single action: Execute Shell Script
       Shell: /bin/zsh
       Script: python3 /path/to/sort_finder_selection.py
    That's it — no other KM actions needed.

Requirements
------------
- macOS (uses osascript for Finder access and dialogs)
- Python 3 (ships with macOS)
- Finder must be the active app (or at least have files selected) when you run the macro.
- Grant Finder/Automation access to Terminal or KM Engine when macOS prompts you.
"""

import os
import shutil
import subprocess
import sys

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DOCS_ROOT = os.path.expanduser(
    "~/Library/Mobile Documents/com~apple~CloudDocs/Documents"
)

FOLDER_MAP = {
    "1": "01_PERSONAL LIFE",
    "2": "02_CAREER",
    "3": "03_SCREENWRITING",
    "4": "04_STUDY / LEARNING",
    "5": "05_ADMIN & FINANCE",
    "6": "07_CREATIVE",
    "7": "99_INBOX",
}

PROMPT_TEXT = (
    "Type a number, then press Return:\\n\\n"
    "1 = 01_PERSONAL LIFE\\n"
    "2 = 02_CAREER\\n"
    "3 = 03_SCREENWRITING\\n"
    "4 = 04_STUDY / LEARNING\\n"
    "5 = 05_ADMIN & FINANCE\\n"
    "6 = 07_CREATIVE\\n"
    "7 = 99_INBOX"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_applescript(script: str) -> tuple[str, int]:
    """Run an AppleScript string and return (stdout, returncode)."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip(), result.returncode


def get_finder_selection() -> list[str]:
    """Return a list of POSIX paths currently selected in Finder."""
    script = """
tell application "Finder"
    set sel to selection
    if sel is {} then
        return ""
    end if
    set pathList to {}
    repeat with f in sel
        set end of pathList to POSIX path of (f as alias)
    end repeat
    set AppleScript's text item delimiters to "\\n"
    set pathString to pathList as string
    set AppleScript's text item delimiters to ""
    return pathString
end tell
"""
    output, _ = run_applescript(script)
    if not output:
        return []
    return [p.rstrip("/") for p in output.splitlines() if p.strip()]


def show_notification(message: str, title: str = "Sort Finder Selection") -> None:
    script = f'display notification "{message}" with title "{title}"'
    run_applescript(script)


def prompt_for_number() -> str | None:
    """
    Show a dialog asking for a number 1-7.
    Returns the entered string, or None if the user cancelled.
    """
    script = f"""
display dialog "{PROMPT_TEXT}" ¬
    default answer "1" ¬
    with title "Sort Finder Selection" ¬
    buttons {{"Cancel", "OK"}} ¬
    default button "OK"
return text returned of result
"""
    output, returncode = run_applescript(script)
    if returncode != 0:
        return None  # User hit Cancel or pressed Escape
    return output.strip()


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def main() -> None:
    # ── Step 1: safety check — nothing selected ──────────────────────────
    selected_files = get_finder_selection()
    if not selected_files:
        show_notification("Select files in Finder first.")
        sys.exit(0)

    # ── Step 2: prompt for folder number ─────────────────────────────────
    choice = prompt_for_number()
    if choice is None:
        sys.exit(0)  # User cancelled — silent exit

    # ── Step 3: map number → folder name ─────────────────────────────────
    target_folder = FOLDER_MAP.get(choice)
    if not target_folder:
        show_notification("Invalid choice. Type 1–7.")
        sys.exit(1)

    # ── Step 4: build destination path ───────────────────────────────────
    dest_folder = os.path.join(DOCS_ROOT, target_folder)

    # ── Step 5: create folder if it doesn't exist ────────────────────────
    os.makedirs(dest_folder, exist_ok=True)

    # ── Step 6: move each selected file/folder ───────────────────────────
    moved = 0
    errors: list[str] = []

    for path in selected_files:
        try:
            shutil.move(path, dest_folder)
            moved += 1
        except Exception as exc:
            errors.append(f"{os.path.basename(path)}: {exc}")

    # ── Step 7: success / error notification ─────────────────────────────
    if errors:
        summary = "; ".join(errors[:3])  # cap message length
        show_notification(f"Moved {moved} item(s). Errors: {summary}")
        sys.exit(1)
    else:
        show_notification(f"Moved {moved} item(s) → {target_folder}")


if __name__ == "__main__":
    main()
