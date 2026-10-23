import os
from flask import Flask, request, jsonify, send_from_directory, render_template
import sqlite3
from datetime import datetime, timedelta

app = Flask(__name__, template_folder='templates', static_folder='static')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "captchas")
DB_PATH = os.path.join(BASE_DIR, "labels.db")
VOTES_REQUIRED = 5

# Leaderboard cache - recalculate only once per hour
_leaderboard_cache = None
_leaderboard_cache_time = None
LEADERBOARD_CACHE_DURATION = timedelta(hours=1)

def init_db():
    if not os.path.exists(DB_PATH):
        print(f"Creating database at {DB_PATH}")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT UNIQUE,
            completed INTEGER DEFAULT 0
        )
    ''')
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
    
    # contributors table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS contributors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            url TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # online sessions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS online_sessions (
            session_id TEXT PRIMARY KEY,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Migration: Add session_id column to submissions if missing
    cursor.execute("PRAGMA table_info(submissions)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'session_id' not in columns:
        print("Migrating: Adding session_id column to submissions")
        cursor.execute("ALTER TABLE submissions ADD COLUMN session_id TEXT")
    
    # Migration: Add session_id column to contributors if missing
    cursor.execute("PRAGMA table_info(contributors)")
    contributor_columns = [col[1] for col in cursor.fetchall()]
    if 'session_id' not in contributor_columns:
        print("Migrating: Adding session_id column to contributors")
        cursor.execute("ALTER TABLE contributors ADD COLUMN session_id TEXT")
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_submissions_image_id ON submissions(image_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_submissions_session_id ON submissions(session_id)')
    
    cursor.execute("SELECT COUNT(*) FROM images")
    if cursor.fetchone()[0] == 0:
        if os.path.exists(DATA_DIR):
            image_files = [f for f in os.listdir(DATA_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
            for f in image_files:
                cursor.execute("INSERT OR IGNORE INTO images (filename) VALUES (?)", (f,))
        else:
            print(f"Warning: DATA_DIR {DATA_DIR} not found.")
            
    conn.commit()
    conn.close()

# Initialize DB on import (for PythonAnywhere)
init_db()

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.errorhandler(500)
def internal_error(error):
    import traceback
    return jsonify({
        "error": "Internal Server Error",
        "message": str(error),
        "traceback": traceback.format_exc()
    }), 500

@app.route('/')
def index():
    return render_template('index.html')

import uuid
from flask import make_response

@app.route('/api/next_captcha')
def next_captcha():
    session_id = request.cookies.get('session_id')
    if not session_id:
        session_id = str(uuid.uuid4())
        
    conn = get_db_connection()
    # Get an image that hasn't reached the required votes yet
    # AND hasn't been voted on by THIS session_id
    # Optimized query: Join images with submission counts, filter by session, 
    # and pull images that need more votes, prioritizing those closest to consensus.
    # We also cap at 10 total votes to avoid "stuck" images if people disagree wildly.
    query = '''
        SELECT i.id, i.filename, COUNT(s.id) as vote_count
        FROM images i
        LEFT JOIN submissions s ON i.id = s.image_id
        WHERE i.completed = 0
        AND i.id NOT IN (
            SELECT image_id FROM submissions WHERE session_id = ?
        )
        GROUP BY i.id
        HAVING vote_count < 10
        ORDER BY vote_count DESC, RANDOM()
        LIMIT 1
    '''
    image = conn.execute(query, (session_id,)).fetchone()
    
    if not image:
        # Fallback: if all images have 10+ votes or user has seen everything
        # Just pick any incomplete one they haven't seen
        query_fallback = '''
            SELECT id, filename, 0 as vote_count
            FROM images
            WHERE completed = 0
            AND id NOT IN (SELECT image_id FROM submissions WHERE session_id = ?)
            ORDER BY RANDOM()
            LIMIT 1
        '''
        image = conn.execute(query_fallback, (session_id,)).fetchone()
    
    # Progress stats
    stats = conn.execute('''
        SELECT 
            (SELECT COUNT(*) FROM images WHERE completed = 1) as completed,
            (SELECT COUNT(*) FROM images) as total
    ''').fetchone()
    
    # User's personal contribution count
    user_contributions = conn.execute(
        'SELECT COUNT(*) as count FROM submissions WHERE session_id = ?', 
        (session_id,)
    ).fetchone()['count']
    
    conn.close()
    
    if image:
        resp = make_response(jsonify({
            'id': image['id'],
            'filename': image['filename'],
            'progress': {
                'completed': stats['completed'],
                'total': stats['total'],
                'percent': (stats['completed'] / stats['total'] * 100) if stats['total'] > 0 else 0
            },
            'user_contributions': user_contributions
        }))
        if not request.cookies.get('session_id'):
            resp.set_cookie('session_id', session_id, max_age=60*60*24*30) # 30 days
        return resp
    else:
        return jsonify({'message': 'All images labeled!', 'user_contributions': user_contributions}), 404

@app.route('/api/submit', methods=['POST'])
def submit_label():
    session_id = request.cookies.get('session_id')
    data = request.json
    image_id = data.get('image_id')
    label = data.get('label', '').strip().upper()
    
    if not image_id or not label or not session_id:
        return jsonify({'error': 'Invalid data'}), 400
    
    conn = get_db_connection()
    
    # Check if image was already completed before this submission
    was_completed = conn.execute(
        'SELECT completed FROM images WHERE id = ?', (image_id,)
    ).fetchone()['completed']
    
    conn.execute('INSERT INTO submissions (image_id, label, session_id) VALUES (?, ?, ?)', (image_id, label, session_id))
    
    # Check if we should mark as completed using confidence-based logic
    submissions = conn.execute('SELECT label FROM submissions WHERE image_id = ?', (image_id,)).fetchall()
    labels = [s['label'] for s in submissions]
    
    just_completed = False
    total_votes = len(labels)
    
    # Confidence-based completion: >= 60% agreement with at least 3 votes
    if total_votes >= 3 and not was_completed:
        from collections import Counter
        counts = Counter(labels)
        most_common, freq = counts.most_common(1)[0]
        confidence = freq / total_votes
        
        if confidence >= 0.6:
            conn.execute('UPDATE images SET completed = 1 WHERE id = ?', (image_id,))
            just_completed = True
    
    # Get user's total contribution count
    user_contributions = conn.execute(
        'SELECT COUNT(*) as count FROM submissions WHERE session_id = ?', 
        (session_id,)
    ).fetchone()['count']
            
    conn.commit()
    conn.close()
    return jsonify({
        'success': True,
        'just_completed': just_completed,
        'user_contributions': user_contributions
    })

@app.route('/api/add_contributor', methods=['POST'])
def add_contributor():
    session_id = request.cookies.get('session_id')
    data = request.json
    name = data.get('name', '').strip()
    url = data.get('url', '').strip()
    
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    
    conn = get_db_connection()
    
    # Check if this session_id already has a contributor entry
    existing = conn.execute(
        'SELECT id FROM contributors WHERE session_id = ?', (session_id,)
    ).fetchone()
    
    if existing:
        # Update existing entry
        conn.execute('UPDATE contributors SET name = ?, url = ? WHERE session_id = ?', 
                     (name, url, session_id))
    else:
        # Insert new contributor with session_id
        conn.execute('INSERT INTO contributors (name, url, session_id) VALUES (?, ?, ?)', 
                     (name, url, session_id))
    
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/leaderboard')
def leaderboard():
    global _leaderboard_cache, _leaderboard_cache_time
    
    session_id = request.cookies.get('session_id')
    
    # Use IST (UTC+5:30) for cache timing
    now_utc = datetime.utcnow()
    now_ist = now_utc + timedelta(hours=5, minutes=30)
    
    # Cache refreshes at the top of each hour (IST)
    current_hour_ist = now_ist.replace(minute=0, second=0, microsecond=0)
    cache_valid = (
        _leaderboard_cache is not None and 
        _leaderboard_cache_time is not None and 
        _leaderboard_cache_time >= current_hour_ist  # Cache valid if set this hour (IST)
    )
    
    if not cache_valid:
        # Recalculate leaderboard data
        conn = get_db_connection()
        
        # Only include contributors with at least 1 contribution
        all_leaders = conn.execute('''
            SELECT 
                COALESCE(c.name, 'Anonymous') as name,
                c.url,
                COUNT(DISTINCT s.id) as contribution_count,
                GROUP_CONCAT(DISTINCT c.session_id) as session_ids
            FROM contributors c
            LEFT JOIN submissions s ON c.session_id = s.session_id
            WHERE c.name IS NOT NULL AND c.name != ''
            GROUP BY LOWER(TRIM(c.name))
            HAVING COUNT(DISTINCT s.id) > 0
            ORDER BY contribution_count DESC
        ''').fetchall()
        
        # Store in cache (convert to list of dicts for serialization)
        _leaderboard_cache = [
            {
                'name': l['name'],
                'url': l['url'],
                'contribution_count': l['contribution_count'],
                'session_ids': [s.strip() for s in (l['session_ids'] or '').split(',') if s.strip()]
            }
            for l in all_leaders
        ]
        _leaderboard_cache_time = now_ist
        conn.close()
    
    # Build personalized response using cached data
    leaderboard_data = []
    user_rank = None
    user_contributions = 0
    user_name = None
    
    for i, l in enumerate(_leaderboard_cache):
        is_user = session_id in l['session_ids'] if session_id else False
        
        entry = {
            'name': l['name'],
            'url': l['url'],
            'contributions': l['contribution_count'],
            'rank': i + 1,
            'is_user': is_user
        }
        
        if is_user:
            user_rank = i + 1
            user_contributions = l['contribution_count']
            user_name = l['name']
        
        # Only include top 10 in main leaderboard
        if i < 10:
            leaderboard_data.append(entry)
    
    # If user is not found in leaderboard, get their session contributions
    if user_rank is None and session_id:
        conn = get_db_connection()
        user_stats = conn.execute('''
            SELECT COUNT(*) as count FROM submissions WHERE session_id = ?
        ''', (session_id,)).fetchone()
        user_contributions = user_stats['count'] if user_stats else 0
        
        # Find user's rank based on contribution count
        if user_contributions > 0:
            # Count how many cached entries have more contributions
            higher_count = sum(1 for l in _leaderboard_cache if l['contribution_count'] > user_contributions)
            user_rank = higher_count + 1
        conn.close()
    
    # Calculate next update time (hourly, on the hour - IST)
    next_update_ist = current_hour_ist + timedelta(hours=1)
    seconds_until_update = int((next_update_ist - now_ist).total_seconds())
    
    return jsonify({
        'leaderboard': leaderboard_data,
        'user_rank': user_rank,
        'user_contributions': user_contributions,
        'user_name': user_name,
        'next_update_seconds': seconds_until_update
    })

@app.route('/api/heartbeat', methods=['POST'])
def heartbeat():
    session_id = request.cookies.get('session_id')
    if not session_id:
        conn = get_db_connection()
        count_row = conn.execute("SELECT COUNT(*) as count FROM online_sessions").fetchone()
        conn.close()
        return jsonify({'online_count': count_row['count'] if count_row else 0})
    
    conn = get_db_connection()
    conn.execute('''
        INSERT INTO online_sessions (session_id, last_seen) 
        VALUES (?, datetime('now'))
        ON CONFLICT(session_id) DO UPDATE SET last_seen=excluded.last_seen
    ''', (session_id,))
    conn.execute("DELETE FROM online_sessions WHERE last_seen < datetime('now', '-60 seconds')")
    count_row = conn.execute("SELECT COUNT(*) as count FROM online_sessions").fetchone()
    count = count_row['count']
    conn.commit()
    conn.close()
    return jsonify({'online_count': count})

@app.route('/captchas/<filename>')
def serve_captcha(filename):
    return send_from_directory(DATA_DIR, filename)

@app.route('/api/admin/stats')
def admin_stats():
    conn = get_db_connection()
    stats = conn.execute('''
        SELECT 
            (SELECT COUNT(*) FROM images WHERE completed = 1) as completed_count,
            (SELECT COUNT(*) FROM images) as total_count,
            (SELECT COUNT(*) FROM submissions) as total_submissions,
            (SELECT COUNT(*) FROM contributors) as total_contributors
    ''').fetchone()
    
    # Get last 5 completed images and their consensus label
    recent_completed = conn.execute('''
        SELECT i.filename, 
               (SELECT label FROM submissions WHERE image_id = i.id GROUP BY label ORDER BY COUNT(*) DESC LIMIT 1) as consensus_label
        FROM images i
        WHERE i.completed = 1
        ORDER BY i.id DESC
        LIMIT 5
    ''').fetchall()
    
    conn.close()
    
    return jsonify({
        "progress": {
            "completed": stats['completed_count'],
            "total": stats['total_count'],
            "percent": (stats['completed_count'] / stats['total_count'] * 100) if stats['total_count'] > 0 else 0
        },
        "activity": {
            "total_submissions": stats['total_submissions'],
            "total_contributors": stats['total_contributors']
        },
        "recently_finished": [dict(r) for r in recent_completed]
    })

if __name__ == '__main__':
    # Ensure DB is initialized
    if not os.path.exists(DB_PATH):
        init_db()
    app.run(debug=True, port=5000)
