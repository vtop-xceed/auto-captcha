#!/usr/bin/env python3
"""
IP Geolocation Script - Uses ip-api.com (free, no key required)
"""

import re
import gzip
import os
import json
import time
import urllib.request
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, 'labeler', 'logs')
OUTPUT_FILE = os.path.join(ROOT, 'data', 'ip_locations.json')

LOG_PATTERN = re.compile(
    r'(?P<proxy_ip>\S+) - - \[(?P<timestamp>[^\]]+)\] '
    r'"(?P<method>\S+) (?P<endpoint>\S+) \S+" (?P<status>\d+) \d+ '
    r'"(?P<referer>[^"]*)" "(?P<user_agent>[^"]*)" '
    r'"(?P<real_ip>[^"]*)"'
)

def get_unique_ips():
    """Extract unique IPs from logs."""
    ips = Counter()
    
    for f in os.listdir(LOG_DIR):
        if 'access.log' not in f:
            continue
        
        log_file = os.path.join(LOG_DIR, f)
        opener = gzip.open if f.endswith('.gz') else open
        
        try:
            with opener(log_file, 'rt', encoding='utf-8', errors='ignore') as file:
                for line in file:
                    match = LOG_PATTERN.match(line)
                    if match:
                        real_ip = match.group('real_ip').split(',')[0].strip()
                        # Filter out IPv6 and private IPs
                        if real_ip and not real_ip.startswith(('10.', '172.', '192.168.', '2409:', '::')):
                            ips[real_ip] += 1
        except Exception as e:
            print(f"Error reading {log_file}: {e}")
    
    return ips

def geolocate_ips(ips):
    """Get location data for IPs using ip-api.com (batch API)."""
    locations = []
    ip_list = list(ips.keys())
    
    # ip-api.com allows batch requests of up to 100 IPs
    batch_size = 100
    
    for i in range(0, len(ip_list), batch_size):
        batch = ip_list[i:i+batch_size]
        print(f"Geolocating batch {i//batch_size + 1} ({len(batch)} IPs)...")
        
        # Prepare batch request
        payload = json.dumps(batch).encode('utf-8')
        
        try:
            req = urllib.request.Request(
                'http://ip-api.com/batch?fields=status,country,regionName,city,lat,lon,query',
                data=payload,
                headers={'Content-Type': 'application/json'}
            )
            
            with urllib.request.urlopen(req, timeout=30) as response:
                results = json.loads(response.read().decode('utf-8'))
                
                for result in results:
                    if result.get('status') == 'success':
                        ip = result['query']
                        locations.append({
                            'ip': ip,
                            'count': ips[ip],
                            'city': result.get('city', 'Unknown'),
                            'region': result.get('regionName', 'Unknown'),
                            'country': result.get('country', 'Unknown'),
                            'lat': result.get('lat'),
                            'lon': result.get('lon')
                        })
            
            # Rate limiting: ip-api.com allows 45 requests/minute for free
            time.sleep(1.5)
            
        except Exception as e:
            print(f"Error in batch request: {e}")
    
    return locations

def main():
    print("Extracting unique IPs from logs...")
    ips = get_unique_ips()
    print(f"Found {len(ips)} unique IPs")
    
    print("Geolocating IPs...")
    locations = geolocate_ips(ips)
    
    # Filter for India
    india_locations = [loc for loc in locations if loc['country'] == 'India']
    other_locations = [loc for loc in locations if loc['country'] != 'India']
    
    output = {
        'india': india_locations,
        'other': other_locations,
        'total_unique_ips': len(ips),
        'india_ips': len(india_locations),
        'total_requests': sum(ips.values())
    }
    
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, indent=2)
    
    print(f"Results saved to {OUTPUT_FILE}")
    print(f"India IPs: {len(india_locations)}, Other: {len(other_locations)}")

if __name__ == "__main__":
    main()
