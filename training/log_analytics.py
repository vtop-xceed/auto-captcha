#!/usr/bin/env python3
"""
Log Analytics Dashboard Generator
Parses PythonAnywhere logs and generates an HTML dashboard with visualizations.
"""

import re
import gzip
import os
from collections import Counter, defaultdict
from datetime import datetime
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, 'labeler', 'logs')
OUTPUT_FILE = os.path.join(ROOT, 'labeler', 'log_dashboard.html')

# Regex pattern to parse access logs
LOG_PATTERN = re.compile(
    r'(?P<proxy_ip>\S+) - - \[(?P<timestamp>[^\]]+)\] '
    r'"(?P<method>\S+) (?P<endpoint>\S+) \S+" (?P<status>\d+) \d+ '
    r'"(?P<referer>[^"]*)" "(?P<user_agent>[^"]*)" '
    r'"(?P<real_ip>[^"]*)"(?: response-time=(?P<response_time>\S+))?'
)

def parse_user_agent(ua):
    """Extract browser and device info from user agent."""
    browser = "Unknown"
    device = "Unknown"
    os_name = "Unknown"
    
    if "Chrome" in ua:
        browser = "Chrome"
    elif "Firefox" in ua:
        browser = "Firefox"
    elif "Safari" in ua and "Chrome" not in ua:
        browser = "Safari"
    elif "Edge" in ua:
        browser = "Edge"
    elif "Opera" in ua or "OPR" in ua:
        browser = "Opera"
    
    if "Mobile" in ua or "Android" in ua:
        device = "Mobile"
    elif "Tablet" in ua or "iPad" in ua:
        device = "Tablet"
    else:
        device = "Desktop"
    
    if "Android" in ua:
        os_name = "Android"
    elif "iPhone" in ua or "iPad" in ua:
        os_name = "iOS"
    elif "Windows" in ua:
        os_name = "Windows"
    elif "Mac OS" in ua:
        os_name = "macOS"
    elif "Linux" in ua:
        os_name = "Linux"
    
    return browser, device, os_name

def parse_logs():
    """Parse all access log files."""
    data = {
        'ips': Counter(),
        'endpoints': Counter(),
        'hours': Counter(),
        'days': Counter(),
        'browsers': Counter(),
        'devices': Counter(),
        'os': Counter(),
        'status_codes': Counter(),
        'response_times': [],
        'timestamps': [],
        'concurrent': defaultdict(int),
    }
    
    log_files = []
    for f in os.listdir(LOG_DIR):
        if 'access.log' in f:
            log_files.append(os.path.join(LOG_DIR, f))
    
    for log_file in sorted(log_files):
        print(f"Processing: {log_file}")
        
        if log_file.endswith('.gz'):
            opener = gzip.open
        else:
            opener = open
        
        try:
            with opener(log_file, 'rt', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    match = LOG_PATTERN.match(line)
                    if not match:
                        continue
                    
                    d = match.groupdict()
                    
                    # Extract real IP from X-Forwarded-For
                    real_ip = d['real_ip'].split(',')[0].strip() if d['real_ip'] else d['proxy_ip']
                    data['ips'][real_ip] += 1
                    
                    # Endpoint
                    data['endpoints'][d['endpoint']] += 1
                    
                    # Parse timestamp
                    try:
                        ts = datetime.strptime(d['timestamp'], '%d/%b/%Y:%H:%M:%S %z')
                        data['hours'][ts.hour] += 1
                        data['days'][ts.strftime('%Y-%m-%d')] += 1
                        data['timestamps'].append(ts)
                        
                        # Concurrency: count requests per minute
                        minute_key = ts.strftime('%Y-%m-%d %H:%M')
                        data['concurrent'][minute_key] += 1
                    except:
                        pass
                    
                    # User agent parsing
                    browser, device, os_name = parse_user_agent(d['user_agent'])
                    data['browsers'][browser] += 1
                    data['devices'][device] += 1
                    data['os'][os_name] += 1
                    
                    # Status codes
                    data['status_codes'][d['status']] += 1
                    
                    # Response times
                    if d['response_time']:
                        try:
                            data['response_times'].append(float(d['response_time']))
                        except:
                            pass
        except Exception as e:
            print(f"Error processing {log_file}: {e}")
    
    return data

def generate_html(data):
    """Generate HTML dashboard with Chart.js visualizations."""
    
    # Prepare data for charts
    top_ips = data['ips'].most_common(15)
    top_endpoints = data['endpoints'].most_common(10)
    hours = [data['hours'].get(h, 0) for h in range(24)]
    days = sorted(data['days'].items())[-14:]  # Last 14 days
    
    # Concurrency stats
    concurrent_values = list(data['concurrent'].values())
    max_concurrent = max(concurrent_values) if concurrent_values else 0
    avg_concurrent = sum(concurrent_values) / len(concurrent_values) if concurrent_values else 0
    
    # Response time stats
    if data['response_times']:
        avg_response = sum(data['response_times']) / len(data['response_times'])
        max_response = max(data['response_times'])
    else:
        avg_response = 0
        max_response = 0
    
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Captop Log Analytics Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #eee;
            min-height: 100vh;
            padding: 20px;
        }}
        h1 {{
            text-align: center;
            margin-bottom: 30px;
            background: linear-gradient(90deg, #00d4ff, #7b2ff7);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-size: 2.5rem;
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        .stat-card {{
            background: rgba(255,255,255,0.05);
            border-radius: 12px;
            padding: 20px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.1);
        }}
        .stat-card h3 {{
            font-size: 2rem;
            background: linear-gradient(90deg, #00d4ff, #7b2ff7);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .stat-card p {{
            color: #aaa;
            margin-top: 5px;
        }}
        .charts-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(450px, 1fr));
            gap: 20px;
        }}
        .chart-container {{
            background: rgba(255,255,255,0.05);
            border-radius: 12px;
            padding: 20px;
            border: 1px solid rgba(255,255,255,0.1);
        }}
        .chart-container h2 {{
            margin-bottom: 15px;
            font-size: 1.2rem;
            color: #ccc;
        }}
        canvas {{ max-height: 300px; }}
        .ip-table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }}
        .ip-table th, .ip-table td {{
            padding: 8px;
            text-align: left;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }}
        .ip-table th {{
            color: #00d4ff;
        }}
    </style>
</head>
<body>
    <h1>📊 Captop Log Analytics</h1>
    
    <div class="stats-grid">
        <div class="stat-card">
            <h3>{sum(data['ips'].values()):,}</h3>
            <p>Total Requests</p>
        </div>
        <div class="stat-card">
            <h3>{len(data['ips']):,}</h3>
            <p>Unique IPs</p>
        </div>
        <div class="stat-card">
            <h3>{max_concurrent}</h3>
            <p>Max Concurrent (per min)</p>
        </div>
        <div class="stat-card">
            <h3>{avg_response*1000:.1f}ms</h3>
            <p>Avg Response Time</p>
        </div>
    </div>
    
    <div class="charts-grid">
        <div class="chart-container">
            <h2>🕐 Hourly Traffic Distribution</h2>
            <canvas id="hourlyChart"></canvas>
        </div>
        
        <div class="chart-container">
            <h2>📅 Daily Traffic (Last 14 Days)</h2>
            <canvas id="dailyChart"></canvas>
        </div>
        
        <div class="chart-container">
            <h2>🌐 Browser Usage</h2>
            <canvas id="browserChart"></canvas>
        </div>
        
        <div class="chart-container">
            <h2>📱 Device Types</h2>
            <canvas id="deviceChart"></canvas>
        </div>
        
        <div class="chart-container">
            <h2>💻 Operating Systems</h2>
            <canvas id="osChart"></canvas>
        </div>
        
        <div class="chart-container">
            <h2>🔗 Top Endpoints</h2>
            <canvas id="endpointChart"></canvas>
        </div>
        
        <div class="chart-container">
            <h2>✅ Status Codes</h2>
            <canvas id="statusChart"></canvas>
        </div>
        
        <div class="chart-container">
            <h2>🌍 Top IP Addresses</h2>
            <table class="ip-table">
                <tr><th>IP Address</th><th>Requests</th></tr>
                {''.join(f"<tr><td>{ip}</td><td>{count}</td></tr>" for ip, count in top_ips)}
            </table>
        </div>
    </div>
    
    <script>
        const chartOptions = {{
            responsive: true,
            maintainAspectRatio: true,
            plugins: {{
                legend: {{ labels: {{ color: '#ccc' }} }}
            }},
            scales: {{
                x: {{ ticks: {{ color: '#aaa' }}, grid: {{ color: 'rgba(255,255,255,0.05)' }} }},
                y: {{ ticks: {{ color: '#aaa' }}, grid: {{ color: 'rgba(255,255,255,0.05)' }} }}
            }}
        }};
        
        // Hourly Chart
        new Chart(document.getElementById('hourlyChart'), {{
            type: 'bar',
            data: {{
                labels: {json.dumps([f'{h}:00' for h in range(24)])},
                datasets: [{{
                    label: 'Requests',
                    data: {json.dumps(hours)},
                    backgroundColor: 'rgba(0, 212, 255, 0.6)',
                    borderColor: '#00d4ff',
                    borderWidth: 1
                }}]
            }},
            options: chartOptions
        }});
        
        // Daily Chart
        new Chart(document.getElementById('dailyChart'), {{
            type: 'line',
            data: {{
                labels: {json.dumps([d[0] for d in days])},
                datasets: [{{
                    label: 'Requests',
                    data: {json.dumps([d[1] for d in days])},
                    borderColor: '#7b2ff7',
                    backgroundColor: 'rgba(123, 47, 247, 0.2)',
                    fill: true,
                    tension: 0.4
                }}]
            }},
            options: chartOptions
        }});
        
        // Browser Chart
        new Chart(document.getElementById('browserChart'), {{
            type: 'doughnut',
            data: {{
                labels: {json.dumps(list(data['browsers'].keys()))},
                datasets: [{{
                    data: {json.dumps(list(data['browsers'].values()))},
                    backgroundColor: ['#00d4ff', '#7b2ff7', '#ff6b6b', '#ffd93d', '#6bcb77']
                }}]
            }},
            options: {{ responsive: true, plugins: {{ legend: {{ labels: {{ color: '#ccc' }} }} }} }}
        }});
        
        // Device Chart
        new Chart(document.getElementById('deviceChart'), {{
            type: 'pie',
            data: {{
                labels: {json.dumps(list(data['devices'].keys()))},
                datasets: [{{
                    data: {json.dumps(list(data['devices'].values()))},
                    backgroundColor: ['#00d4ff', '#7b2ff7', '#ff6b6b']
                }}]
            }},
            options: {{ responsive: true, plugins: {{ legend: {{ labels: {{ color: '#ccc' }} }} }} }}
        }});
        
        // OS Chart
        new Chart(document.getElementById('osChart'), {{
            type: 'doughnut',
            data: {{
                labels: {json.dumps(list(data['os'].keys()))},
                datasets: [{{
                    data: {json.dumps(list(data['os'].values()))},
                    backgroundColor: ['#00d4ff', '#7b2ff7', '#ff6b6b', '#ffd93d', '#6bcb77']
                }}]
            }},
            options: {{ responsive: true, plugins: {{ legend: {{ labels: {{ color: '#ccc' }} }} }} }}
        }});
        
        // Endpoint Chart
        new Chart(document.getElementById('endpointChart'), {{
            type: 'bar',
            data: {{
                labels: {json.dumps([e[0] for e in top_endpoints])},
                datasets: [{{
                    label: 'Requests',
                    data: {json.dumps([e[1] for e in top_endpoints])},
                    backgroundColor: 'rgba(123, 47, 247, 0.6)',
                    borderColor: '#7b2ff7',
                    borderWidth: 1
                }}]
            }},
            options: {{ ...chartOptions, indexAxis: 'y' }}
        }});
        
        // Status Code Chart
        new Chart(document.getElementById('statusChart'), {{
            type: 'pie',
            data: {{
                labels: {json.dumps(list(data['status_codes'].keys()))},
                datasets: [{{
                    data: {json.dumps(list(data['status_codes'].values()))},
                    backgroundColor: ['#6bcb77', '#ffd93d', '#ff6b6b', '#7b2ff7']
                }}]
            }},
            options: {{ responsive: true, plugins: {{ legend: {{ labels: {{ color: '#ccc' }} }} }} }}
        }});
    </script>
</body>
</html>'''
    
    return html

def main():
    print("Parsing logs...")
    data = parse_logs()
    
    print("Generating dashboard...")
    html = generate_html(data)
    
    with open(OUTPUT_FILE, 'w') as f:
        f.write(html)
    
    print(f"Dashboard generated: {OUTPUT_FILE}")
    print(f"Total requests: {sum(data['ips'].values())}")
    print(f"Unique IPs: {len(data['ips'])}")

if __name__ == "__main__":
    main()
