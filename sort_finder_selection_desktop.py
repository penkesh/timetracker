#!/usr/bin/env python3
import shutil
import subprocess
import sys
from pathlib import Path

DESKTOP_ROOT = Path.home() / "Desktop"
FOLDER_MAP = {
    "1": "01_PERSONAL LIFE",
    "2": "02_CAREER",
    "3": "03_SCREENWRITING",
    "4": "04_STUDY & LEARNING",
    "5": "05_ADMIN & FINANCE",
    "6": "06_CREATIVE",
    "7": "99_INBOX",
}


def run_osascript(script):
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "osascript failed")
    return proc.stdout.strip()


def notify(message, title="Sort Finder Selection"):
    msg = message.replace('"', '\\"')
    ttl = title.replace('"', '\\"')
    subprocess.run(
        ["osascript", "-e", 'display notification "{}" with title "{}"'.format(msg, ttl)],
        capture_output=True,
        text=True,
    )


def alert(message, title="Sort Finder Selection"):
    msg = message.replace('"', '\\"')
    ttl = title.replace('"', '\\"')
    subprocess.run(
        ["osascript", "-e", 'display dialog "{}" with title "{}" buttons {{"OK"}} default button "OK"'.format(msg, ttl)],
        capture_output=True,
        text=True,
    )


def load_folder_map():
    return dict(FOLDER_MAP)


def ensure_all_folders_exist(folder_map):
    for folder_name in folder_map.values():
        (DESKTOP_ROOT / folder_name).mkdir(parents=True, exist_ok=True)


def get_finder_selection_paths():
    script = r'''
    tell application "Finder"
        set sel to selection
        if (count of sel) is 0 then return ""
        set outText to ""
        repeat with f in sel
            set outText to outText & POSIX path of (f as alias) & linefeed
        end repeat
        return outText
    end tell
    '''
    output = run_osascript(script)
    if not output:
        return []
    return [Path(line) for line in output.splitlines() if line.strip()]


def prompt_for_choice(folder_map, default_value, prompt_text="Type a number, then press Return:"):
    entries = sorted(folder_map.items(), key=lambda kv: kv[0])
    mapping_lines = "\n".join("{} = {}".format(key, value) for key, value in entries)
    mapping_lines = mapping_lines.replace("\\", "\\\\").replace('"', '\\"')
    prompt_text = prompt_text.replace("\\", "\\\\").replace('"', '\\"')
    script = '''
    tell application "System Events"
        activate
        try
            display dialog "{prompt}\n\n{mapping}" ¬
                with title "Sort Finder Selection" ¬
                default answer "{default}" ¬
                buttons {{"Cancel", "OK"}} default button "OK"
            return text returned of result
        on error number -128
            return "__CANCELLED__"
        end try
    end tell
    '''.format(prompt=prompt_text, mapping=mapping_lines, default=default_value)
    result = run_osascript(script).strip()
    if result == "__CANCELLED__":
        return None
    return result


def get_subfolders(folder_path):
    """Return a sorted list of immediate subdirectories in folder_path."""
    try:
        return sorted(
            [p for p in folder_path.iterdir() if p.is_dir() and not p.name.startswith(".")],
            key=lambda p: p.name.lower(),
        )
    except PermissionError:
        return []


def drill_down(current_folder):
    """
    Interactively navigate into subfolders of current_folder.
    Returns the final chosen Path, or None if cancelled.
    Keeps prompting as long as the user picks a subfolder.
    Entering "0" stops drilling and uses the current folder.
    """
    folder = current_folder
    while True:
        subfolders = get_subfolders(folder)
        if not subfolders:
            # No subfolders — use this folder directly
            return folder

        # Build a numbered map: "1" -> subfolder, "2" -> subfolder, ...
        sub_map = {}
        for i, sub in enumerate(subfolders, start=1):
            sub_map[str(i)] = sub.name

        # Build the prompt including "0 = Stay here (move to current folder)"
        display_map = {"0": "[ Move here: {} ]".format(folder.name)}
        display_map.update(sub_map)

        prompt_text = "Current folder: {}\n\nEnter 0 to place files here, or pick a subfolder:".format(
            folder.relative_to(DESKTOP_ROOT) if folder != DESKTOP_ROOT else folder.name
        )

        choice = prompt_for_choice(display_map, "0", prompt_text=prompt_text)
        if choice is None:
            return None  # User cancelled

        choice = choice.strip()
        if choice == "0":
            return folder

        if choice in sub_map:
            folder = folder / sub_map[choice]
        else:
            notify("Invalid choice. Use 0 or one of: {}".format(", ".join(sorted(sub_map.keys()))))
            # Re-prompt the same level


def unique_destination(dest_dir, file_name):
    candidate = dest_dir / file_name
    if not candidate.exists():
        return candidate

    src_candidate = Path(file_name)
    stem = src_candidate.stem
    suffix = src_candidate.suffix
    index = 2
    while True:
        alt = dest_dir / ("{} {}{}".format(stem, index, suffix))
        if not alt.exists():
            return alt
        index += 1


def main():
    try:
        folder_map = load_folder_map()
        ensure_all_folders_exist(folder_map)

        selection = get_finder_selection_paths()
        if not selection:
            notify("Select files or folders in Finder first.")
            return 0

        # Step 1: Pick a top-level folder
        folder_num = prompt_for_choice(folder_map, "")
        if folder_num is None:
            return 0

        folder_num = folder_num.strip()
        if folder_num not in folder_map:
            notify("Invalid choice. Use one of: {}".format(", ".join(sorted(folder_map.keys()))))
            return 0

        target_folder_name = folder_map[folder_num]
        top_folder = DESKTOP_ROOT / target_folder_name
        top_folder.mkdir(parents=True, exist_ok=True)

        # Step 2: Optionally drill into subfolders
        dest_folder = drill_down(top_folder)
        if dest_folder is None:
            return 0  # Cancelled during subfolder navigation

        dest_folder.mkdir(parents=True, exist_ok=True)
        dest_root_resolved = dest_folder.resolve()

        moved_count = 0
        skipped_count = 0
        failed_count = 0
        for src_path in selection:
            if not src_path.exists():
                skipped_count += 1
                continue
            try:
                src_resolved = src_path.resolve()
                # Skip attempts to move a folder into itself or its descendants.
                if src_resolved == dest_root_resolved or dest_root_resolved.is_relative_to(src_resolved):
                    skipped_count += 1
                    continue

                dest_path = unique_destination(dest_folder, src_path.name)
                shutil.move(str(src_path), str(dest_path))
                moved_count += 1
            except Exception:
                failed_count += 1

        notify(
            "Moved {} item(s) to {} ({} skipped, {} failed)".format(
                moved_count, dest_folder.name, skipped_count, failed_count
            )
        )
        return 0
    except Exception as exc:
        alert("Error: {}".format(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
