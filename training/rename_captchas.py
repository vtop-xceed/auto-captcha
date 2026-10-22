import sqlite3
import os
import shutil
from collections import Counter

# Configuration
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'data', 'labels_all_done.db')
IMAGE_DIR = os.path.join(ROOT, 'data', 'captchas')
EXTENSIONS = ('.jpg', '.jpeg', '.png')

def rename_captchas():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Create a mapping of filename -> id
    cursor.execute("SELECT filename, id FROM images")
    filename_to_id = {row[0]: row[1] for row in cursor.fetchall()}

    # Get all files in the directory
    files = [f for f in os.listdir(IMAGE_DIR) if f.lower().endswith(EXTENSIONS)]
    print(f"Found {len(files)} files in {IMAGE_DIR}")

    used_names = Counter()

    for filename in files:
        if filename not in filename_to_id:
            print(f"Skipping {filename}: Not found in database.")
            continue

        image_id = filename_to_id[filename]

        # Find the label with the most occurrences for this image_id
        cursor.execute("""
            SELECT label, COUNT(*) as count 
            FROM submissions 
            WHERE image_id = ? 
            GROUP BY label 
            ORDER BY count DESC, timestamp DESC 
            LIMIT 1
        """, (image_id,))
        
        row = cursor.fetchone()
        if not row:
            print(f"Skipping {filename}: No submissions found for this image.")
            continue

        label = row[0].strip()
        if not label:
            print(f"Skipping {filename}: Empty label found.")
            continue

        # Sanitize label for filesystem
        safe_label = "".join([c for c in label if c.isalnum() or c in (' ', '-', '_')]).strip()
        if not safe_label:
            safe_label = "unknown_label"

        ext = os.path.splitext(filename)[1]
        
        # Handle collisions
        new_filename = f"{safe_label}{ext}"
        if used_names[new_filename] > 0:
            new_filename = f"{safe_label}_{used_names[new_filename]}{ext}"
        
        used_names[new_filename] += 1
        
        old_path = os.path.join(IMAGE_DIR, filename)
        new_path = os.path.join(IMAGE_DIR, new_filename)

        if old_path != new_path:
            try:
                os.rename(old_path, new_path)
                print(f"Renamed: {filename} -> {new_filename}")
            except Exception as e:
                print(f"Error renaming {filename}: {e}")
        else:
            print(f"Already Correct: {filename}")

    conn.close()
    print("Renaming process completed.")

if __name__ == "__main__":
    rename_captchas()
