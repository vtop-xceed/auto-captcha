import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(BASE_DIR), "data", "captchas")
DB_PATH = os.path.join(os.path.dirname(BASE_DIR), "labeler", "labels.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # images table: stores filename and status
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT UNIQUE,
            completed INTEGER DEFAULT 0
        )
    ''')
    
    # submissions table: stores individual label entries
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER,
            label TEXT,
            session_id TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (image_id) REFERENCES images (id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_submissions_image_id ON submissions(image_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_submissions_session_id ON submissions(session_id)')
    
    # Populate images if empty
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS contributors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            url TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute("SELECT COUNT(*) FROM images")
    if cursor.fetchone()[0] == 0:
        print(f"Populating database with images from {DATA_DIR}...")
        image_files = [f for f in os.listdir(DATA_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
        for f in image_files:
            cursor.execute("INSERT OR IGNORE INTO images (filename) VALUES (?)", (f,))
        print(f"Added {len(image_files)} images.")
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
